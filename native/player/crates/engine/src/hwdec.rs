//! Hardware decoding: FFmpeg decodes on the GPU.
//!
//! Decoding 4K HEVC in software pins every core of most machines; the GPU's
//! decoder does it for next to nothing. Two forms, chosen per file:
//!
//! - **On the shared device** (see `shared_device.rs`): the decoder writes
//!   into Vulkan images on the device the renderer and the scene graph use,
//!   and the frames stay there -- libplacebo samples them where they lie.
//! - **On VideoToolbox's surfaces** (macOS): each frame is a `CVPixelBuffer`
//!   the Metal renderer samples as it is (see `metal/planes.rs`).
//! - **Copied back** from any other device (CUDA, VA-API, a Vulkan device of
//!   FFmpeg's own, D3D11VA): each decoded surface is
//!   transferred to system memory (`av_hwframe_transfer_data`) and rendered
//!   like any other frame.
//!
//! A frame that stays on the GPU still reaches memory when a path needs it
//! there (swscale, the readback renderer on a device of its own): see
//! `copy_to_memory`.
//!
//! Devices are tried in a per-platform order and created once per player: a
//! CUDA or Vulkan context costs a noticeable fraction of a second to make. A
//! device that cannot be created, a codec none of them decodes, or a hwaccel
//! FFmpeg fails to start all end the same way -- software decoding, which is
//! what the engine did before.

use std::cell::Cell;
use std::collections::HashMap;
use std::ffi::{CStr, CString, c_char, c_int, c_void};
use std::ptr;
use std::sync::Arc;

use ffmpeg_next::{codec, ffi, frame};
use parking_lot::Mutex;

/// Which hardware decoders the player may use.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum HardwareDecoding {
    /// The platform's usual order (see `AUTO`).
    #[default]
    Auto,
    /// Software decoding only.
    Off,
    /// This FFmpeg device type only ("cuda", "vaapi", "vulkan", ...).
    Only(String),
}

impl HardwareDecoding {
    /// "auto", "no" or an FFmpeg device type name.
    #[must_use]
    pub fn from_name(name: &str) -> Self {
        match name.trim() {
            "" | "auto" => Self::Auto,
            "no" | "none" | "off" => Self::Off,
            other => Self::Only(other.to_owned()),
        }
    }
}

/// The order devices are tried in. NVDEC first on Linux: on NVIDIA it is the
/// driver's own decoder, VA-API there goes through a translation layer, and
/// creating a CUDA device fails at once on every other GPU.
#[cfg(target_os = "linux")]
const AUTO: &[&str] = &["cuda", "vaapi", "vulkan"];
#[cfg(target_os = "macos")]
const AUTO: &[&str] = &["videotoolbox"];
#[cfg(target_os = "windows")]
const AUTO: &[&str] = &["d3d11va", "cuda", "vulkan"];
#[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
const AUTO: &[&str] = &[];

/// FFmpeg's device type for Vulkan, which the shared device is.
const VULKAN: &str = "vulkan";

/// The `VkVideoCodecOperationFlagBitsKHR` a codec decodes with, for the
/// codecs Vulkan Video has.
fn vulkan_operation(codec: ffi::AVCodecID) -> Option<u32> {
    match codec {
        ffi::AVCodecID::AV_CODEC_ID_H264 => Some(0x1),
        ffi::AVCodecID::AV_CODEC_ID_HEVC => Some(0x2),
        ffi::AVCodecID::AV_CODEC_ID_AV1 => Some(0x4),
        ffi::AVCodecID::AV_CODEC_ID_VP9 => Some(0x8),
        _ => None,
    }
}

/// A hardware device context (an `AVBufferRef`).
#[derive(Debug)]
pub(crate) struct Device {
    name: String,
    kind: ffi::AVHWDeviceType,
    buffer: *mut ffi::AVBufferRef,
}

// SAFETY: an AVHWDeviceContext is reference counted and meant to be shared
// between codec contexts on any thread; this only ever takes new references.
unsafe impl Send for Device {}
unsafe impl Sync for Device {}

#[cfg(not(target_os = "macos"))]
impl Device {
    /// Takes over `buffer`, a reference to an initialised Vulkan
    /// `AVHWDeviceContext`.
    ///
    /// # Safety
    /// `buffer` must be such a reference, owned by the caller.
    pub(crate) unsafe fn vulkan(buffer: *mut ffi::AVBufferRef) -> Self {
        Self {
            name: VULKAN.to_owned(),
            kind: ffi::AVHWDeviceType::AV_HWDEVICE_TYPE_VULKAN,
            buffer,
        }
    }
}

impl Drop for Device {
    fn drop(&mut self) {
        // SAFETY: the reference was created by av_hwdevice_ctx_create and is
        // released once, here.
        unsafe { ffi::av_buffer_unref(&raw mut self.buffer) };
    }
}

/// The shared device's decoder, and the codec operations its decode queues
/// offer.
#[derive(Debug)]
struct Shared {
    device: Arc<Device>,
    operations: u32,
}

/// What a decoder was set up with.
#[derive(Clone, Debug)]
pub(crate) struct Attached {
    /// The FFmpeg device type ("cuda", "vulkan", ...).
    pub(crate) name: String,
    /// The frames stay in the shared device's memory rather than being
    /// copied back.
    pub(crate) on_gpu: bool,
}

/// The player's hardware devices, created on first use.
#[derive(Debug, Default)]
pub(crate) struct HwDecoders {
    mode: Mutex<HardwareDecoding>,
    /// None records a device that could not be created, so it is not tried
    /// again for every file.
    devices: Mutex<HashMap<String, Option<Arc<Device>>>>,
    shared: Mutex<Option<Shared>>,
}

impl HwDecoders {
    pub(crate) fn set_mode(&self, mode: HardwareDecoding) {
        *self.mode.lock() = mode;
    }

    /// Decodes into the shared device, first, whenever its decode queues
    /// take the codec. `operations` is what they offer.
    #[cfg(not(target_os = "macos"))]
    pub(crate) fn set_shared(&self, device: Arc<Device>, operations: u32) {
        *self.shared.lock() = Some(Shared { device, operations });
    }

    /// The devices to try, in order.
    fn names(&self) -> Vec<String> {
        match &*self.mode.lock() {
            HardwareDecoding::Off => Vec::new(),
            HardwareDecoding::Auto => AUTO.iter().map(|&n| n.to_owned()).collect(),
            HardwareDecoding::Only(name) => vec![name.clone()],
        }
    }

    /// Creates the first device that can be created, ahead of `attach`.
    /// Called as a file starts opening, so a device (CUDA's took ~150 ms)
    /// is made while the demuxer reads the header rather than after it.
    pub(crate) fn warm(&self) {
        // With a shared device, most files never need another one: the rest
        // (codecs Vulkan Video lacks) make theirs when they come.
        if self.shared.lock().is_some() {
            return;
        }
        for name in self.names() {
            if self.device(&name).is_some() {
                return;
            }
        }
    }

    /// Sets `context` up to decode on the first device that can decode its
    /// codec, before the decoder is opened: the shared device when its queues
    /// take the codec, then the copy-back devices in order. None for software
    /// decoding.
    ///
    /// `frames_held` is how many decoded frames the player may hold at once:
    /// frames that stay on the GPU come out of the decoder's own pool, which
    /// must be that much bigger.
    pub(crate) fn attach(
        &self,
        context: &mut codec::Context,
        frames_held: usize,
    ) -> Option<Attached> {
        let names = self.names();
        if names.is_empty() {
            return None;
        }
        // SAFETY: the context is not open yet; its codec is set by
        // from_parameters. Setting hw_device_ctx, get_format and opaque before
        // avcodec_open2 is FFmpeg's documented way to enable a hwaccel.
        unsafe {
            let raw = context.as_mut_ptr();
            let codec = ffi::avcodec_find_decoder((*raw).codec_id);
            if codec.is_null() {
                return None;
            }
            let shared = self.shared.lock();
            if let Some(shared) = shared.as_ref()
                && names.iter().any(|n| n == VULKAN)
                && vulkan_operation((*raw).codec_id).is_some_and(|op| shared.operations & op != 0)
                && let Some(format) = hw_format(codec, shared.device.kind)
            {
                use_device(raw, &shared.device, format);
                (*raw).extra_hw_frames = i32::try_from(frames_held).unwrap_or(i32::MAX);
                return Some(Attached {
                    name: VULKAN.to_owned(),
                    on_gpu: true,
                });
            }
            let skip_vulkan = shared.is_some();
            drop(shared);
            for name in names {
                // With a shared device, a codec its queues cannot take is not
                // one a Vulkan device of FFmpeg's own could take either.
                if skip_vulkan && name == VULKAN {
                    continue;
                }
                let Some(device) = self.device(&name) else {
                    continue;
                };
                let Some(format) = hw_format(codec, device.kind) else {
                    continue;
                };
                use_device(raw, &device, format);
                return Some(Attached {
                    name: device.name.clone(),
                    on_gpu: stays_on_gpu(&device),
                });
            }
        }
        None
    }

    fn device(&self, name: &str) -> Option<Arc<Device>> {
        let mut devices = self.devices.lock();
        if let Some(known) = devices.get(name) {
            return known.clone();
        }
        let created = create(name);
        match &created {
            Some(_) => log::info!("hardware decoding available through {name}"),
            None => log::debug!("no {name} device"),
        }
        let created = created.map(Arc::new);
        devices.insert(name.to_owned(), created.clone());
        created
    }
}

/// Whether frames from `device` are rendered where they lie. VideoToolbox's
/// are `CVPixelBuffer`s on IOSurfaces, which the Metal renderer samples as
/// textures (see `metal/planes.rs`); every other device here is copied back.
fn stays_on_gpu(device: &Device) -> bool {
    cfg!(target_os = "macos") && device.kind == ffi::AVHWDeviceType::AV_HWDEVICE_TYPE_VIDEOTOOLBOX
}

/// Points an unopened codec context at `device`, decoding to `format`.
///
/// # Safety
/// `context` must be a valid codec context not yet opened.
unsafe fn use_device(
    context: *mut ffi::AVCodecContext,
    device: &Device,
    format: ffi::AVPixelFormat,
) {
    // SAFETY: per the caller; the context takes its own reference.
    unsafe {
        (*context).hw_device_ctx = ffi::av_buffer_ref(device.buffer);
        (*context).opaque = format as i32 as isize as *mut c_void;
        (*context).get_format = Some(pick_format);
    }
}

/// FFmpeg's log callback with its `va_list` declared as what it is at the ABI
/// level on every platform FFmpeg builds for: a pointer. The bindings spell it
/// per platform (a pointer to `__va_list_tag`, a char pointer, or the 32-byte
/// struct of `AArch64`, which its procedure call standard passes by
/// reference), and the callback never reads it, only hands it to FFmpeg's own.
type Declared = unsafe extern "C" fn(*mut c_void, c_int, *const c_char, *mut c_void);

thread_local! {
    /// Set while this thread asks FFmpeg for a hardware device.
    static PROBING: Cell<bool> = const { Cell::new(false) };
}

/// FFmpeg's log callback, as the engine installs it: everything goes to
/// FFmpeg's own default callback exactly as before, except what a thread
/// says while it asks for a hardware device. Devices are tried in order and
/// most machines lack one of them, so that is not an error to the viewer --
/// `Cannot load nvcuda.dll` on every AMD or Intel machine, the Vulkan
/// loader's `VK_ERROR_INCOMPATIBLE_DRIVER` without a driver -- and it goes to
/// the log at debug instead. Device creation is synchronous, so the thread
/// that asked is the thread that logs.
unsafe extern "C" fn log_callback(
    context: *mut c_void,
    level: c_int,
    format: *const c_char,
    args: *mut c_void,
) {
    if PROBING.get() {
        // SAFETY: plain FFmpeg call.
        if level <= unsafe { ffi::av_log_get_level() } && !format.is_null() {
            // The format, not the message: expanding a va_list is not
            // possible from stable Rust, and the format names the failure.
            // SAFETY: FFmpeg passes a valid NUL-terminated format.
            let format = unsafe { CStr::from_ptr(format) }.to_string_lossy();
            log::debug!("FFmpeg, trying a hardware device: {}", format.trim_end());
        }
        return;
    }
    // SAFETY: av_log_default_callback under `Declared`; `args` is handed on
    // untouched.
    #[allow(
        clippy::missing_transmute_annotations,
        reason = "the bound type differs per platform; see Declared"
    )]
    unsafe {
        let default = std::mem::transmute::<_, Declared>(
            ffi::av_log_default_callback as unsafe extern "C" fn(_, _, _, _),
        );
        default(context, level, format, args);
    }
}

/// Installs `log_callback` for the process.
pub(crate) fn install_log_callback() {
    let callback: Declared = log_callback;
    // SAFETY: the same function under the bindings' spelling of `Declared`.
    #[allow(
        clippy::missing_transmute_annotations,
        reason = "the bound type differs per platform; see Declared"
    )]
    unsafe {
        ffi::av_log_set_callback(std::mem::transmute::<Option<Declared>, _>(Some(callback)));
    }
}

fn create(name: &str) -> Option<Device> {
    let c_name = CString::new(name).ok()?;
    // SAFETY: plain FFmpeg calls; the buffer is owned by the Device made
    // from it, or null on failure.
    unsafe {
        let kind = ffi::av_hwdevice_find_type_by_name(c_name.as_ptr());
        if kind == ffi::AVHWDeviceType::AV_HWDEVICE_TYPE_NONE {
            return None;
        }
        let mut buffer = ptr::null_mut();
        PROBING.set(true);
        let created =
            ffi::av_hwdevice_ctx_create(&raw mut buffer, kind, ptr::null(), ptr::null_mut(), 0);
        PROBING.set(false);
        if created < 0 {
            log::debug!("no {name} device for hardware decoding");
            return None;
        }
        Some(Device {
            name: name.to_owned(),
            kind,
            buffer,
        })
    }
}

/// The pixel format `codec` decodes to on a device of `kind`, if it can.
///
/// # Safety
/// `codec` must be a valid decoder.
unsafe fn hw_format(
    codec: *const ffi::AVCodec,
    kind: ffi::AVHWDeviceType,
) -> Option<ffi::AVPixelFormat> {
    let method = ffi::AV_CODEC_HW_CONFIG_METHOD_HW_DEVICE_CTX as i32;
    for index in 0.. {
        // SAFETY: indices past the last config return null.
        let config = unsafe { ffi::avcodec_get_hw_config(codec, index) };
        if config.is_null() {
            return None;
        }
        // SAFETY: a config FFmpeg returned.
        let config = unsafe { &*config };
        if config.device_type == kind && config.methods & method != 0 {
            return Some(config.pix_fmt);
        }
    }
    None
}

/// FFmpeg's `get_format`: the hardware format this context was set up for when
/// it is offered, else the first software one -- which is how a stream the
/// hardware cannot take (a profile it does not support) keeps decoding.
unsafe extern "C" fn pick_format(
    context: *mut ffi::AVCodecContext,
    formats: *const ffi::AVPixelFormat,
) -> ffi::AVPixelFormat {
    // SAFETY: FFmpeg passes its context and a list ending in AV_PIX_FMT_NONE.
    unsafe {
        let wanted = (*context).opaque as isize as i32;
        let mut software = ffi::AVPixelFormat::AV_PIX_FMT_NONE;
        let mut cursor = formats;
        while *cursor != ffi::AVPixelFormat::AV_PIX_FMT_NONE {
            let format = *cursor;
            if format as i32 == wanted {
                return format;
            }
            if software == ffi::AVPixelFormat::AV_PIX_FMT_NONE && !is_hardware(format) {
                software = format;
            }
            cursor = cursor.add(1);
        }
        if software != ffi::AVPixelFormat::AV_PIX_FMT_NONE {
            log::info!("the hardware decoder cannot take this stream; decoding in software");
        }
        software
    }
}

fn is_hardware(format: ffi::AVPixelFormat) -> bool {
    // SAFETY: av_pix_fmt_desc_get returns a static descriptor or null.
    let descriptor = unsafe { ffi::av_pix_fmt_desc_get(format) };
    // SAFETY: non-null descriptors are static.
    !descriptor.is_null()
        && unsafe { (*descriptor).flags } & ffi::AV_PIX_FMT_FLAG_HWACCEL as u64 != 0
}

/// Whether `decoded` is a hardware surface rather than a picture in memory.
pub(crate) fn on_hardware(decoded: &frame::Video) -> bool {
    // SAFETY: a valid decoded video frame; its format field holds an
    // AVPixelFormat.
    let format = unsafe { (*decoded.as_ptr()).format };
    // SAFETY: as above.
    is_hardware(unsafe { std::mem::transmute::<i32, ffi::AVPixelFormat>(format) })
}

/// `decoded` in system memory: the frame itself for a software frame, a
/// transferred copy for a hardware one (see `copy_to_memory`).
pub(crate) fn to_memory(decoded: frame::Video) -> Option<frame::Video> {
    if on_hardware(&decoded) {
        copy_to_memory(&decoded)
    } else {
        Some(decoded)
    }
}

/// A hardware frame's picture in system memory, with its timestamps, colour
/// tags and side data (Dolby Vision metadata included). None when the
/// transfer fails.
pub(crate) fn copy_to_memory(decoded: &frame::Video) -> Option<frame::Video> {
    let mut memory = frame::Video::empty();
    // SAFETY: both frames are valid; the destination is allocated by the
    // transfer in the surface's software format.
    unsafe {
        if ffi::av_hwframe_transfer_data(memory.as_mut_ptr(), decoded.as_ptr(), 0) < 0 {
            return None;
        }
        if ffi::av_frame_copy_props(memory.as_mut_ptr(), decoded.as_ptr()) < 0 {
            return None;
        }
    }
    Some(memory)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The callback hands FFmpeg's arguments on untouched: a formatted
    /// message through the default callback (to stderr, as FFmpeg would
    /// print it), and nothing -- no crash either -- while a device is probed.
    /// A `va_list` handed on wrongly crashes here, or prints garbage.
    #[test]
    fn ffmpeg_messages_pass_through_and_device_probes_stay_quiet() {
        install_log_callback();
        let error = ffi::AV_LOG_ERROR as c_int;
        // SAFETY: each format names exactly the arguments that follow it.
        unsafe {
            ffi::av_log(
                ptr::null_mut(),
                error,
                c"hwdec test: passed through, %d %s\n".as_ptr(),
                42 as c_int,
                c"intact".as_ptr(),
            );
            PROBING.set(true);
            ffi::av_log(
                ptr::null_mut(),
                error,
                c"hwdec test: never printed %d\n".as_ptr(),
                7 as c_int,
            );
            PROBING.set(false);
        }
    }
}
