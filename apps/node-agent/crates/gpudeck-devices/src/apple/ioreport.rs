//! GPU power from IOReport's `Energy Model` group (no root needed). `libIOReport.dylib` is a
//! private system library, so it is loaded with `dlopen` and every failure degrades to `None`.
//!
//! Power = Δ energy of the `GPU Energy` channel between two samples / Δ t. The first call after
//! start only primes the previous sample and returns `None`.

use std::ffi::c_void;
use std::time::Instant;

use core_foundation_sys::array::{CFArrayGetCount, CFArrayGetValueAtIndex, CFArrayRef};
use core_foundation_sys::base::CFTypeRef;
use core_foundation_sys::dictionary::{
    CFDictionaryGetValue, CFDictionaryRef, CFMutableDictionaryRef,
};
use core_foundation_sys::string::CFStringRef;
use libloading::Library;

use super::cf::{cfstr, string, Owned};

type CopyChannelsInGroup =
    unsafe extern "C" fn(CFStringRef, CFStringRef, u64, u64, u64) -> CFMutableDictionaryRef;
type CreateSubscription = unsafe extern "C" fn(
    *const c_void,
    CFMutableDictionaryRef,
    *mut CFMutableDictionaryRef,
    u64,
    CFTypeRef,
) -> *const c_void;
type CreateSamples =
    unsafe extern "C" fn(*const c_void, CFMutableDictionaryRef, CFTypeRef) -> CFDictionaryRef;
type CreateSamplesDelta =
    unsafe extern "C" fn(CFDictionaryRef, CFDictionaryRef, CFTypeRef) -> CFDictionaryRef;
type ChannelGetString = unsafe extern "C" fn(CFDictionaryRef) -> CFStringRef;
type SimpleGetIntegerValue = unsafe extern "C" fn(CFDictionaryRef, i32) -> i64;

struct Api {
    _lib: Library,
    create_samples: CreateSamples,
    create_samples_delta: CreateSamplesDelta,
    get_group: ChannelGetString,
    get_channel_name: ChannelGetString,
    get_unit_label: ChannelGetString,
    get_integer: SimpleGetIntegerValue,
}

pub struct IoReport {
    api: Api,
    subscription: *const c_void,
    subscribed: CFMutableDictionaryRef,
    _channels: Owned,
    prev: Option<(Owned, Instant)>,
}

// SAFETY: the subscription and dictionaries are only touched behind the plugin's Mutex.
unsafe impl Send for IoReport {}

impl IoReport {
    pub fn new() -> Option<Self> {
        // SAFETY: loading a system library and resolving C symbols with the declared signatures
        // (as used by powermetrics-style tools); any missing symbol aborts initialisation.
        unsafe {
            let lib = Library::new("/usr/lib/libIOReport.dylib").ok()?;
            let copy_channels: CopyChannelsInGroup =
                *lib.get(b"IOReportCopyChannelsInGroup\0").ok()?;
            let create_sub: CreateSubscription = *lib.get(b"IOReportCreateSubscription\0").ok()?;
            let api = Api {
                create_samples: *lib.get(b"IOReportCreateSamples\0").ok()?,
                create_samples_delta: *lib.get(b"IOReportCreateSamplesDelta\0").ok()?,
                get_group: *lib.get(b"IOReportChannelGetGroup\0").ok()?,
                get_channel_name: *lib.get(b"IOReportChannelGetChannelName\0").ok()?,
                get_unit_label: *lib.get(b"IOReportChannelGetUnitLabel\0").ok()?,
                get_integer: *lib.get(b"IOReportSimpleGetIntegerValue\0").ok()?,
                _lib: lib,
            };
            let group = cfstr("Energy Model")?;
            let channels =
                Owned::new(
                    copy_channels(group.as_ptr() as CFStringRef, std::ptr::null(), 0, 0, 0)
                        as CFTypeRef,
                )?;
            let mut subscribed: CFMutableDictionaryRef = std::ptr::null_mut();
            let subscription = create_sub(
                std::ptr::null(),
                channels.as_ptr() as CFMutableDictionaryRef,
                &mut subscribed,
                0,
                std::ptr::null(),
            );
            if subscription.is_null() || subscribed.is_null() {
                return None;
            }
            let mut r = IoReport {
                api,
                subscription,
                subscribed,
                _channels: channels,
                prev: None,
            };
            r.prev = r.take().map(|s| (s, Instant::now()));
            Some(r)
        }
    }

    fn take(&self) -> Option<Owned> {
        // SAFETY: subscription and subscribed dictionary are valid for the lifetime of self.
        Owned::new(unsafe {
            (self.api.create_samples)(self.subscription, self.subscribed, std::ptr::null())
        } as CFTypeRef)
    }

    /// Average GPU power since the previous call, in watts.
    pub fn gpu_power_watts(&mut self) -> Option<f64> {
        let now_sample = self.take()?;
        let now = Instant::now();
        let prev = self.prev.replace((now_sample, now));
        let (prev_sample, prev_t) = prev?;
        let dt = now.duration_since(prev_t).as_secs_f64();
        if dt <= 0.0 {
            return None;
        }
        let cur = &self.prev.as_ref()?.0;
        // SAFETY: both samples are valid dictionaries produced by IOReportCreateSamples.
        let delta = Owned::new(unsafe {
            (self.api.create_samples_delta)(
                prev_sample.as_ptr() as CFDictionaryRef,
                cur.as_ptr() as CFDictionaryRef,
                std::ptr::null(),
            )
        } as CFTypeRef)?;
        self.energy_joules(&delta).map(|j| j / dt)
    }

    /// Sum of GPU energy (J) in a delta sample.
    fn energy_joules(&self, delta: &Owned) -> Option<f64> {
        let key = cfstr("IOReportChannels")?;
        // SAFETY: delta is a dictionary; the channel array and items are borrowed (+0).
        unsafe {
            let arr =
                CFDictionaryGetValue(delta.as_ptr() as CFDictionaryRef, key.as_ptr()) as CFArrayRef;
            if arr.is_null() {
                return None;
            }
            let mut exact = 0.0;
            let mut found_exact = false;
            let mut prefixed = 0.0;
            let mut found_prefixed = false;
            for i in 0..CFArrayGetCount(arr) {
                let item = CFArrayGetValueAtIndex(arr, i) as CFDictionaryRef;
                if string((self.api.get_group)(item)).as_deref() != Some("Energy Model") {
                    continue;
                }
                let Some(name) = string((self.api.get_channel_name)(item)) else {
                    continue;
                };
                let unit = string((self.api.get_unit_label)(item)).unwrap_or_default();
                let joules = (self.api.get_integer)(item, 0) as f64 * unit_to_joules(&unit);
                if name == "GPU Energy" {
                    exact += joules;
                    found_exact = true;
                } else if name.starts_with("GPU") && !name.contains("SRAM") {
                    prefixed += joules;
                    found_prefixed = true;
                }
            }
            if found_exact {
                Some(exact)
            } else if found_prefixed {
                Some(prefixed)
            } else {
                None
            }
        }
    }
}

fn unit_to_joules(unit: &str) -> f64 {
    match unit.trim() {
        "mJ" => 1e-3,
        "uJ" | "µJ" => 1e-6,
        "nJ" => 1e-9,
        "J" => 1.0,
        _ => 1e-3,
    }
}
