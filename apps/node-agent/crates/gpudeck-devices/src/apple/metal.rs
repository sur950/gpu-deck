//! Metal device facts via `objc2-metal`.

use objc2_metal::{MTLCreateSystemDefaultDevice, MTLDevice, MTLGPUFamily};

pub struct MetalInfo {
    pub name: String,
    /// Wired-memory budget the GPU may use (`recommendedMaxWorkingSetSize`).
    pub recommended_max_working_set: u64,
    /// Highest supported `MTLGPUFamilyMetalN` (3 or 4).
    pub metal_family: u8,
}

pub fn default_device_info() -> Option<MetalInfo> {
    let dev = MTLCreateSystemDefaultDevice()?;
    let name = dev.name().to_string();
    let recommended_max_working_set = dev.recommendedMaxWorkingSetSize();
    let metal_family = if dev.supportsFamily(MTLGPUFamily::Metal4) {
        4
    } else {
        3
    };
    Some(MetalInfo {
        name,
        recommended_max_working_set,
        metal_family,
    })
}
