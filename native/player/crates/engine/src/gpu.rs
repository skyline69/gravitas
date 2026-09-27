//! Rendering on the GPU with libplacebo: the step from "a decoded frame" to
//! "the right colours on an SDR screen".
//!
//! swscale converts matrices and ranges; it knows nothing about transfer
//! functions or gamuts, so HDR comes out washed out and Dolby Vision profile 5
//! -- whose IPT-C2 colour only libplacebo converts -- comes out magenta.
//! libplacebo tone-maps HDR10/HLG to SDR, applies Dolby Vision reshaping from
//! the RPU FFmpeg attaches to each frame, and scales with proper filters.
//!
//! This is the first step towards rendering on Qt's own Vulkan device (see
//! the roadmap in `native/player/README.md`): libplacebo runs on a Vulkan
//! device of its own and the result is read back into the embedder's buffer,
//! so the video item is unchanged. One readback per shown frame is the price
//! until the zero-copy step removes it. Without a usable Vulkan device the
//! player keeps rendering with swscale.

use std::ffi::{CStr, c_char, c_int, c_void};
use std::ptr;

use ffmpeg_next::frame;
use libplacebo_sys as pl;

use crate::picture::{OverlayImage, SharedImage};
use crate::render::RenderTarget;

/// Why the GPU renderer could not start or render.
#[derive(Debug, thiserror::Error)]
pub(crate) enum GpuError {
    #[error("libplacebo could not create a Vulkan device")]
    NoDevice,
    #[error("libplacebo could not create a renderer")]
    NoRenderer,
    #[error("the GPU has no renderable, host-readable RGBA8 format")]
    NoTargetFormat,
    #[error("could not create a {0}x{1} render target")]
    Target(u32, u32),
    #[error("the frame could not be mapped to the GPU")]
    Map,
    #[error("rendering failed")]
    Render,
    #[error("reading the picture back failed")]
    Download,
}

/// Qt's Vulkan device, as the embedder hands it over (handles as integers).
///
/// `features` points at the `VkPhysicalDeviceFeatures2` chain the device was
/// created with, and must stay valid for the renderer's lifetime.
#[derive(Clone, Copy, Debug)]
pub struct VulkanDevice {
    pub instance: u64,
    pub get_instance_proc_addr: u64,
    pub physical_device: u64,
    pub device: u64,
    pub queue_family: u32,
    pub queue_index: u32,
    pub api_version: u32,
    pub features: u64,
}

// Vulkan values the bindings leave out (only libplacebo's names are bound).
const VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL: pl::VkImageLayout = 5;
const VK_QUEUE_FAMILY_IGNORED: u32 = !0;
const VK_SEMAPHORE_TYPE_TIMELINE: pl::VkSemaphoreType = 1;

/// How many images the embedder may be sampling from while the next is
/// drawn: the one on screen, one Qt may still be finishing a frame with, and
/// the one being rendered.
const SHARED_RING: usize = 3;

/// One image of the ring, and whether the embedder holds it.
struct SharedTarget {
    tex: pl::pl_tex,
    held: bool,
}

/// libplacebo's objects for one Vulkan device.
pub(crate) struct GpuRenderer {
    log: pl::pl_log,
    vulkan: pl::pl_vulkan,
    gpu: pl::pl_gpu,
    renderer: pl::pl_renderer,
    /// Upload textures, reused frame to frame (libplacebo's advice: creating
    /// them per frame is most of the cost of mapping one).
    planes: [pl::pl_tex; 4],
    /// The readback target (own device only).
    target: pl::pl_tex,
    target_format: pl::pl_fmt,
    /// The ring handed to the embedder (imported device only), and images
    /// replaced by a new size, which the embedder may still be sampling:
    /// they go with the renderer, never before.
    ring: Vec<SharedTarget>,
    retired: Vec<pl::pl_tex>,
    next: usize,
    /// Signalled as each image is handed over. Nothing waits on it -- the
    /// embedder samples on the same queue, after this in submission order, and
    /// libplacebo's barrier orders the two -- but handing an image over needs
    /// one, and a timeline semaphore may be signalled without a waiter.
    handover: pl::VkSemaphore,
    handover_value: u64,
    /// The subtitle overlay texture, re-uploaded only when subtitles change.
    overlay: pl::pl_tex,
}

// SAFETY: the renderer is only ever used through `&mut self` (behind a
// lock), so libplacebo never sees two threads at once; libplacebo objects are
// not tied to the thread that created them. An imported renderer is further
// only used on the embedder's render thread (see Player::render_shared).
unsafe impl Send for GpuRenderer {}

impl std::fmt::Debug for GpuRenderer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("GpuRenderer").finish_non_exhaustive()
    }
}

/// Compiled shaders kept on disk between runs. The first render of a run
/// compiles libplacebo's shaders -- ~160 ms before the first picture, every
/// launch -- and with the cache only the first launch pays it, as with mpv's
/// shader cache directory. Each object is written as it is made
/// (`pl_cache_set_file`), so nothing waits for a save and a crash loses
/// nothing. Process-wide and never freed: every renderer may hold it.
struct ShaderCache(pl::pl_cache);

// SAFETY: a pl_cache is internally synchronised, and this one is never
// destroyed.
unsafe impl Send for ShaderCache {}
unsafe impl Sync for ShaderCache {}

static SHADER_CACHE: std::sync::OnceLock<ShaderCache> = std::sync::OnceLock::new();

/// What the cache may hold in memory; everything stays on disk.
const SHADER_CACHE_MEMORY: usize = 64 * 1024 * 1024;

/// Keeps compiled shaders in `directory` from now on (created if needed).
/// The first call wins; later ones are ignored.
pub fn configure_shader_cache(directory: &std::path::Path) {
    if SHADER_CACHE.get().is_some() {
        return;
    }
    if let Err(error) = std::fs::create_dir_all(directory) {
        log::warn!("no shader cache at {}: {error}", directory.display());
        return;
    }
    // The callbacks join this and a hex key, so it ends in a separator.
    let mut path = directory.as_os_str().to_owned();
    path.push(std::path::MAIN_SEPARATOR_STR);
    let Ok(path) = std::ffi::CString::new(path.into_encoded_bytes()) else {
        return;
    };
    let params = pl::pl_cache_params {
        log: log_create(),
        max_object_size: 0,
        max_total_size: SHADER_CACHE_MEMORY,
        set: Some(pl::pl_cache_set_file),
        get: Some(pl::pl_cache_get_file),
        // Lives as long as the cache: for the process.
        priv_: path.into_raw().cast(),
    };
    // SAFETY: valid parameters, copied; the path they point at is leaked.
    let cache = unsafe { pl::pl_cache_create(&raw const params) };
    if !cache.is_null() {
        let _ = SHADER_CACHE.set(ShaderCache(cache));
    }
}

/// The address `log_priv` carries while the engine looks for a device of its
/// own. That search is a guess -- a machine without a Vulkan driver (a VM, a
/// GPU too old) answers it with a burst of errors, and the engine then says
/// in one line that it renders through swscale -- so what libplacebo reports
/// meanwhile is filed at debug. Its value is never read.
static SEARCHING: u8 = 0;

fn log_params(searching: bool) -> pl::pl_log_params {
    pl::pl_log_params {
        log_cb: Some(log_message),
        log_priv: if searching {
            (&raw const SEARCHING).cast_mut().cast::<c_void>()
        } else {
            ptr::null_mut()
        },
        log_level: pl::PL_LOG_WARN,
    }
}

pub(crate) fn log_create() -> pl::pl_log {
    log_create_with(&log_params(false))
}

fn log_create_with(params: &pl::pl_log_params) -> pl::pl_log {
    // SAFETY: the parameters are copied by libplacebo.
    unsafe { pl::pl_log_create(pl::PL_API_VER as c_int, params) }
}

impl GpuRenderer {
    /// Starts libplacebo on the system's Vulkan device. A software rasteriser
    /// (llvmpipe) is refused: it would be slower than swscale.
    pub(crate) fn new() -> Result<Self, GpuError> {
        Self::with_software(false)
    }

    /// libplacebo on any device, a software one included -- the reference
    /// the Metal renderer's tests compare against. None without Vulkan at
    /// all.
    #[cfg(all(test, target_os = "macos"))]
    pub(crate) fn reference() -> Option<Self> {
        Self::with_software(true)
            .inspect_err(|error| eprintln!("no libplacebo reference: {error}"))
            .ok()
    }

    fn with_software(allow_software: bool) -> Result<Self, GpuError> {
        let log = log_create_with(&log_params(true));
        // SAFETY: libplacebo's constant defaults, copied.
        let mut params = unsafe { pl::pl_vulkan_default_params };
        params.allow_software = allow_software;
        // SAFETY: valid log and parameters; the result is checked in finish.
        let vulkan = unsafe { pl::pl_vulkan_create(log, &raw const params) };
        let renderer = Self::finish(log, vulkan)?;
        // Found: from here on an error is one.
        let found = log_params(false);
        // SAFETY: a live log; the parameters are copied.
        unsafe { pl::pl_log_update(log, &raw const found) };
        Ok(renderer)
    }

    /// Starts libplacebo on the embedder's own Vulkan device, so what it
    /// renders can be sampled where it lies.
    ///
    /// # Safety
    /// `device` must describe a live device (and the feature chain it was
    /// created with) that outlives the renderer, and the renderer must only
    /// be used on the thread that submits the embedder's own work to that
    /// queue -- libplacebo is given no queue lock.
    pub(crate) unsafe fn import(device: &VulkanDevice) -> Result<Self, GpuError> {
        let log = log_create();
        let queue = pl::pl_vulkan_queue {
            index: device.queue_family,
            count: device.queue_index + 1,
        };
        let params = pl::pl_vulkan_import_params {
            instance: device.instance as pl::VkInstance,
            // SAFETY: a PFN_vkGetInstanceProcAddr, per the caller.
            get_proc_addr: unsafe {
                std::mem::transmute::<u64, pl::PFN_vkGetInstanceProcAddr>(
                    device.get_instance_proc_addr,
                )
            },
            phys_device: device.physical_device as pl::VkPhysicalDevice,
            device: device.device as pl::VkDevice,
            // No extensions beyond what Vulkan 1.2+ makes core: listing one
            // the device was not created with would be undefined, and
            // rendering needs none.
            extensions: ptr::null(),
            num_extensions: 0,
            queue_graphics: queue,
            queue_compute: queue,
            queue_transfer: queue,
            features: device.features as *const pl::VkPhysicalDeviceFeatures2,
            max_api_version: device.api_version,
            ..pl::pl_vulkan_import_params::default()
        };
        // SAFETY: the handles are live, per the caller.
        let vulkan = unsafe { pl::pl_vulkan_import(log, &raw const params) };
        let mut renderer = Self::finish(log, vulkan)?;
        let semaphore = pl::pl_vulkan_sem_params {
            type_: VK_SEMAPHORE_TYPE_TIMELINE,
            initial_value: 0,
            ..pl::pl_vulkan_sem_params::default()
        };
        // SAFETY: a valid gpu; the semaphore is destroyed in Drop.
        renderer.handover = unsafe { pl::pl_vulkan_sem_create(renderer.gpu, &raw const semaphore) };
        if renderer.handover.is_null() {
            return Err(GpuError::NoDevice);
        }
        Ok(renderer)
    }

    fn finish(log: pl::pl_log, vulkan: pl::pl_vulkan) -> Result<Self, GpuError> {
        let mut renderer = Self {
            log,
            vulkan,
            gpu: ptr::null(),
            renderer: ptr::null_mut(),
            planes: [ptr::null(); 4],
            target: ptr::null(),
            target_format: ptr::null(),
            ring: Vec::new(),
            retired: Vec::new(),
            next: 0,
            handover: ptr::null_mut(),
            handover_value: 0,
            overlay: ptr::null(),
        };
        if vulkan.is_null() {
            return Err(GpuError::NoDevice);
        }
        // SAFETY: a live pl_vulkan; Drop frees whatever was created.
        unsafe {
            renderer.gpu = (*vulkan).gpu;
            if let Some(cache) = SHADER_CACHE.get() {
                pl::pl_gpu_set_cache(renderer.gpu, cache.0);
            }
            renderer.renderer = pl::pl_renderer_create(log, renderer.gpu);
            if renderer.renderer.is_null() {
                return Err(GpuError::NoRenderer);
            }
            renderer.target_format = pl::pl_find_named_fmt(renderer.gpu, c"rgba8".as_ptr());
        }
        if renderer.target_format.is_null() {
            return Err(GpuError::NoTargetFormat);
        }
        Ok(renderer)
    }

    /// Renders `source` into `target`, stretched to its size, in SDR sRGB, and
    /// reads it back.
    pub(crate) fn render(
        &mut self,
        source: &frame::Video,
        target: &mut RenderTarget<'_>,
    ) -> Result<(), GpuError> {
        self.ensure_readback_target(target.width, target.height)?;
        let tex = self.target;
        self.draw(source, tex, None)?;
        let transfer = pl::pl_tex_transfer_params {
            tex,
            row_pitch: target.stride,
            ptr: target.pixels.as_mut_ptr().cast::<c_void>(),
            ..pl::pl_tex_transfer_params::default()
        };
        // SAFETY: the download writes at most `height` rows of `stride`
        // bytes, which RenderTarget::check guarantees the buffer holds (see
        // Player::render, which checks it before choosing a renderer). It
        // blocks until the bytes are there: no callback given.
        if unsafe { pl::pl_tex_download(self.gpu, &raw const transfer) } {
            Ok(())
        } else {
            Err(GpuError::Download)
        }
    }

    /// Renders `source` at `width` x `height` into the next image of the
    /// ring and hands it to the embedder (imported device only).
    pub(crate) fn render_shared(
        &mut self,
        source: &frame::Video,
        width: u32,
        height: u32,
        overlay: Option<&OverlayImage<'_>>,
    ) -> Result<SharedImage, GpuError> {
        self.ensure_ring(width, height)?;
        let slot = self.next;
        self.next = (self.next + 1) % SHARED_RING;
        let tex = self.ring[slot].tex;
        // SAFETY: tex belongs to this renderer's gpu; a held image is taken
        // back before libplacebo touches it again, and handed over after.
        unsafe {
            if self.ring[slot].held {
                let release = pl::pl_vulkan_release_params {
                    tex,
                    layout: VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
                    qf: VK_QUEUE_FAMILY_IGNORED,
                    ..pl::pl_vulkan_release_params::default()
                };
                pl::pl_vulkan_release_ex(self.gpu, &raw const release);
                self.ring[slot].held = false;
            }
            self.draw(source, tex, overlay)?;
            self.handover_value += 1;
            let hold = pl::pl_vulkan_hold_params {
                tex,
                layout: VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
                qf: VK_QUEUE_FAMILY_IGNORED,
                semaphore: pl::pl_vulkan_sem {
                    sem: self.handover,
                    value: self.handover_value,
                },
                ..pl::pl_vulkan_hold_params::default()
            };
            if !pl::pl_vulkan_hold_ex(self.gpu, &raw const hold) {
                return Err(GpuError::Render);
            }
            self.ring[slot].held = true;
            let image = pl::pl_vulkan_unwrap(self.gpu, tex, ptr::null_mut(), ptr::null_mut());
            Ok(SharedImage {
                image: image as u64,
                width,
                height,
            })
        }
    }

    /// Copies the image last handed over by `render_shared` into `target`,
    /// which must be its size (imported device only). The image is taken
    /// back for the download and handed over again after.
    pub(crate) fn read_last_shared(
        &mut self,
        target: &mut RenderTarget<'_>,
    ) -> Result<(), GpuError> {
        let slot = (self.next + SHARED_RING - 1) % SHARED_RING;
        let Some(handed) = self.ring.get(slot).filter(|t| t.held) else {
            return Err(GpuError::Download);
        };
        let tex = handed.tex;
        // SAFETY: tex belongs to this renderer's gpu and is held; it is
        // released before libplacebo reads it and held again after. The
        // download writes `height` rows of `stride` bytes, which the caller's
        // checked target holds.
        unsafe {
            let (width, height) = ((*tex).params.w, (*tex).params.h);
            if (width, height) != (target.width as c_int, target.height as c_int) {
                return Err(GpuError::Target(target.width, target.height));
            }
            let release = pl::pl_vulkan_release_params {
                tex,
                layout: VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
                qf: VK_QUEUE_FAMILY_IGNORED,
                ..pl::pl_vulkan_release_params::default()
            };
            pl::pl_vulkan_release_ex(self.gpu, &raw const release);
            let transfer = pl::pl_tex_transfer_params {
                tex,
                row_pitch: target.stride,
                ptr: target.pixels.as_mut_ptr().cast::<c_void>(),
                ..pl::pl_tex_transfer_params::default()
            };
            let downloaded = pl::pl_tex_download(self.gpu, &raw const transfer);
            self.handover_value += 1;
            let hold = pl::pl_vulkan_hold_params {
                tex,
                layout: VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
                qf: VK_QUEUE_FAMILY_IGNORED,
                semaphore: pl::pl_vulkan_sem {
                    sem: self.handover,
                    value: self.handover_value,
                },
                ..pl::pl_vulkan_hold_params::default()
            };
            if !pl::pl_vulkan_hold_ex(self.gpu, &raw const hold) {
                self.ring[slot].held = false;
                return Err(GpuError::Download);
            }
            if downloaded {
                Ok(())
            } else {
                Err(GpuError::Download)
            }
        }
    }

    /// Maps `source`, renders it into `tex` with `overlay` on top, unmaps.
    fn draw(
        &mut self,
        source: &frame::Video,
        tex: pl::pl_tex,
        overlay: Option<&OverlayImage<'_>>,
    ) -> Result<(), GpuError> {
        let overlay_part;
        let overlay_desc;
        // SAFETY: `source` wraps a valid decoded AVFrame for this call; the
        // mapped frame is unmapped before returning; the overlay description
        // outlives pl_render_image, which reads it.
        unsafe {
            let mut output = pl::pl_frame {
                num_planes: 1,
                repr: pl::pl_color_repr_rgb,
                color: pl::pl_color_space_srgb,
                ..pl::pl_frame::default()
            };
            output.planes[0] = pl::pl_plane {
                texture: tex,
                components: 4,
                component_mapping: [0, 1, 2, 3],
                ..pl::pl_plane::default()
            };
            if let Some(overlay) = overlay {
                self.upload_overlay(overlay)?;
                let (w, h) = (overlay.width as f32, overlay.height as f32);
                overlay_part = pl::pl_overlay_part {
                    src: pl::pl_rect2df {
                        x0: 0.0,
                        y0: 0.0,
                        x1: w,
                        y1: h,
                    },
                    dst: pl::pl_rect2df {
                        x0: 0.0,
                        y0: 0.0,
                        x1: w,
                        y1: h,
                    },
                    color: [1.0; 4],
                };
                let mut repr = pl::pl_color_repr_rgb;
                repr.alpha = pl::PL_ALPHA_INDEPENDENT;
                overlay_desc = pl::pl_overlay {
                    tex: self.overlay,
                    mode: pl::PL_OVERLAY_NORMAL,
                    coords: pl::PL_OVERLAY_COORDS_DST_FRAME,
                    repr,
                    color: pl::pl_color_space_srgb,
                    parts: &raw const overlay_part,
                    num_parts: 1,
                };
                output.overlays = &raw const overlay_desc;
                output.num_overlays = 1;
            }
            let mut image = pl::pl_frame::default();
            let map = pl::pl_avframe_params {
                frame: source.as_ptr().cast(),
                tex: self.planes.as_mut_ptr(),
                // Dolby Vision reshaping from the RPU FFmpeg decoded: what
                // turns profile 5 from magenta into the picture.
                map_dovi: true,
            };
            if !pl::pl_map_avframe_ex(self.gpu, &raw mut image, &raw const map) {
                return Err(GpuError::Map);
            }
            let rendered = pl::pl_render_image(
                self.renderer,
                &raw const image,
                &raw const output,
                &raw const pl::pl_render_default_params,
            );
            pl::pl_unmap_avframe(self.gpu, &raw mut image);
            if rendered {
                Ok(())
            } else {
                Err(GpuError::Render)
            }
        }
    }

    fn upload_overlay(&mut self, overlay: &OverlayImage<'_>) -> Result<(), GpuError> {
        let params = pl::pl_tex_params {
            w: overlay.width as c_int,
            h: overlay.height as c_int,
            format: self.target_format,
            sampleable: true,
            host_writable: true,
            ..pl::pl_tex_params::default()
        };
        // SAFETY: valid gpu and format; the upload reads `height` rows of
        // `width * 4` bytes, which the overlay's buffer holds.
        unsafe {
            if !pl::pl_tex_recreate(self.gpu, &raw mut self.overlay, &raw const params) {
                return Err(GpuError::Target(overlay.width, overlay.height));
            }
            let transfer = pl::pl_tex_transfer_params {
                tex: self.overlay,
                row_pitch: overlay.width as usize * 4,
                ptr: overlay.pixels.as_ptr().cast_mut().cast::<c_void>(),
                ..pl::pl_tex_transfer_params::default()
            };
            if pl::pl_tex_upload(self.gpu, &raw const transfer) {
                Ok(())
            } else {
                Err(GpuError::Download)
            }
        }
    }

    fn ensure_readback_target(&mut self, width: u32, height: u32) -> Result<(), GpuError> {
        let params = pl::pl_tex_params {
            w: width as c_int,
            h: height as c_int,
            format: self.target_format,
            renderable: true,
            host_readable: true,
            ..pl::pl_tex_params::default()
        };
        // SAFETY: valid gpu and format; pl_tex_recreate only rebuilds when
        // the parameters changed.
        if unsafe { pl::pl_tex_recreate(self.gpu, &raw mut self.target, &raw const params) } {
            Ok(())
        } else {
            Err(GpuError::Target(width, height))
        }
    }

    /// A ring of `width` x `height` images. The previous ring is retired, not
    /// destroyed: the embedder's renderer may sample an image for frames
    /// after it last saw it.
    fn ensure_ring(&mut self, width: u32, height: u32) -> Result<(), GpuError> {
        // SAFETY: ring textures belong to this gpu.
        let current = self
            .ring
            .first()
            .map(|t| unsafe { ((*t.tex).params.w, (*t.tex).params.h) });
        if current == Some((width as c_int, height as c_int)) {
            return Ok(());
        }
        self.retired.extend(self.ring.drain(..).map(|t| t.tex));
        self.next = 0;
        for _ in 0..SHARED_RING {
            let params = pl::pl_tex_params {
                w: width as c_int,
                h: height as c_int,
                format: self.target_format,
                sampleable: true,
                renderable: true,
                // For read_last_shared; costs a transfer-source usage bit.
                host_readable: true,
                ..pl::pl_tex_params::default()
            };
            // SAFETY: valid gpu and format.
            let tex = unsafe { pl::pl_tex_create(self.gpu, &raw const params) };
            if tex.is_null() {
                return Err(GpuError::Target(width, height));
            }
            self.ring.push(SharedTarget { tex, held: false });
        }
        Ok(())
    }
}

impl Drop for GpuRenderer {
    fn drop(&mut self) {
        // SAFETY: each object was created by this renderer and is used
        // nowhere else; libplacebo's destroy functions accept null, and
        // destroying a held image is allowed.
        unsafe {
            if !self.gpu.is_null() {
                // The last hand-over's submission signals `handover`; a
                // semaphore may not be destroyed while a queue still uses it
                // (the validation layer caught exactly that at teardown).
                pl::pl_gpu_finish(self.gpu);
                for plane in &mut self.planes {
                    pl::pl_tex_destroy(self.gpu, plane);
                }
                pl::pl_tex_destroy(self.gpu, &raw mut self.target);
                pl::pl_tex_destroy(self.gpu, &raw mut self.overlay);
                for mut target in self.ring.drain(..) {
                    pl::pl_tex_destroy(self.gpu, &raw mut target.tex);
                }
                for mut tex in self.retired.drain(..) {
                    pl::pl_tex_destroy(self.gpu, &raw mut tex);
                }
                if !self.handover.is_null() {
                    pl::pl_vulkan_sem_destroy(self.gpu, &raw mut self.handover);
                }
            }
            pl::pl_renderer_destroy(&raw mut self.renderer);
            pl::pl_vulkan_destroy(&raw mut self.vulkan);
            pl::pl_log_destroy(&raw mut self.log);
        }
    }
}

/// libplacebo's log, into ours -- at debug while `data` says the engine is
/// only looking for a device (see SEARCHING).
unsafe extern "C" fn log_message(
    data: *mut c_void,
    level: pl::pl_log_level,
    message: *const c_char,
) {
    if message.is_null() {
        return;
    }
    // SAFETY: libplacebo passes a valid NUL-terminated message.
    let text = unsafe { CStr::from_ptr(message) }.to_string_lossy();
    if !data.is_null() {
        log::debug!("libplacebo, looking for a device: {text}");
    } else if level <= pl::PL_LOG_ERR {
        log::error!("libplacebo: {text}");
    } else {
        log::warn!("libplacebo: {text}");
    }
}

#[cfg(test)]
mod tests {
    use ffmpeg_next::format::Pixel;

    use super::*;

    /// A renderer on any device, a software one included: the tests check
    /// what comes out, not how fast. Skips (None) with no Vulkan at all.
    fn renderer() -> Option<GpuRenderer> {
        match GpuRenderer::with_software(true) {
            Ok(renderer) => Some(renderer),
            Err(error) => {
                eprintln!("skipped: {error}");
                None
            }
        }
    }

    fn render(
        renderer: &mut GpuRenderer,
        source: &frame::Video,
        width: u32,
        height: u32,
    ) -> Vec<u8> {
        let mut pixels = vec![0u8; (width * height * 4) as usize];
        let mut target = RenderTarget {
            pixels: &mut pixels,
            width,
            height,
            stride: width as usize * 4,
        };
        renderer.render(source, &mut target).expect("renders");
        pixels
    }

    fn solid(format: Pixel, luma: u16, depth_shift: u32) -> frame::Video {
        let mut frame = frame::Video::new(format, 64, 36);
        let wide = depth_shift > 0;
        for plane in 0..3 {
            let value = if plane == 0 { luma } else { 128 << depth_shift };
            let data = frame.data_mut(plane);
            if wide {
                for pair in data.as_chunks_mut::<2>().0 {
                    pair.copy_from_slice(&value.to_ne_bytes());
                }
            } else {
                data.fill(value as u8);
            }
        }
        frame
    }

    #[test]
    fn sdr_white_stays_white() {
        let Some(mut renderer) = renderer() else {
            return;
        };
        let mut white = solid(Pixel::YUV420P, 235, 0);
        white.set_color_space(ffmpeg_next::color::Space::BT709);
        white.set_color_range(ffmpeg_next::color::Range::MPEG);
        let pixels = render(&mut renderer, &white, 32, 18);
        assert!(
            pixels
                .as_chunks::<4>()
                .0
                .iter()
                .all(|p| p[0] > 245 && p[1] > 245 && p[2] > 245),
            "{:?}",
            &pixels[..4]
        );
    }

    #[test]
    fn hdr_is_tone_mapped_rather_than_read_as_sdr() {
        let Some(mut renderer) = renderer() else {
            return;
        };
        // 10-bit PQ at about 100 nits (code ~520 of 1023 in limited range):
        // SDR reference white. Read as SDR -- which is what swscale does --
        // that code value is a mid grey.
        let mut hdr = solid(Pixel::YUV420P10LE, 520, 2);
        hdr.set_color_space(ffmpeg_next::color::Space::BT2020NCL);
        hdr.set_color_range(ffmpeg_next::color::Range::MPEG);
        hdr.set_color_primaries(ffmpeg_next::color::Primaries::BT2020);
        hdr.set_color_transfer_characteristic(
            ffmpeg_next::color::TransferCharacteristic::SMPTE2084,
        );
        let pixels = render(&mut renderer, &hdr, 32, 18);
        let red = pixels[0];
        assert!(red > 170, "100-nit PQ white rendered as {:?}", &pixels[..4]);
    }

    #[test]
    fn a_resize_rebuilds_the_target() {
        let Some(mut renderer) = renderer() else {
            return;
        };
        let frame = solid(Pixel::YUV420P, 128, 0);
        render(&mut renderer, &frame, 32, 18);
        let pixels = render(&mut renderer, &frame, 48, 27);
        assert_eq!(pixels.len(), 48 * 27 * 4);
    }
}
