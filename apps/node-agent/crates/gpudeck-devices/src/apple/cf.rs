//! Minimal CoreFoundation helpers: owned references, CFString conversion and a property-list →
//! `serde_json::Value` converter for IOKit registry properties.

use std::ffi::{c_void, CStr, CString};

use core_foundation_sys::array::{
    CFArrayGetCount, CFArrayGetTypeID, CFArrayGetValueAtIndex, CFArrayRef,
};
use core_foundation_sys::base::{CFGetTypeID, CFRelease, CFTypeRef};
use core_foundation_sys::dictionary::{
    CFDictionaryGetCount, CFDictionaryGetKeysAndValues, CFDictionaryGetTypeID, CFDictionaryRef,
};
use core_foundation_sys::number::{
    kCFNumberFloat64Type, kCFNumberSInt64Type, CFBooleanGetTypeID, CFBooleanGetValue, CFBooleanRef,
    CFNumberGetTypeID, CFNumberGetValue, CFNumberIsFloatType, CFNumberRef,
};
use core_foundation_sys::string::{
    kCFStringEncodingUTF8, CFStringCreateWithCString, CFStringGetCString, CFStringGetTypeID,
    CFStringRef,
};
use serde_json::Value;

/// An owned (+1) CF reference released on drop.
pub struct Owned(pub CFTypeRef);

impl Owned {
    /// Wrap a reference obtained from a Create/Copy function; `None` for NULL.
    pub fn new(r: CFTypeRef) -> Option<Self> {
        if r.is_null() {
            None
        } else {
            Some(Owned(r))
        }
    }

    pub fn as_ptr(&self) -> CFTypeRef {
        self.0
    }
}

impl Drop for Owned {
    fn drop(&mut self) {
        // SAFETY: `Owned` is only constructed from +1 references.
        unsafe { CFRelease(self.0) }
    }
}

// SAFETY: CF objects used here are immutable property lists / strings; CF reference counting is
// thread-safe.
unsafe impl Send for Owned {}

/// Create a CFString (+1).
pub fn cfstr(s: &str) -> Option<Owned> {
    let c = CString::new(s).ok()?;
    // SAFETY: valid NUL-terminated UTF-8 string.
    Owned::new(unsafe {
        CFStringCreateWithCString(std::ptr::null(), c.as_ptr(), kCFStringEncodingUTF8)
    } as CFTypeRef)
}

/// CFString → Rust string (`None` for NULL / non-string).
pub fn string(r: CFStringRef) -> Option<String> {
    if r.is_null() {
        return None;
    }
    // SAFETY: r is a valid CF object; type is checked before use.
    unsafe {
        if CFGetTypeID(r as CFTypeRef) != CFStringGetTypeID() {
            return None;
        }
        let mut buf = vec![0i8; 1024];
        if CFStringGetCString(r, buf.as_mut_ptr(), buf.len() as _, kCFStringEncodingUTF8) == 0 {
            return None;
        }
        Some(CStr::from_ptr(buf.as_ptr()).to_string_lossy().into_owned())
    }
}

/// Convert a CF property list (dictionary/array/string/number/boolean) into JSON. Unsupported
/// types (CFData, CFDate) become `null`.
pub fn to_json(r: CFTypeRef) -> Value {
    if r.is_null() {
        return Value::Null;
    }
    // SAFETY: r is a valid CF object; each branch checks the type id before casting.
    unsafe {
        let t = CFGetTypeID(r);
        if t == CFDictionaryGetTypeID() {
            let d = r as CFDictionaryRef;
            let n = CFDictionaryGetCount(d) as usize;
            let mut keys = vec![std::ptr::null::<c_void>(); n];
            let mut vals = vec![std::ptr::null::<c_void>(); n];
            CFDictionaryGetKeysAndValues(d, keys.as_mut_ptr(), vals.as_mut_ptr());
            let mut m = serde_json::Map::new();
            for (k, v) in keys.into_iter().zip(vals) {
                if let Some(ks) = string(k as CFStringRef) {
                    m.insert(ks, to_json(v));
                }
            }
            Value::Object(m)
        } else if t == CFArrayGetTypeID() {
            let a = r as CFArrayRef;
            let n = CFArrayGetCount(a);
            Value::Array(
                (0..n)
                    .map(|i| to_json(CFArrayGetValueAtIndex(a, i)))
                    .collect(),
            )
        } else if t == CFStringGetTypeID() {
            string(r as CFStringRef)
                .map(Value::String)
                .unwrap_or(Value::Null)
        } else if t == CFNumberGetTypeID() {
            let num = r as CFNumberRef;
            if CFNumberIsFloatType(num) != 0 {
                let mut f: f64 = 0.0;
                CFNumberGetValue(num, kCFNumberFloat64Type, &mut f as *mut f64 as *mut c_void);
                serde_json::Number::from_f64(f)
                    .map(Value::Number)
                    .unwrap_or(Value::Null)
            } else {
                let mut i: i64 = 0;
                CFNumberGetValue(num, kCFNumberSInt64Type, &mut i as *mut i64 as *mut c_void);
                Value::from(i)
            }
        } else if t == CFBooleanGetTypeID() {
            Value::Bool(CFBooleanGetValue(r as CFBooleanRef))
        } else {
            Value::Null
        }
    }
}
