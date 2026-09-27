//! One Vulkan device for the embedder's scene graph, libplacebo and FFmpeg's
//! decoder, so decoded frames are sampled where the decoder wrote them.
//!
//! The embedder's own device cannot be used for this: Qt creates its device
//! with a graphics queue and nothing else, and decoding needs a video decode
//! queue that has to exist from device creation. So the device is made here
//! (by libplacebo, on the embedder's `VkInstance`), and the embedder adopts
//! it before its scene graph starts. What that promises the embedder, and
//! which queues the decoder may use, is in `libplacebo-sys/src/shared_device.c`.
//!
//! The device is never destroyed. The embedder's scene graph holds it for as
//! long as its window exists, which ends at a moment no one here chooses, and
//! destroying it before would pull it out from under Qt.

use std::ffi::{CStr, c_char};
use std::sync::Arc;

use libplacebo_sys as pl;

use crate::error::{Error, Result};
use crate::gpu::{self, VulkanDevice};
use crate::hwdec;

/// The highest Vulkan version the device is used at (see
/// `gravitas_native_vk.cpp`: Qt's feature chain stops at 1.3).
const API_VERSION_1_3: u32 = (1 << 22) | (3 << 12);

/// The shared device, and FFmpeg's view of it for decoding.
pub struct SharedDevice {
    vulkan: pl::pl_vulkan,
    decoder: Option<Arc<hwdec::Device>>,
    /// The `VkVideoCodecOperationFlagsKHR` its decode queues offer.
    operations: u32,
}

// SAFETY: nothing here is mutated after creation; the handles are plain
// Vulkan handles, usable from any thread, and libplacebo's `pl_vulkan` is
// read-only once created (its queue locks are its own).
unsafe impl Send for SharedDevice {}
unsafe impl Sync for SharedDevice {}

impl std::fmt::Debug for SharedDevice {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SharedDevice")
            .field("decodes", &self.decoder.is_some())
            .field("operations", &self.operations)
            .finish_non_exhaustive()
    }
}

impl SharedDevice {
    /// Creates the device on `instance`, for the rest of the process.
    ///
    /// # Safety
    /// `instance` must be a live `VkInstance` created at API 1.3 or newer,
    /// and `get_instance_proc_addr` its `vkGetInstanceProcAddr`; the instance
    /// must never be destroyed, since the device never is.
    ///
    /// # Errors
    /// When libplacebo cannot create a device on that instance.
    pub unsafe fn create(instance: u64, get_instance_proc_addr: u64) -> Result<&'static Self> {
        let log = gpu::log_create();
        // SAFETY: a PFN_vkGetInstanceProcAddr, per the caller.
        let get_proc_addr = unsafe {
            std::mem::transmute::<u64, pl::PFN_vkGetInstanceProcAddr>(get_instance_proc_addr)
        };
        // SAFETY: a live instance, per the caller; the result is checked.
        let vulkan =
            unsafe { pl::gv_shared_device_create(log, instance as pl::VkInstance, get_proc_addr) };
        if vulkan.is_null() {
            return Err(Error::Gpu(
                "libplacebo could not create a Vulkan device for the scene graph".to_owned(),
            ));
        }
        let mut why = [0 as c_char; 256];
        // SAFETY: a live pl_vulkan; `why` is a NUL-terminated buffer of the
        // size given.
        let buffer = unsafe { pl::gv_shared_device_decoder(vulkan, why.as_mut_ptr(), why.len()) };
        // SAFETY: as above.
        let operations = unsafe { pl::gv_shared_device_decode_operations(vulkan) };
        let decoder = if buffer.is_null() {
            // SAFETY: filled NUL-terminated by the C side on failure.
            let why = unsafe { CStr::from_ptr(why.as_ptr()) }.to_string_lossy();
            log::info!("the scene graph's Vulkan device is up; no decoding into it: {why}");
            None
        } else {
            log::info!(
                "the scene graph's Vulkan device decodes {} in place",
                describe(operations)
            );
            // SAFETY: an initialised Vulkan device context, handed over.
            Some(Arc::new(unsafe { hwdec::Device::vulkan(buffer) }))
        };
        Ok(Box::leak(Box::new(Self {
            vulkan,
            decoder,
            operations,
        })))
    }

    /// The device as the embedder adopts it, and as `Player::attach_vulkan`
    /// takes it: its graphics queue, queue 0 of its family.
    #[must_use]
    pub fn device(&self) -> VulkanDevice {
        // SAFETY: a live pl_vulkan, never destroyed.
        let vk = unsafe { &*self.vulkan };
        VulkanDevice {
            instance: vk.instance as u64,
            get_instance_proc_addr: vk.get_proc_addr.map_or(0, |f| f as usize as u64),
            physical_device: vk.phys_device as u64,
            device: vk.device as u64,
            queue_family: vk.queue_graphics.index,
            queue_index: 0,
            api_version: vk.api_version.min(API_VERSION_1_3),
            features: vk.features as u64,
        }
    }

    /// Whether the device decodes video at all.
    #[must_use]
    pub fn decodes(&self) -> bool {
        self.decoder.is_some()
    }

    pub(crate) fn decoder(&self) -> Option<(Arc<hwdec::Device>, u32)> {
        self.decoder.clone().map(|d| (d, self.operations))
    }
}

/// The codecs `operations` names, for the log.
fn describe(operations: u32) -> String {
    let names: Vec<&str> = [(0x1, "H.264"), (0x2, "HEVC"), (0x4, "AV1"), (0x8, "VP9")]
        .into_iter()
        .filter(|(bit, _)| operations & bit != 0)
        .map(|(_, name)| name)
        .collect();
    if names.is_empty() {
        "nothing".to_owned()
    } else {
        names.join(", ")
    }
}
