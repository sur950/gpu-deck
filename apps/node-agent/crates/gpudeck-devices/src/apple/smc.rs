//! GPU temperature from the System Management Controller (`AppleSMC` user client, no root).
//!
//! At first use the key table is enumerated once and every `Tg*` key of type `flt ` is kept; each
//! sample reads those keys and reports the mean of plausible values (1-150 °C). Key names differ
//! per chip (`Tg0f`, `Tg05`, `Tg0D`, `Tg0L`...), hence the enumeration. Best effort: any failure
//! yields `None`.

use std::ffi::c_void;

use super::iokit::{
    mach_task_self_, service, IOConnectCallStructMethod, IOServiceClose, IOServiceOpen, IoObject,
};

const KERNEL_INDEX_SMC: u32 = 2;
const CMD_READ_BYTES: u8 = 5;
const CMD_READ_INDEX: u8 = 8;
const CMD_READ_KEYINFO: u8 = 9;

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct KeyDataVer {
    major: u8,
    minor: u8,
    build: u8,
    reserved: u8,
    release: u16,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct PLimitData {
    version: u16,
    length: u16,
    cpu_p_limit: u32,
    gpu_p_limit: u32,
    mem_p_limit: u32,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct KeyInfo {
    data_size: u32,
    data_type: u32,
    data_attributes: u8,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct KeyData {
    key: u32,
    vers: KeyDataVer,
    p_limit_data: PLimitData,
    key_info: KeyInfo,
    result: u8,
    status: u8,
    data8: u8,
    data32: u32,
    bytes: [u8; 32],
}

const _: () = assert!(std::mem::size_of::<KeyData>() == 80);

fn fourcc(s: &[u8; 4]) -> u32 {
    u32::from_be_bytes(*s)
}

fn fourcc_str(v: u32) -> String {
    String::from_utf8_lossy(&v.to_be_bytes()).into_owned()
}

pub struct Smc {
    conn: IoObject,
    gpu_keys: Option<Vec<u32>>,
}

// SAFETY: the connection is a mach port used only behind the plugin's Mutex.
unsafe impl Send for Smc {}

impl Smc {
    pub fn open() -> Option<Self> {
        let svc = service("AppleSMC")?;
        let mut conn: IoObject = 0;
        // SAFETY: valid service handle; conn receives a new connection closed on drop.
        let kr = unsafe { IOServiceOpen(svc.0, mach_task_self_, 0, &mut conn) };
        if kr != 0 || conn == 0 {
            return None;
        }
        Some(Smc {
            conn,
            gpu_keys: None,
        })
    }

    fn call(&self, input: &KeyData) -> Option<KeyData> {
        let mut out = KeyData::default();
        let mut out_size = std::mem::size_of::<KeyData>();
        // SAFETY: input/output are 80-byte repr(C) structs matching the SMC ABI.
        let kr = unsafe {
            IOConnectCallStructMethod(
                self.conn,
                KERNEL_INDEX_SMC,
                input as *const KeyData as *const c_void,
                std::mem::size_of::<KeyData>(),
                &mut out as *mut KeyData as *mut c_void,
                &mut out_size,
            )
        };
        if kr != 0 || out.result != 0 {
            None
        } else {
            Some(out)
        }
    }

    fn key_info(&self, key: u32) -> Option<KeyInfo> {
        let input = KeyData {
            key,
            data8: CMD_READ_KEYINFO,
            ..Default::default()
        };
        self.call(&input).map(|o| o.key_info)
    }

    fn read(&self, key: u32) -> Option<(KeyInfo, [u8; 32])> {
        let info = self.key_info(key)?;
        let input = KeyData {
            key,
            key_info: info,
            data8: CMD_READ_BYTES,
            ..Default::default()
        };
        self.call(&input).map(|o| (info, o.bytes))
    }

    fn key_at(&self, index: u32) -> Option<u32> {
        let input = KeyData {
            data8: CMD_READ_INDEX,
            data32: index,
            ..Default::default()
        };
        self.call(&input).map(|o| o.key)
    }

    fn discover_gpu_keys(&self) -> Vec<u32> {
        let Some((_, bytes)) = self.read(fourcc(b"#KEY")) else {
            return Vec::new();
        };
        let count = u32::from_be_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]).min(10_000);
        let flt = fourcc(b"flt ");
        (0..count)
            .filter_map(|i| self.key_at(i))
            .filter(|k| fourcc_str(*k).starts_with("Tg"))
            .filter(|k| {
                self.key_info(*k)
                    .is_some_and(|ki| ki.data_type == flt && ki.data_size == 4)
            })
            .collect()
    }

    /// Mean of plausible `Tg*` sensor readings.
    pub fn gpu_temperature_c(&mut self) -> Option<f64> {
        if self.gpu_keys.is_none() {
            self.gpu_keys = Some(self.discover_gpu_keys());
        }
        let keys = self.gpu_keys.as_ref()?;
        let vals: Vec<f64> = keys
            .iter()
            .filter_map(|k| self.read(*k))
            .map(|(_, b)| f32::from_le_bytes([b[0], b[1], b[2], b[3]]) as f64)
            .filter(|t| *t > 1.0 && *t < 150.0)
            .collect();
        if vals.is_empty() {
            None
        } else {
            Some((vals.iter().sum::<f64>() / vals.len() as f64 * 10.0).round() / 10.0)
        }
    }
}

impl Drop for Smc {
    fn drop(&mut self) {
        // SAFETY: connection opened in `open` and closed once.
        unsafe { IOServiceClose(self.conn) };
    }
}
