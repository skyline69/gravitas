//! Rendering through Metal on macOS: the step from "a decoded frame" to
//! "the right colours on an SDR screen", as `gpu.rs` does with libplacebo on
//! Vulkan elsewhere.
//!
//! libplacebo has no Metal backend, and Qt's scene graph runs on Metal on
//! macOS; going through a Vulkan translation layer to reach it was ruled out.
//! So this is a renderer of its own: one fragment shader (`shaders.metal`)
//! decodes the frame's planes, applies Dolby Vision reshaping, linearises,
//! tone-maps HDR on IPT's intensity with libplacebo's spline and encodes for
//! an sRGB display, with every parameter worked out on the CPU (`colour.rs`).
//!
//! Two ways to run, like `gpu.rs`:
//!
//! - **Readback**: a device and queue of its own; the picture is copied into
//!   the embedder's buffer.
//! - **Shared**: Qt's own device and command queue. Frames render into a
//!   ring of textures the scene graph samples where they lie, and
//!   VideoToolbox's surfaces are sampled where the decoder wrote them -- no
//!   copy anywhere. Ordering against Qt needs no fence: both submit to the
//!   same queue, Metal runs its command buffers in order, and its hazard
//!   tracking orders a write after an earlier read of the same texture.

mod colour;
mod planes;

use std::collections::HashMap;
use std::ffi::c_void;
use std::ptr::NonNull;

use ffmpeg_next::{ffi, frame};
use objc2::rc::{Retained, autoreleasepool};
use objc2::runtime::ProtocolObject;
use objc2_core_foundation::CFRetained;
use objc2_core_video::{CVMetalTextureCache, kCVReturnSuccess};
use objc2_foundation::NSString;
use objc2_metal::{
    MTLBlitCommandEncoder, MTLBuffer, MTLCommandBuffer, MTLCommandBufferStatus, MTLCommandEncoder,
    MTLCommandQueue, MTLComputeCommandEncoder, MTLComputePipelineState,
    MTLCreateSystemDefaultDevice, MTLDataType, MTLDevice, MTLFunctionConstantValues, MTLLibrary,
    MTLLoadAction, MTLOrigin, MTLPixelFormat, MTLPrimitiveType, MTLRenderCommandEncoder,
    MTLRenderPassDescriptor, MTLRenderPipelineDescriptor, MTLRenderPipelineState,
    MTLResourceOptions, MTLSize, MTLStoreAction, MTLTexture, MTLTextureUsage,
};

use crate::picture::{OverlayImage, SharedImage};
use crate::render::RenderTarget;
use colour::{Dovi, Layout, Mat3, Plan};
use planes::{Planes, Texture, Uploads};

/// Why the Metal renderer could not start or render.
#[derive(Debug, thiserror::Error)]
pub(crate) enum MetalError {
    #[error("no Metal device")]
    NoDevice,
    #[error("the video shaders did not compile: {0}")]
    Shaders(String),
    #[error("could not create a {0}x{1} render target")]
    Target(u32, u32),
    #[error("the frame's format cannot be rendered")]
    Format,
    #[error("rendering failed")]
    Render,
}

/// Qt's Metal device and command queue, as the embedder hands them over
/// (`id<MTLDevice>` and `id<MTLCommandQueue>` as integers).
#[derive(Clone, Copy, Debug)]
pub struct MetalDevice {
    pub device: u64,
    pub queue: u64,
}

type Device = Retained<ProtocolObject<dyn MTLDevice>>;
type Queue = Retained<ProtocolObject<dyn MTLCommandQueue>>;
type CommandBuffer = Retained<ProtocolObject<dyn MTLCommandBuffer>>;
type Pipeline = Retained<ProtocolObject<dyn MTLRenderPipelineState>>;
type Kernel = Retained<ProtocolObject<dyn MTLComputePipelineState>>;
type Buffer = Retained<ProtocolObject<dyn MTLBuffer>>;

/// What the shader is specialised on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
struct PipelineKey {
    layout: Layout,
    transfer: u32,
    dovi: bool,
    tone_map: bool,
    overlay: bool,
    target: MTLPixelFormat,
}

/// The shader's parameters; mirrors `Params` in `shaders.metal`.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
struct Params {
    decode: [[f32; 4]; 3],
    sampling: [f32; 4],
    transfer: [f32; 4],
    luma: [f32; 4],
    linear_scale: [f32; 4],
    to_output: [[f32; 4]; 3],
    to_lms: [[f32; 4]; 3],
    from_lms: [[f32; 4]; 3],
    to_ictcp: [[f32; 4]; 3],
    from_ictcp: [[f32; 4]; 3],
    dovi_linear: [[f32; 4]; 3],
    tone_range: [f32; 4],
    tone_metadata: [f32; 4],
}

/// One component's reshaping; mirrors `DoviComponent`.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
struct DoviComponent {
    coeffs: [[f32; 4]; 8],
    mmr: [[f32; 4]; 48],
    pivots: [[f32; 4]; 2],
    range: [f32; 4],
}

/// Mirrors `DoviParams`.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
struct DoviParams {
    comp: [DoviComponent; 3],
}

const _: () = assert!(
    size_of::<DoviParams>() <= 4096,
    "setFragmentBytes takes at most 4 KiB"
);

fn rows(m: &Mat3) -> [[f32; 4]; 3] {
    [0, 1, 2].map(|i| [m[i][0] as f32, m[i][1] as f32, m[i][2] as f32, 0.0])
}

impl Params {
    fn new(plan: &Plan, dither_bits: u32, reset_tone: bool) -> Self {
        let mut decode = rows(&plan.decode);
        for (row, offset) in decode.iter_mut().zip(plan.decode_offset) {
            row[3] = offset as f32;
        }
        let (tone_range, tone_metadata) = plan.tone.map_or(([0.0; 4], [0.0; 4]), |tone| {
            let peak = tone.metadata.unwrap_or(0.0);
            (
                [
                    tone.input_min as f32,
                    tone.output_min as f32,
                    tone.output_max as f32,
                    f32::from(u8::from(reset_tone)),
                ],
                [
                    f32::from(u8::from(tone.metadata.is_some())),
                    peak as f32,
                    0.0,
                    0.0,
                ],
            )
        });
        Self {
            decode,
            sampling: [
                plan.sample_scale as f32,
                plan.chroma_offset.0 as f32,
                plan.chroma_offset.1 as f32,
                1.0 / ((1u32 << dither_bits) - 1) as f32,
            ],
            transfer: plan.transfer_params.map(|v| v as f32),
            luma: [
                plan.luma[0] as f32,
                plan.luma[1] as f32,
                plan.luma[2] as f32,
                0.0,
            ],
            linear_scale: [
                plan.linear_scale.0 as f32,
                plan.linear_scale.1 as f32,
                plan.output_black as f32,
                f32::from(u8::from(plan.compress_gamut)),
            ],
            to_output: rows(&plan.to_output),
            to_lms: rows(&plan.to_lms),
            from_lms: rows(&plan.from_lms),
            to_ictcp: rows(&colour::LMS_TO_ICTCP),
            from_ictcp: rows(&colour::invert(&colour::LMS_TO_ICTCP)),
            dovi_linear: rows(&plan.dovi_linear),
            tone_range,
            tone_metadata,
        }
    }
}

impl DoviParams {
    /// The RPU's curves, packed the way the shader reads them.
    fn new(dovi: &Dovi) -> Box<Self> {
        let empty = DoviComponent {
            coeffs: [[0.0; 4]; 8],
            mmr: [[0.0; 4]; 48],
            pivots: [[1e9; 4]; 2],
            range: [0.0; 4],
        };
        let mut out = Box::new(Self { comp: [empty; 3] });
        for (dst, src) in out.comp.iter_mut().zip(&dovi.comp) {
            let pivots = src.num_pivots.clamp(0, 9) as usize;
            if pivots < 2 {
                continue;
            }
            let mut mmr_index = 0;
            for piece in 0..pivots - 1 {
                if src.method[piece] == 0 {
                    let [a, b, c] = src.poly[piece];
                    dst.coeffs[piece] = [a, b, c, 0.0];
                } else {
                    let order = src.mmr_order[piece].clamp(1, 3) as usize;
                    dst.coeffs[piece] =
                        [src.mmr_constant[piece], mmr_index as f32, 0.0, order as f32];
                    for j in 0..order {
                        let w = src.mmr[piece][j];
                        dst.mmr[mmr_index] = [w[0], w[1], w[2], 0.0];
                        dst.mmr[mmr_index + 1] = [w[3], w[4], w[5], w[6]];
                        mmr_index += 2;
                    }
                }
            }
            for (i, &pivot) in src.pivots[1..pivots - 1].iter().enumerate() {
                dst.pivots[i / 4][i % 4] = pivot;
            }
            dst.range = [
                src.pivots[0],
                src.pivots[pivots - 1],
                (pivots - 1) as f32,
                0.0,
            ];
        }
        out
    }
}

/// Nothing to configure on macOS: Metal keeps compiled pipelines in the
/// system's own shader cache, per app and per GPU, across launches.
pub fn configure_shader_cache(_directory: &std::path::Path) {}

/// `ToneState`'s size: the histogram, the smoothed peak and padding, the
/// curve.
const TONE_STATE_BYTES: usize = 256 * 4 + 16 + 2 * 16;

/// How many frames may be in flight: the one on screen, one Qt may still
/// be finishing a frame with, and the one being rendered.
const RING: usize = 3;

/// Per-frame resources the GPU may still be reading.
#[derive(Debug, Default)]
struct Slot {
    uploads: Uploads,
    overlay: Option<Texture>,
    /// VideoToolbox wrappers and the command buffer that last read them.
    keep: Vec<CFRetained<objc2_core_video::CVMetalTexture>>,
    pending: Option<CommandBuffer>,
}

impl Slot {
    /// Waits for the GPU to be done with this slot's resources, then lets
    /// the ones kept for it go.
    fn reclaim(&mut self) {
        if let Some(pending) = self.pending.take()
            && !matches!(
                pending.status(),
                MTLCommandBufferStatus::Completed | MTLCommandBufferStatus::Error
            )
        {
            pending.waitUntilCompleted();
        }
        self.keep.clear();
    }
}

/// The renderer on one Metal device.
pub(crate) struct MetalRenderer {
    device: Device,
    queue: Queue,
    library: Retained<ProtocolObject<dyn MTLLibrary>>,
    pipelines: HashMap<PipelineKey, Pipeline>,
    /// Brightness measurement, per (layout, transfer, Dolby Vision).
    measure: HashMap<(Layout, u32, bool), Kernel>,
    update_tone: Option<Kernel>,
    /// `ToneState` in `shaders.metal`: the HDR passes' shared state.
    tone_state: Buffer,
    /// The last frame's tone curve came from measuring: the next measured
    /// frame may be smoothed against it.
    measured: bool,
    cache: CFRetained<CVMetalTextureCache>,
    slots: [Slot; RING],
    next_slot: usize,
    /// The readback target and the buffer it is copied into (own device).
    readback: Option<(Texture, Buffer, u32, u32)>,
    /// The ring handed to the embedder (Qt's device).
    ring: Vec<Texture>,
    next: usize,
    /// Converts frames the shader cannot sample as they are.
    converter: Option<Converter>,
}

impl std::fmt::Debug for MetalRenderer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MetalRenderer").finish_non_exhaustive()
    }
}

// SAFETY: Metal devices, queues, pipelines and textures are thread-safe
// objects; the renderer itself is only used through `&mut self` behind a
// lock, and the swscale context inside `Converter` is never shared.
unsafe impl Send for MetalRenderer {}

/// The pixel format of the images handed to the embedder: 10 bits, because
/// HDR tone-mapped into SDR bands visibly at 8.
pub(crate) const SHARED_FORMAT: MTLPixelFormat = MTLPixelFormat::RGB10A2Unorm;
/// The readback format: R, G, B, X bytes (`RenderTarget`'s layout).
const READBACK_FORMAT: MTLPixelFormat = MTLPixelFormat::RGBA8Unorm;

impl MetalRenderer {
    /// A renderer on the system's default device, for readback.
    pub(crate) fn new() -> Result<Self, MetalError> {
        let device = MTLCreateSystemDefaultDevice().ok_or(MetalError::NoDevice)?;
        let queue = device.newCommandQueue().ok_or(MetalError::NoDevice)?;
        Self::with(device, queue)
    }

    /// A renderer on the embedder's own device and queue.
    ///
    /// # Safety
    /// `device` must hold a live `id<MTLDevice>` and the `id<MTLCommandQueue>`
    /// the embedder renders with, created on it.
    pub(crate) unsafe fn import(device: &MetalDevice) -> Result<Self, MetalError> {
        let device_ptr = device.device as *mut ProtocolObject<dyn MTLDevice>;
        let queue_ptr = device.queue as *mut ProtocolObject<dyn MTLCommandQueue>;
        // SAFETY: live objects, per the caller; retained here, so they
        // outlive whatever the embedder does with its own references.
        let (device, queue) = unsafe {
            (
                Retained::retain(device_ptr).ok_or(MetalError::NoDevice)?,
                Retained::retain(queue_ptr).ok_or(MetalError::NoDevice)?,
            )
        };
        Self::with(device, queue)
    }

    fn with(device: Device, queue: Queue) -> Result<Self, MetalError> {
        autoreleasepool(|_| {
            let source = NSString::from_str(include_str!("shaders.metal"));
            let library = device
                .newLibraryWithSource_options_error(&source, None)
                .map_err(|e| MetalError::Shaders(e.localizedDescription().to_string()))?;
            let mut cache: *mut CVMetalTextureCache = std::ptr::null_mut();
            // SAFETY: a live device; `cache` receives a +1 reference.
            let status = unsafe {
                CVMetalTextureCache::create(None, None, &device, None, NonNull::from(&mut cache))
            };
            if status != kCVReturnSuccess {
                return Err(MetalError::NoDevice);
            }
            // SAFETY: created above with a +1 reference.
            let cache =
                unsafe { CFRetained::from_raw(NonNull::new(cache).ok_or(MetalError::NoDevice)?) };
            let tone_state = device
                .newBufferWithLength_options(
                    TONE_STATE_BYTES,
                    MTLResourceOptions::StorageModeShared,
                )
                .ok_or(MetalError::NoDevice)?;
            // SAFETY: a fresh shared buffer of TONE_STATE_BYTES, not yet in use.
            unsafe {
                tone_state
                    .contents()
                    .cast::<u8>()
                    .write_bytes(0, TONE_STATE_BYTES);
            }
            Ok(Self {
                device,
                queue,
                library,
                pipelines: HashMap::new(),
                measure: HashMap::new(),
                update_tone: None,
                tone_state,
                measured: false,
                cache,
                slots: Default::default(),
                next_slot: 0,
                readback: None,
                ring: Vec::new(),
                next: 0,
                converter: None,
            })
        })
    }

    /// Renders `source` into `target`, stretched to its size, and reads it
    /// back.
    pub(crate) fn render(
        &mut self,
        source: &frame::Video,
        target: &mut RenderTarget<'_>,
    ) -> Result<(), MetalError> {
        autoreleasepool(|_| {
            let (width, height) = (target.width, target.height);
            let (texture, buffer) = self.ensure_readback(width, height)?;
            let row = (width as usize * 4).next_multiple_of(256);
            let buffer_view = buffer.clone();
            let texture_view = texture.clone();
            let command = self.draw(source, &texture, READBACK_FORMAT, None, 8, |command| {
                let blit = command.blitCommandEncoder().ok_or(MetalError::Render)?;
                // SAFETY: the buffer holds `height` rows of `row` bytes
                // (ensure_readback), and the region is the texture's size.
                unsafe {
                    blit.copyFromTexture_sourceSlice_sourceLevel_sourceOrigin_sourceSize_toBuffer_destinationOffset_destinationBytesPerRow_destinationBytesPerImage(
                        &texture_view,
                        0,
                        0,
                        MTLOrigin { x: 0, y: 0, z: 0 },
                        MTLSize { width: width as usize, height: height as usize, depth: 1 },
                        &buffer_view,
                        0,
                        row,
                        row * height as usize,
                    );
                }
                blit.endEncoding();
                Ok(())
            })?;
            command.waitUntilCompleted();
            if command.status() != MTLCommandBufferStatus::Completed {
                return Err(MetalError::Render);
            }
            let bytes = buffer.contents().cast::<u8>();
            let copy = width as usize * 4;
            for y in 0..height as usize {
                // SAFETY: the buffer holds `height` rows of `row` bytes; the
                // target was checked (RenderTarget::check) to hold `height`
                // rows of `stride >= width * 4` bytes.
                unsafe {
                    std::ptr::copy_nonoverlapping(
                        bytes.as_ptr().add(y * row),
                        target.pixels.as_mut_ptr().add(y * target.stride),
                        copy,
                    );
                }
            }
            Ok(())
        })
    }

    /// Renders `source` at `width` x `height` into the next texture of the
    /// ring (Qt's device) and hands it over, with `overlay` on top.
    pub(crate) fn render_shared(
        &mut self,
        source: &frame::Video,
        width: u32,
        height: u32,
        overlay: Option<&OverlayImage<'_>>,
    ) -> Result<SharedImage, MetalError> {
        autoreleasepool(|_| {
            self.ensure_ring(width, height)?;
            let texture = self.ring[self.next].clone();
            self.next = (self.next + 1) % RING;
            self.draw(source, &texture, SHARED_FORMAT, overlay, 10, |_| Ok(()))?;
            Ok(SharedImage {
                image: Retained::as_ptr(&texture).cast::<c_void>() as u64,
                width,
                height,
            })
        })
    }

    /// Copies the texture `render_shared` last returned into `target`, which
    /// must be its size -- for checks of the zero-copy path. Its 10 bits per
    /// component are rounded to 8.
    pub(crate) fn read_last_shared(
        &mut self,
        target: &mut RenderTarget<'_>,
    ) -> Result<(), MetalError> {
        autoreleasepool(|_| {
            let texture =
                self.ring[(self.next + RING - 1) % RING.min(self.ring.len().max(1))].clone();
            let (width, height) = (texture.width(), texture.height());
            if (width, height) != (target.width as usize, target.height as usize) {
                return Err(MetalError::Target(target.width, target.height));
            }
            let row = (width * 4).next_multiple_of(256);
            let buffer = self
                .device
                .newBufferWithLength_options(row * height, MTLResourceOptions::StorageModeShared)
                .ok_or(MetalError::Target(target.width, target.height))?;
            let command = self.queue.commandBuffer().ok_or(MetalError::Render)?;
            let blit = command.blitCommandEncoder().ok_or(MetalError::Render)?;
            // SAFETY: the buffer holds `height` rows of `row` bytes.
            unsafe {
                blit.copyFromTexture_sourceSlice_sourceLevel_sourceOrigin_sourceSize_toBuffer_destinationOffset_destinationBytesPerRow_destinationBytesPerImage(
                    &texture,
                    0,
                    0,
                    MTLOrigin { x: 0, y: 0, z: 0 },
                    MTLSize { width, height, depth: 1 },
                    &buffer,
                    0,
                    row,
                    row * height,
                );
            }
            blit.endEncoding();
            command.commit();
            command.waitUntilCompleted();
            let words = buffer.contents().cast::<u32>();
            for y in 0..height {
                for x in 0..width {
                    // SAFETY: within the buffer's `height` rows of `row` bytes.
                    let word = unsafe { words.as_ptr().add(y * row / 4 + x).read() };
                    let pixel = y * target.stride + x * 4;
                    for c in 0..3 {
                        let ten = (word >> (10 * c)) & 0x3ff;
                        target.pixels[pixel + c] = ((ten * 255 + 511) / 1023) as u8;
                    }
                    target.pixels[pixel + 3] = 255;
                }
            }
            Ok(())
        })
    }

    /// Encodes and commits the pass that draws `source` into `target`;
    /// `then` may encode more into the same command buffer first.
    fn draw(
        &mut self,
        source: &frame::Video,
        target: &Texture,
        format: MTLPixelFormat,
        overlay: Option<&OverlayImage<'_>>,
        dither_bits: u32,
        then: impl FnOnce(&ProtocolObject<dyn MTLCommandBuffer>) -> Result<(), MetalError>,
    ) -> Result<CommandBuffer, MetalError> {
        let index = self.next_slot;
        self.next_slot = (self.next_slot + 1) % RING;
        self.slots[index].reclaim();

        let converted;
        let frame = if is_videotoolbox(source) || planes::samples_directly(frame_format(source)) {
            source
        } else {
            converted = self.convert(source)?;
            &converted
        };
        let planes = if is_videotoolbox(frame) {
            planes::videotoolbox(frame, &self.cache).ok_or(MetalError::Format)?
        } else {
            self.slots[index]
                .uploads
                .upload(&self.device, frame)
                .ok_or(MetalError::Format)?
        };
        let Planes {
            layout,
            colour,
            textures,
            keep,
        } = planes;
        let dovi = colour::dovi(frame);
        let plan = Plan::new(&colour, colour::hdr_metadata(frame), dovi);
        let key = PipelineKey {
            layout,
            transfer: plan.transfer as u32,
            dovi: plan.dovi.is_some(),
            tone_map: plan.tone.is_some(),
            overlay: overlay.is_some(),
            target: format,
        };
        let pipeline = self.pipeline(key)?;
        let overlay_texture = match overlay {
            Some(image) => Some(self.upload_overlay(index, image)?),
            None => None,
        };
        let measure = plan.tone.is_some_and(|t| t.metadata.is_none());
        let params = Params::new(&plan, dither_bits, !self.measured);
        let dovi_params = plan.dovi.as_deref().map(DoviParams::new);

        let command = self.queue.commandBuffer().ok_or(MetalError::Render)?;
        if plan.tone.is_some() {
            self.encode_tone(
                &command,
                key,
                measure,
                &params,
                dovi_params.as_deref(),
                &textures,
            )?;
        }
        self.measured = measure;
        let pass = MTLRenderPassDescriptor::new();
        // SAFETY: attachment 0 always exists on a render pass descriptor.
        let attachment = unsafe { pass.colorAttachments().objectAtIndexedSubscript(0) };
        attachment.setTexture(Some(target));
        attachment.setLoadAction(MTLLoadAction::DontCare);
        attachment.setStoreAction(MTLStoreAction::Store);
        let encoder = command
            .renderCommandEncoderWithDescriptor(&pass)
            .ok_or(MetalError::Render)?;
        encoder.setRenderPipelineState(&pipeline);
        // SAFETY: the bytes are copied by Metal before the call returns;
        // the textures and buffer are live and belong to this device.
        unsafe {
            encoder.setFragmentBytes_length_atIndex(
                NonNull::from(&params).cast(),
                size_of::<Params>(),
                0,
            );
            if let Some(packed) = &dovi_params {
                encoder.setFragmentBytes_length_atIndex(
                    NonNull::from(&**packed).cast(),
                    size_of::<DoviParams>(),
                    1,
                );
            }
            if plan.tone.is_some() {
                encoder.setFragmentBuffer_offset_atIndex(Some(&self.tone_state), 0, 2);
            }
            for (slot, texture) in textures.iter().enumerate() {
                encoder.setFragmentTexture_atIndex(Some(texture), slot);
            }
            if let Some(texture) = &overlay_texture {
                encoder.setFragmentTexture_atIndex(Some(texture), 3);
            }
            encoder.drawPrimitives_vertexStart_vertexCount(MTLPrimitiveType::Triangle, 0, 3);
        }
        encoder.endEncoding();
        then(&command)?;
        command.commit();
        let slot = &mut self.slots[index];
        slot.keep = keep;
        slot.pending = Some(command.clone());
        Ok(command)
    }

    /// The HDR passes: measure this frame's brightness (unless dynamic
    /// metadata states it), then derive the tone curve the draw reads.
    fn encode_tone(
        &mut self,
        command: &ProtocolObject<dyn MTLCommandBuffer>,
        key: PipelineKey,
        measure: bool,
        params: &Params,
        dovi: Option<&DoviParams>,
        textures: &[Texture],
    ) -> Result<(), MetalError> {
        let measure_kernel = if measure {
            Some(self.measure_kernel(key)?)
        } else {
            None
        };
        let update = self.update_kernel(key)?;
        let encoder = command.computeCommandEncoder().ok_or(MetalError::Render)?;
        // SAFETY: the bytes are copied before each call returns; textures and
        // buffer are live on this device; the grids cover the frame.
        unsafe {
            encoder.setBytes_length_atIndex(NonNull::from(params).cast(), size_of::<Params>(), 0);
            if let Some(dovi) = dovi {
                encoder.setBytes_length_atIndex(
                    NonNull::from(dovi).cast(),
                    size_of::<DoviParams>(),
                    1,
                );
            }
            encoder.setBuffer_offset_atIndex(Some(&self.tone_state), 0, 2);
            if let Some(kernel) = &measure_kernel {
                for (slot, texture) in textures.iter().enumerate() {
                    encoder.setTexture_atIndex(Some(texture), slot);
                }
                encoder.setComputePipelineState(kernel);
                let (width, height) = (textures[0].width(), textures[0].height());
                encoder.dispatchThreadgroups_threadsPerThreadgroup(
                    MTLSize {
                        width: width.div_ceil(16),
                        height: height.div_ceil(16),
                        depth: 1,
                    },
                    MTLSize {
                        width: 16,
                        height: 16,
                        depth: 1,
                    },
                );
            }
            encoder.setComputePipelineState(&update);
            let one = MTLSize {
                width: 1,
                height: 1,
                depth: 1,
            };
            encoder.dispatchThreadgroups_threadsPerThreadgroup(one, one);
        }
        encoder.endEncoding();
        Ok(())
    }

    /// The shader's function constants for `key`.
    fn constants(key: PipelineKey) -> Retained<MTLFunctionConstantValues> {
        let constants = MTLFunctionConstantValues::new();
        let layout: i32 = match key.layout {
            Layout::Planar => 0,
            Layout::SemiPlanar => 1,
            Layout::Packed => 2,
        };
        let transfer = key.transfer as i32;
        let flags = [key.dovi, key.tone_map, key.overlay];
        // SAFETY: each value is the type named, read during the call.
        unsafe {
            constants.setConstantValue_type_atIndex(
                NonNull::from(&layout).cast(),
                MTLDataType::Int,
                0,
            );
            constants.setConstantValue_type_atIndex(
                NonNull::from(&transfer).cast(),
                MTLDataType::Int,
                1,
            );
            for (i, flag) in flags.iter().enumerate() {
                constants.setConstantValue_type_atIndex(
                    NonNull::from(flag).cast(),
                    MTLDataType::Bool,
                    2 + i,
                );
            }
        }
        constants
    }

    fn function(
        &self,
        name: &str,
        key: PipelineKey,
    ) -> Result<Retained<ProtocolObject<dyn objc2_metal::MTLFunction>>, MetalError> {
        self.library
            .newFunctionWithName_constantValues_error(
                &NSString::from_str(name),
                &Self::constants(key),
            )
            .map_err(shader_error)
    }

    fn pipeline(&mut self, key: PipelineKey) -> Result<Pipeline, MetalError> {
        if let Some(pipeline) = self.pipelines.get(&key) {
            return Ok(pipeline.clone());
        }
        let descriptor = MTLRenderPipelineDescriptor::new();
        let (vertex, fragment) = (
            self.function("video_vertex", key)?,
            self.function("video_fragment", key)?,
        );
        descriptor.setVertexFunction(Some(&vertex));
        descriptor.setFragmentFunction(Some(&fragment));
        // SAFETY: attachment 0 always exists on a pipeline descriptor.
        unsafe { descriptor.colorAttachments().objectAtIndexedSubscript(0) }
            .setPixelFormat(key.target);
        let pipeline = self
            .device
            .newRenderPipelineStateWithDescriptor_error(&descriptor)
            .map_err(shader_error)?;
        self.pipelines.insert(key, pipeline.clone());
        Ok(pipeline)
    }

    fn measure_kernel(&mut self, key: PipelineKey) -> Result<Kernel, MetalError> {
        let id = (key.layout, key.transfer, key.dovi);
        if let Some(kernel) = self.measure.get(&id) {
            return Ok(kernel.clone());
        }
        let function = self.function("measure_peak", key)?;
        let kernel = self
            .device
            .newComputePipelineStateWithFunction_error(&function)
            .map_err(shader_error)?;
        self.measure.insert(id, kernel.clone());
        Ok(kernel)
    }

    fn update_kernel(&mut self, key: PipelineKey) -> Result<Kernel, MetalError> {
        if let Some(kernel) = &self.update_tone {
            return Ok(kernel.clone());
        }
        let function = self.function("update_tone", key)?;
        let kernel = self
            .device
            .newComputePipelineStateWithFunction_error(&function)
            .map_err(shader_error)?;
        self.update_tone = Some(kernel.clone());
        Ok(kernel)
    }

    fn upload_overlay(
        &mut self,
        index: usize,
        image: &OverlayImage<'_>,
    ) -> Result<Texture, MetalError> {
        let (width, height) = (image.width as usize, image.height as usize);
        let slot = &mut self.slots[index];
        let fits = slot
            .overlay
            .as_ref()
            .is_some_and(|t| t.width() == width && t.height() == height);
        if !fits {
            slot.overlay = Some(
                planes::new_texture(
                    &self.device,
                    MTLPixelFormat::RGBA8Unorm,
                    width,
                    height,
                    MTLTextureUsage::ShaderRead,
                )
                .ok_or(MetalError::Target(image.width, image.height))?,
            );
        }
        let texture = slot.overlay.clone().ok_or(MetalError::Render)?;
        let region = objc2_metal::MTLRegion {
            origin: MTLOrigin { x: 0, y: 0, z: 0 },
            size: MTLSize {
                width,
                height,
                depth: 1,
            },
        };
        let data = NonNull::new(image.pixels.as_ptr().cast_mut().cast::<c_void>())
            .ok_or(MetalError::Render)?;
        // SAFETY: the overlay holds `height` rows of `width * 4` bytes; the
        // slot was reclaimed, so the GPU no longer reads this texture.
        unsafe {
            texture.replaceRegion_mipmapLevel_withBytes_bytesPerRow(region, 0, data, width * 4);
        }
        Ok(texture)
    }

    fn ensure_readback(
        &mut self,
        width: u32,
        height: u32,
    ) -> Result<(Texture, Retained<ProtocolObject<dyn MTLBuffer>>), MetalError> {
        if let Some((texture, buffer, w, h)) = &self.readback
            && (*w, *h) == (width, height)
        {
            return Ok((texture.clone(), buffer.clone()));
        }
        let texture = planes::new_texture(
            &self.device,
            READBACK_FORMAT,
            width as usize,
            height as usize,
            MTLTextureUsage::RenderTarget,
        )
        .ok_or(MetalError::Target(width, height))?;
        let row = (width as usize * 4).next_multiple_of(256);
        let buffer = self
            .device
            .newBufferWithLength_options(
                row * height as usize,
                MTLResourceOptions::StorageModeShared,
            )
            .ok_or(MetalError::Target(width, height))?;
        self.readback = Some((texture.clone(), buffer.clone(), width, height));
        Ok((texture, buffer))
    }

    /// A ring of `width` x `height` textures. A replaced ring is simply
    /// dropped: the embedder retains every texture it wraps for as long as
    /// its scene graph may sample it.
    fn ensure_ring(&mut self, width: u32, height: u32) -> Result<(), MetalError> {
        let current = self.ring.first().map(|t| (t.width(), t.height()));
        if current == Some((width as usize, height as usize)) {
            return Ok(());
        }
        self.ring.clear();
        self.next = 0;
        for _ in 0..RING {
            let texture = planes::new_texture(
                &self.device,
                SHARED_FORMAT,
                width as usize,
                height as usize,
                MTLTextureUsage::RenderTarget | MTLTextureUsage::ShaderRead,
            )
            .ok_or(MetalError::Target(width, height))?;
            self.ring.push(texture);
        }
        Ok(())
    }

    fn convert(&mut self, source: &frame::Video) -> Result<frame::Video, MetalError> {
        let target = planes::conversion_target(frame_format(source));
        let converter = self.converter.get_or_insert_with(Converter::default);
        converter.convert(source, target).ok_or(MetalError::Format)
    }
}

impl Drop for MetalRenderer {
    fn drop(&mut self) {
        // Nothing may still be reading what goes with the renderer.
        for slot in &mut self.slots {
            slot.reclaim();
        }
    }
}

#[allow(clippy::needless_pass_by_value, reason = "used as a map_err adapter")]
fn shader_error(error: Retained<objc2_foundation::NSError>) -> MetalError {
    MetalError::Shaders(error.localizedDescription().to_string())
}

fn frame_format(frame: &frame::Video) -> ffi::AVPixelFormat {
    // SAFETY: a decoded frame's format field holds an AVPixelFormat.
    unsafe { std::mem::transmute::<i32, ffi::AVPixelFormat>((*frame.as_ptr()).format) }
}

fn is_videotoolbox(frame: &frame::Video) -> bool {
    frame_format(frame) == ffi::AVPixelFormat::AV_PIX_FMT_VIDEOTOOLBOX
}

/// A cached swscale context for formats the shader does not sample.
#[derive(Default)]
struct Converter {
    context: Option<(
        ffi::AVPixelFormat,
        u32,
        u32,
        ffi::AVPixelFormat,
        ffmpeg_next::software::scaling::Context,
    )>,
}

impl std::fmt::Debug for Converter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Converter").finish_non_exhaustive()
    }
}

impl Converter {
    fn convert(
        &mut self,
        source: &frame::Video,
        target: ffi::AVPixelFormat,
    ) -> Option<frame::Video> {
        let format = frame_format(source);
        let (width, height) = (source.width(), source.height());
        let fits = self
            .context
            .as_ref()
            .is_some_and(|(f, w, h, t, _)| (*f, *w, *h, *t) == (format, width, height, target));
        if !fits {
            let context = ffmpeg_next::software::scaling::Context::get(
                format.into(),
                width,
                height,
                target.into(),
                width,
                height,
                ffmpeg_next::software::scaling::Flags::POINT,
            )
            .ok()?;
            self.context = Some((format, width, height, target, context));
        }
        let (.., context) = self.context.as_mut()?;
        let mut out = frame::Video::empty();
        context.run(source, &mut out).ok()?;
        // SAFETY: both frames are valid; the tags go across with the picture.
        unsafe { ffi::av_frame_copy_props(out.as_mut_ptr(), source.as_ptr()) };
        Some(out)
    }
}

#[cfg(test)]
mod tests {
    use ffmpeg_next::color::{Primaries, Range, Space, TransferCharacteristic};
    use ffmpeg_next::format::Pixel;

    use super::*;
    use crate::gpu::GpuRenderer;

    const WIDTH: u32 = 64;
    const HEIGHT: u32 = 36;

    fn renderer() -> Option<MetalRenderer> {
        MetalRenderer::new()
            .inspect_err(|error| eprintln!("skipped: {error}"))
            .ok()
    }

    /// A frame of vertical stripes, one per `(y, cb, cr)` code triple.
    fn stripes(format: Pixel, codes: &[[u16; 3]]) -> frame::Video {
        let mut frame = frame::Video::new(format, WIDTH, HEIGHT);
        let wide = frame.format() != Pixel::YUV420P && frame.format() != Pixel::YUV444P;
        let chroma_shift = u32::from(matches!(format, Pixel::YUV420P | Pixel::YUV420P10LE));
        for plane in 0..3 {
            let stride = frame.stride(plane);
            let width = if plane == 0 {
                WIDTH
            } else {
                WIDTH >> chroma_shift
            };
            let height = if plane == 0 {
                HEIGHT
            } else {
                HEIGHT >> chroma_shift
            };
            let data = frame.data_mut(plane);
            for y in 0..height as usize {
                for x in 0..width as usize {
                    let band = x * codes.len() / width as usize;
                    let value = codes[band][plane];
                    if wide {
                        let at = y * stride + x * 2;
                        data[at..at + 2].copy_from_slice(&value.to_le_bytes());
                    } else {
                        data[y * stride + x] = value as u8;
                    }
                }
            }
        }
        frame
    }

    fn render_metal(renderer: &mut MetalRenderer, frame: &frame::Video) -> Vec<u8> {
        let mut pixels = vec![0u8; (WIDTH * HEIGHT * 4) as usize];
        let mut target = RenderTarget {
            pixels: &mut pixels,
            width: WIDTH,
            height: HEIGHT,
            stride: WIDTH as usize * 4,
        };
        renderer.render(frame, &mut target).expect("renders");
        pixels
    }

    fn render_reference(reference: &mut GpuRenderer, frame: &frame::Video) -> Vec<u8> {
        let mut pixels = vec![0u8; (WIDTH * HEIGHT * 4) as usize];
        let mut target = RenderTarget {
            pixels: &mut pixels,
            width: WIDTH,
            height: HEIGHT,
            stride: WIDTH as usize * 4,
        };
        reference.render(frame, &mut target).expect("renders");
        pixels
    }

    /// Each stripe's mean colour, away from its edges (where chroma and
    /// scaling blend neighbours) -- dithering averages out.
    fn stripe_means(pixels: &[u8], count: usize) -> Vec<[f64; 3]> {
        let width = WIDTH as usize;
        (0..count)
            .map(|stripe| {
                let (from, to) = (stripe * width / count + 2, (stripe + 1) * width / count - 2);
                let mut sum = [0.0; 3];
                let mut n = 0.0;
                for y in 4..HEIGHT as usize - 4 {
                    for x in from..to {
                        for c in 0..3 {
                            sum[c] += f64::from(pixels[(y * width + x) * 4 + c]);
                        }
                        n += 1.0;
                    }
                }
                sum.map(|v| v / n)
            })
            .collect()
    }

    fn tag(
        frame: &mut frame::Video,
        space: Space,
        range: Range,
        primaries: Primaries,
        trc: TransferCharacteristic,
    ) {
        frame.set_color_space(space);
        frame.set_color_range(range);
        frame.set_color_primaries(primaries);
        frame.set_color_transfer_characteristic(trc);
    }

    /// Renders `frame` with both renderers and returns the largest
    /// difference between their stripe means, in 8-bit steps.
    fn compare(name: &str, frame: &frame::Video, count: usize) -> Option<f64> {
        let (Some(mut metal), Some(mut reference)) = (renderer(), GpuRenderer::reference()) else {
            return None;
        };
        let ours = stripe_means(&render_metal(&mut metal, frame), count);
        let theirs = stripe_means(&render_reference(&mut reference, frame), count);
        let mut worst: f64 = 0.0;
        for (i, (a, b)) in ours.iter().zip(&theirs).enumerate() {
            let diff = (0..3).map(|c| (a[c] - b[c]).abs()).fold(0.0, f64::max);
            eprintln!("{name} stripe {i}: metal {a:.1?} libplacebo {b:.1?} (max diff {diff:.2})");
            worst = worst.max(diff);
        }
        Some(worst)
    }

    #[test]
    fn sdr_white_stays_white() {
        let Some(mut renderer) = renderer() else {
            return;
        };
        let mut white = stripes(Pixel::YUV420P, &[[235, 128, 128]]);
        tag(
            &mut white,
            Space::BT709,
            Range::MPEG,
            Primaries::BT709,
            TransferCharacteristic::BT709,
        );
        let pixels = render_metal(&mut renderer, &white);
        assert!(
            pixels
                .as_chunks::<4>()
                .0
                .iter()
                .all(|p| p[0] > 250 && p[1] > 250 && p[2] > 250)
        );
    }

    #[test]
    fn hdr_is_tone_mapped_rather_than_read_as_sdr() {
        let Some(mut renderer) = renderer() else {
            return;
        };
        // 10-bit PQ at about 100 nits (code ~520): SDR white, not mid grey.
        let mut hdr = stripes(Pixel::YUV420P10LE, &[[520, 512, 512]]);
        tag(
            &mut hdr,
            Space::BT2020NCL,
            Range::MPEG,
            Primaries::BT2020,
            TransferCharacteristic::SMPTE2084,
        );
        let pixels = render_metal(&mut renderer, &hdr);
        assert!(
            pixels[0] > 170,
            "100-nit PQ white rendered as {:?}",
            &pixels[..4]
        );
    }

    #[test]
    fn sdr_colours_match_libplacebo() {
        // BT.709 limited: black, grey ramp, white, red, green, blue, a skin
        // tone.
        let codes = [
            [16, 128, 128],
            [64, 128, 128],
            [126, 128, 128],
            [180, 128, 128],
            [235, 128, 128],
            [63, 102, 240],
            [173, 42, 26],
            [32, 240, 118],
            [150, 110, 150],
        ];
        let mut frame = stripes(Pixel::YUV444P, &codes);
        tag(
            &mut frame,
            Space::BT709,
            Range::MPEG,
            Primaries::BT709,
            TransferCharacteristic::BT709,
        );
        if let Some(worst) = compare("sdr709", &frame, codes.len()) {
            assert!(worst < 1.5, "differs by {worst:.2} steps");
        }
    }

    #[test]
    fn hdr10_is_mapped_by_bt2390_and_stays_near_libplacebo() {
        // PQ, BT.2020, limited 10-bit: greys at ~10, 100, 400, 1000 nits and
        // saturated colours.
        let codes = [
            [300, 512, 512],
            [520, 512, 512],
            [668, 512, 512],
            [769, 512, 512],
            [400, 380, 700],
            [500, 300, 400],
            [300, 750, 450],
        ];
        let mut frame = stripes(Pixel::YUV444P10LE, &codes);
        tag(
            &mut frame,
            Space::BT2020NCL,
            Range::MPEG,
            Primaries::BT2020,
            TransferCharacteristic::SMPTE2084,
        );
        let Some(mut metal) = renderer() else {
            return;
        };
        let ours = stripe_means(&render_metal(&mut metal, &frame), codes.len());
        // Greys stay grey and in order; the scene's peak lands on white.
        for pair in ours[..4].windows(2) {
            assert!(pair[0][0] < pair[1][0], "{ours:.1?}");
        }
        assert!(
            ours[..4]
                .iter()
                .all(|g| (g[0] - g[1]).abs() < 1.0 && (g[1] - g[2]).abs() < 1.0)
        );
        assert!(ours[3][0] > 254.0, "{ours:.1?}");
        // ~113 nits (code 520) sits just above BT.2390's knee here, and
        // SDR white is 203 nits (BT.2408): close to 113/203 of white's
        // linear light, 197 as sRGB.
        assert!((180.0..200.0).contains(&ours[1][0]), "{ours:.1?}");
        // Saturated colours keep their hue: the dominant channel stays
        // dominant.
        for (stripe, channel) in [(4, 0), (5, 1), (6, 2)] {
            let c = ours[stripe];
            let others = (0..3)
                .filter(|&i| i != channel)
                .map(|i| c[i])
                .fold(0.0, f64::max);
            assert!(c[channel] > others + 100.0, "stripe {stripe}: {c:.1?}");
        }
        // The same frame through libplacebo, for the record: its own curve
        // and gamut mapping, not a standard, so only printed.
        compare("hdr10", &frame, codes.len());
    }

    fn hdr10(codes: &[[u16; 3]]) -> frame::Video {
        let mut frame = stripes(Pixel::YUV444P10LE, codes);
        tag(
            &mut frame,
            Space::BT2020NCL,
            Range::MPEG,
            Primaries::BT2020,
            TransferCharacteristic::SMPTE2084,
        );
        frame
    }

    /// Dolby Vision metadata that decodes exactly as BT.2020 limited-range
    /// PQ does: no reshaping, the BT.2020 matrix, and an LMS step that
    /// undoes the fixed one.
    fn identity_dovi() -> colour::Dovi {
        let depth = 10;
        let frame = colour::FrameColour {
            width: WIDTH,
            height: HEIGHT,
            space: ffi::AVColorSpace::AVCOL_SPC_BT2020_NCL,
            range: ffi::AVColorRange::AVCOL_RANGE_MPEG,
            primaries: ffi::AVColorPrimaries::AVCOL_PRI_BT2020,
            transfer: ffi::AVColorTransferCharacteristic::AVCOL_TRC_SMPTE2084,
            chroma_location: ffi::AVChromaLocation::AVCHROMA_LOC_LEFT,
            depth,
            texture_bits: 16,
            shift: 0,
            chroma_shift: (0, 0),
            rgb: false,
        };
        let (matrix, _) = colour::ycbcr_decode(&frame, frame.space, depth);
        let linear = colour::invert(&colour::dovi_lms_to_rgb());
        let flat = |m: &Mat3| {
            let mut out = [0.0f32; 9];
            for (i, v) in out.iter_mut().enumerate() {
                *v = m[i / 3][i % 3] as f32;
            }
            out
        };
        let mut dovi = colour::Dovi {
            nonlinear: flat(&matrix),
            nonlinear_offset: [16.0 / 256.0, 128.0 / 256.0, 128.0 / 256.0],
            linear: flat(&linear),
            source_max_pq: 0.75,
            bl_bit_depth: 10,
            ..Default::default()
        };
        for curve in &mut dovi.comp {
            curve.num_pivots = 2;
            curve.pivots[1] = 1.0;
            curve.poly[0] = [0.0, 1.0, 0.0];
        }
        dovi
    }

    const DV_CODES: [[u16; 3]; 6] = [
        [200, 512, 512],
        [420, 512, 512],
        [600, 512, 512],
        [450, 400, 640],
        [520, 330, 420],
        [380, 700, 480],
    ];

    #[test]
    fn dolby_vision_without_reshaping_decodes_like_hdr10() {
        let Some(mut metal) = renderer() else {
            return;
        };
        let plain = stripe_means(&render_metal(&mut metal, &hdr10(&DV_CODES)), DV_CODES.len());
        let mut dv = hdr10(&DV_CODES);
        assert!(colour::set_dovi(&mut dv, &identity_dovi()));
        let Some(mut metal) = renderer() else {
            return;
        };
        let reshaped = stripe_means(&render_metal(&mut metal, &dv), DV_CODES.len());
        for (a, b) in plain.iter().zip(&reshaped) {
            for c in 0..3 {
                assert!((a[c] - b[c]).abs() < 0.5, "{plain:.1?} vs {reshaped:.1?}");
            }
        }
    }

    #[test]
    fn dolby_vision_reshaping_follows_the_rpu() {
        // Luma: two polynomial pieces. Cb: second-order MMR over all three
        // components. Cr: one polynomial.
        let mut dovi = identity_dovi();
        let luma = &mut dovi.comp[0];
        luma.num_pivots = 3;
        luma.pivots[..3].copy_from_slice(&[0.0, 0.5, 1.0]);
        luma.poly[0] = [0.05, 0.7, 0.3];
        luma.poly[1] = [0.1, 0.6, 0.25];
        let cb = &mut dovi.comp[1];
        cb.method[0] = 1;
        cb.mmr_order[0] = 2;
        cb.mmr_constant[0] = 0.05;
        cb.mmr[0][0] = [-0.1, 0.8, 0.1, 0.2, -0.1, 0.05, 0.02];
        cb.mmr[0][1] = [0.03, 0.05, -0.04, 0.0, 0.02, -0.05, 0.01];
        dovi.comp[2].poly[0] = [0.03, 0.9, 0.0];

        // The same arithmetic on the CPU, straight from the curves' definition.
        let reshape = |codes: [u16; 3]| -> [u16; 3] {
            let s = codes.map(|c| f64::from(c) / 1023.0);
            let y = if s[0] < 0.5 {
                0.05 + 0.7 * s[0] + 0.3 * s[0] * s[0]
            } else {
                0.1 + 0.6 * s[0] + 0.25 * s[0] * s[0]
            };
            let terms = [
                s[0],
                s[1],
                s[2],
                s[0] * s[1],
                s[0] * s[2],
                s[1] * s[2],
                s[0] * s[1] * s[2],
            ];
            let first = [-0.1, 0.8, 0.1, 0.2, -0.1, 0.05, 0.02];
            let second = [0.03, 0.05, -0.04, 0.0, 0.02, -0.05, 0.01];
            let cb = 0.05
                + (0..7)
                    .map(|k| first[k] * terms[k] + second[k] * terms[k] * terms[k])
                    .sum::<f64>();
            let cr = 0.03 + 0.9 * s[2];
            [y, cb, cr].map(|v| (v.clamp(0.0, 1.0) * 1023.0).round() as u16)
        };
        let expected: Vec<[u16; 3]> = DV_CODES.iter().map(|&c| reshape(c)).collect();

        let Some(mut metal) = renderer() else {
            return;
        };
        let want = stripe_means(&render_metal(&mut metal, &hdr10(&expected)), DV_CODES.len());
        let mut dv = hdr10(&DV_CODES);
        assert!(colour::set_dovi(&mut dv, &dovi));
        let Some(mut metal) = renderer() else {
            return;
        };
        let got = stripe_means(&render_metal(&mut metal, &dv), DV_CODES.len());
        // The reshaping must matter, or this proves nothing.
        let Some(mut metal) = renderer() else {
            return;
        };
        let unshaped = stripe_means(&render_metal(&mut metal, &hdr10(&DV_CODES)), DV_CODES.len());
        let moved = want
            .iter()
            .zip(&unshaped)
            .map(|(a, b)| (0..3).map(|c| (a[c] - b[c]).abs()).fold(0.0, f64::max))
            .fold(0.0, f64::max);
        assert!(
            moved > 8.0,
            "the curves barely change the picture ({moved:.1})"
        );
        for (a, b) in want.iter().zip(&got) {
            for c in 0..3 {
                // The expected codes are rounded to 10 bits (half a code
                // is up to ~1% of luminance through PQ at these levels),
                // and the peak each picture measures differs as slightly:
                // 2.1 steps at worst, measured.
                assert!((a[c] - b[c]).abs() < 2.5, "{want:.1?} vs {got:.1?}");
            }
        }
    }

    #[test]
    fn a_resize_rebuilds_the_target() {
        let Some(mut renderer) = renderer() else {
            return;
        };
        let frame = stripes(Pixel::YUV420P, &[[128, 128, 128]]);
        let mut pixels = vec![0u8; 48 * 27 * 4];
        let mut target = RenderTarget {
            pixels: &mut pixels,
            width: 48,
            height: 27,
            stride: 48 * 4,
        };
        render_metal(&mut renderer, &frame);
        renderer
            .render(&frame, &mut target)
            .expect("renders at the new size");
    }
}
