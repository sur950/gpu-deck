//! IOKit registry reads (`IOAccelerator` performance statistics, platform serial) and sysctl.

use std::ffi::{c_char, c_void, CString};

use core_foundation_sys::base::{CFAllocatorRef, CFTypeRef};
use core_foundation_sys::dictionary::CFMutableDictionaryRef;
use core_foundation_sys::string::CFStringRef;

use super::cf::{cfstr, string, to_json, Owned};
use super::AccelStats;

pub type MachPort = u32;
pub type IoObject = MachPort;
pub type KernReturn = i32;

/// `kIOMainPortDefault` / `MACH_PORT_NULL`: the default main port.
pub const MAIN_PORT_DEFAULT: MachPort = 0;

#[link(name = "IOKit", kind = "framework")]
extern "C" {
    pub fn IOServiceMatching(name: *const c_char) -> CFMutableDictionaryRef;
    pub fn IOServiceGetMatchingServices(
        main_port: MachPort,
        matching: CFMutableDictionaryRef,
        existing: *mut IoObject,
    ) -> KernReturn;
    pub fn IOServiceGetMatchingService(
        main_port: MachPort,
        matching: CFMutableDictionaryRef,
    ) -> IoObject;
    pub fn IOIteratorNext(iterator: IoObject) -> IoObject;
    pub fn IOObjectRelease(object: IoObject) -> KernReturn;
    pub fn IORegistryEntryCreateCFProperty(
        entry: IoObject,
        key: CFStringRef,
        allocator: CFAllocatorRef,
        options: u32,
    ) -> CFTypeRef;
    pub fn IOServiceOpen(
        service: IoObject,
        owning_task: MachPort,
        kind: u32,
        connect: *mut IoObject,
    ) -> KernReturn;
    pub fn IOServiceClose(connect: IoObject) -> KernReturn;
    pub fn IOConnectCallStructMethod(
        connection: IoObject,
        selector: u32,
        input: *const c_void,
        input_size: usize,
        output: *mut c_void,
        output_size: *mut usize,
    ) -> KernReturn;
}

extern "C" {
    /// The task's own port (`mach_task_self()` is a macro for this variable).
    pub static mach_task_self_: MachPort;
}

/// Released on drop.
pub struct IoHandle(pub IoObject);

impl Drop for IoHandle {
    fn drop(&mut self) {
        if self.0 != 0 {
            // SAFETY: handle obtained from IOKit and released exactly once.
            unsafe { IOObjectRelease(self.0) };
        }
    }
}

/// Read one registry property as JSON.
pub fn property(entry: IoObject, key: &str) -> Option<serde_json::Value> {
    let k = cfstr(key)?;
    // SAFETY: entry is a live registry entry; the returned property is +1 and wrapped in `Owned`.
    let v = Owned::new(unsafe {
        IORegistryEntryCreateCFProperty(entry, k.as_ptr() as CFStringRef, std::ptr::null(), 0)
    })?;
    Some(to_json(v.as_ptr()))
}

/// First service matching `class`.
pub fn service(class: &str) -> Option<IoHandle> {
    let c = CString::new(class).ok()?;
    // SAFETY: IOServiceMatching returns a +1 dictionary consumed by IOServiceGetMatchingService.
    let svc =
        unsafe { IOServiceGetMatchingService(MAIN_PORT_DEFAULT, IOServiceMatching(c.as_ptr())) };
    if svc == 0 {
        None
    } else {
        Some(IoHandle(svc))
    }
}

pub struct Accelerator {
    pub stats: AccelStats,
    pub gpu_cores: Option<u32>,
    pub model: Option<String>,
}

/// The first `IOAccelerator` exposing `PerformanceStatistics` (the AGX GPU on Apple Silicon).
pub fn accelerator() -> Option<Accelerator> {
    let class = CString::new("IOAccelerator").ok()?;
    let mut iter: IoObject = 0;
    // SAFETY: matching dictionary is consumed by the call; iterator released by IoHandle.
    let kr = unsafe {
        IOServiceGetMatchingServices(
            MAIN_PORT_DEFAULT,
            IOServiceMatching(class.as_ptr()),
            &mut iter,
        )
    };
    if kr != 0 || iter == 0 {
        return None;
    }
    let iter = IoHandle(iter);
    loop {
        // SAFETY: iterator is valid; each returned entry is released by IoHandle.
        let entry = unsafe { IOIteratorNext(iter.0) };
        if entry == 0 {
            return None;
        }
        let entry = IoHandle(entry);
        let Some(stats) = property(entry.0, "PerformanceStatistics") else {
            continue;
        };
        let gpu_cores = property(entry.0, "gpu-core-count")
            .and_then(|v| v.as_u64())
            .map(|n| n as u32);
        let model = property(entry.0, "model").and_then(|v| v.as_str().map(str::to_string));
        return Some(Accelerator {
            stats: AccelStats::from_json(&stats),
            gpu_cores,
            model,
        });
    }
}

/// `IOPlatformSerialNumber` from `IOPlatformExpertDevice` (readable without root).
pub fn platform_serial() -> Option<String> {
    let svc = service("IOPlatformExpertDevice")?;
    let k = cfstr("IOPlatformSerialNumber")?;
    // SAFETY: valid entry and key; result is +1 and wrapped.
    let v = Owned::new(unsafe {
        IORegistryEntryCreateCFProperty(svc.0, k.as_ptr() as CFStringRef, std::ptr::null(), 0)
    })?;
    string(v.as_ptr() as CFStringRef)
}

/// `sysctlbyname` string value.
pub fn sysctl_string(name: &str) -> Option<String> {
    let c = CString::new(name).ok()?;
    let mut len: libc::size_t = 0;
    // SAFETY: first call queries the size, second fills a buffer of that size.
    unsafe {
        if libc::sysctlbyname(
            c.as_ptr(),
            std::ptr::null_mut(),
            &mut len,
            std::ptr::null_mut(),
            0,
        ) != 0
            || len == 0
        {
            return None;
        }
        let mut buf = vec![0u8; len];
        if libc::sysctlbyname(
            c.as_ptr(),
            buf.as_mut_ptr() as *mut c_void,
            &mut len,
            std::ptr::null_mut(),
            0,
        ) != 0
        {
            return None;
        }
        buf.truncate(len);
        while buf.last() == Some(&0) {
            buf.pop();
        }
        let s = String::from_utf8_lossy(&buf).trim().to_string();
        if s.is_empty() {
            None
        } else {
            Some(s)
        }
    }
}

/// `sysctlbyname` integer value (4- or 8-byte).
pub fn sysctl_u64(name: &str) -> Option<u64> {
    let c = CString::new(name).ok()?;
    let mut buf = [0u8; 8];
    let mut len: libc::size_t = buf.len();
    // SAFETY: buffer is 8 bytes and len reflects it.
    let rc = unsafe {
        libc::sysctlbyname(
            c.as_ptr(),
            buf.as_mut_ptr() as *mut c_void,
            &mut len,
            std::ptr::null_mut(),
            0,
        )
    };
    if rc != 0 {
        return None;
    }
    match len {
        4 => Some(u32::from_ne_bytes([buf[0], buf[1], buf[2], buf[3]]) as u64),
        8 => Some(u64::from_ne_bytes(buf)),
        _ => None,
    }
}
