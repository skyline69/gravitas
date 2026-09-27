//! A decoded frame as the textures the shader samples.
//!
//! Two kinds of frame arrive. VideoToolbox's are `CVPixelBuffer`s backed by
//! IOSurfaces the GPU already holds, and the texture cache wraps each plane
//! as an `MTLTexture` without a copy: decoding, rendering and the scene graph
//! all touch the same memory. Software frames are copied into textures the
//! renderer keeps -- one `memcpy` per plane on Apple silicon's unified
//! memory. A software format the shader has no layout for goes through
//! swscale first, keeping its colour representation (YUV stays YUV).

use std::ffi::c_void;
use std::ptr::{self, NonNull};

use ffmpeg_next::{ffi, frame};
use objc2::rc::Retained;
use objc2::runtime::ProtocolObject;
use objc2_core_foundation::CFRetained;
use objc2_core_video::{
    CVMetalTexture, CVMetalTextureCache, CVMetalTextureGetTexture, CVPixelBuffer,
    CVPixelBufferGetHeightOfPlane, CVPixelBufferGetPixelFormatType, CVPixelBufferGetWidthOfPlane,
    kCVReturnSuccess,
};
use objc2_metal::{
    MTLDevice, MTLOrigin, MTLPixelFormat, MTLRegion, MTLSize, MTLStorageMode, MTLTexture,
    MTLTextureDescriptor, MTLTextureUsage,
};

use super::colour::{FrameColour, Layout};

pub(crate) type Texture = Retained<ProtocolObject<dyn MTLTexture>>;

/// One plane's shape: its texture format and size in texels.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct PlaneShape {
    format: MTLPixelFormat,
    width: usize,
    height: usize,
}

/// What a frame is, for the shader: its layout, its colour and the textures.
#[derive(Debug)]
pub(crate) struct Planes {
    pub(crate) layout: Layout,
    pub(crate) colour: FrameColour,
    pub(crate) textures: Vec<Texture>,
    /// VideoToolbox wrappers: each keeps its `CVPixelBuffer` (and so the
    /// decoder's surface) alive, and must until the GPU is done reading.
    pub(crate) keep: Vec<CFRetained<CVMetalTexture>>,
}

/// Textures a software frame is copied into, reused while the shape holds.
#[derive(Debug, Default)]
pub(crate) struct Uploads {
    textures: Vec<(PlaneShape, Texture)>,
}

// `kCVPixelFormatType_*` for what VideoToolbox decodes to: two planes, luma
// then interleaved chroma, 8-bit or 10-bit at the top of 16.
const CV_420V: u32 = u32::from_be_bytes(*b"420v");
const CV_420F: u32 = u32::from_be_bytes(*b"420f");
const CV_422V: u32 = u32::from_be_bytes(*b"422v");
const CV_444V: u32 = u32::from_be_bytes(*b"444v");
const CV_X420: u32 = u32::from_be_bytes(*b"x420");
const CV_XF20: u32 = u32::from_be_bytes(*b"xf20");
const CV_X422: u32 = u32::from_be_bytes(*b"x422");
const CV_X444: u32 = u32::from_be_bytes(*b"x444");

/// `frame`'s tags with the shape of its storage filled in.
fn colour_of(
    frame: &frame::Video,
    depth: u32,
    texture_bits: u32,
    shift: u32,
    chroma: (u32, u32),
    rgb: bool,
) -> FrameColour {
    // SAFETY: a valid decoded frame.
    let raw = unsafe { &*frame.as_ptr() };
    FrameColour {
        width: raw.width.max(0) as u32,
        height: raw.height.max(0) as u32,
        space: raw.colorspace,
        range: raw.color_range,
        primaries: raw.color_primaries,
        transfer: raw.color_trc,
        chroma_location: raw.chroma_location,
        depth,
        texture_bits,
        shift,
        chroma_shift: chroma,
        rgb,
    }
}

/// Wraps a VideoToolbox frame's planes; None for a surface format the
/// shader does not know (the caller copies it to memory instead).
pub(crate) fn videotoolbox(frame: &frame::Video, cache: &CVMetalTextureCache) -> Option<Planes> {
    // SAFETY: a VideoToolbox frame carries its CVPixelBufferRef in data[3].
    let buffer = unsafe { (*frame.as_ptr()).data[3] }.cast::<CVPixelBuffer>();
    // SAFETY: non-null, and alive for as long as the frame is.
    let buffer = unsafe { buffer.as_ref() }?;
    let (depth, wide) = match CVPixelBufferGetPixelFormatType(buffer) {
        CV_420V | CV_420F | CV_422V | CV_444V => (8, false),
        CV_X420 | CV_XF20 | CV_X422 | CV_X444 => (10, true),
        _ => return None,
    };
    let (luma, chroma) = if wide {
        (MTLPixelFormat::R16Unorm, MTLPixelFormat::RG16Unorm)
    } else {
        (MTLPixelFormat::R8Unorm, MTLPixelFormat::RG8Unorm)
    };
    let mut textures = Vec::with_capacity(2);
    let mut keep = Vec::with_capacity(2);
    let mut sizes = [(0, 0); 2];
    for (plane, format) in [(0, luma), (1, chroma)] {
        let width = CVPixelBufferGetWidthOfPlane(buffer, plane);
        let height = CVPixelBufferGetHeightOfPlane(buffer, plane);
        sizes[plane] = (width, height);
        let mut out: *mut CVMetalTexture = ptr::null_mut();
        // SAFETY: a live cache and buffer; `out` receives a +1 reference.
        let status = unsafe {
            CVMetalTextureCache::create_texture_from_image(
                None,
                cache,
                buffer,
                None,
                format,
                width,
                height,
                plane,
                NonNull::from(&mut out),
            )
        };
        if status != kCVReturnSuccess {
            return None;
        }
        // SAFETY: created above with a +1 reference, released by the wrapper.
        let wrapper = unsafe { CFRetained::from_raw(NonNull::new(out)?) };
        textures.push(CVMetalTextureGetTexture(&wrapper)?);
        keep.push(wrapper);
    }
    let shift_of = |luma: usize, chroma: usize| u32::from(chroma < luma);
    let chroma_shift = (
        shift_of(sizes[0].0, sizes[1].0),
        shift_of(sizes[0].1, sizes[1].1),
    );
    let texture_bits = if wide { 16 } else { 8 };
    let shift = if wide { 6 } else { 0 };
    Some(Planes {
        layout: Layout::SemiPlanar,
        colour: colour_of(frame, depth, texture_bits, shift, chroma_shift, false),
        textures,
        keep,
    })
}

/// How a software format maps onto textures, if the shader samples it as
/// it is.
struct SoftwareLayout {
    layout: Layout,
    /// Plane index for each texture slot, in component order.
    planes: Vec<usize>,
    formats: Vec<MTLPixelFormat>,
    depth: u32,
    wide: bool,
    shift: u32,
    chroma: (u32, u32),
    rgb: bool,
}

fn software_layout(format: ffi::AVPixelFormat) -> Option<SoftwareLayout> {
    // SAFETY: av_pix_fmt_desc_get returns a static descriptor or null.
    let desc = unsafe { ffi::av_pix_fmt_desc_get(format).as_ref() }?;
    let unsupported = ffi::AV_PIX_FMT_FLAG_BE
        | ffi::AV_PIX_FMT_FLAG_PAL
        | ffi::AV_PIX_FMT_FLAG_BITSTREAM
        | ffi::AV_PIX_FMT_FLAG_HWACCEL
        | ffi::AV_PIX_FMT_FLAG_FLOAT;
    if desc.flags & unsupported as u64 != 0 || desc.nb_components < 3 {
        return None;
    }
    let comps = &desc.comp[..3];
    let depth = comps[0].depth as u32;
    if comps.iter().any(|c| c.depth as u32 != depth) || depth > 16 {
        return None;
    }
    let wide = depth > 8;
    let bytes = if wide { 2 } else { 1 };
    let shift = comps[0].shift as u32;
    let rgb = desc.flags & ffi::AV_PIX_FMT_FLAG_RGB as u64 != 0;
    let chroma = (u32::from(desc.log2_chroma_w), u32::from(desc.log2_chroma_h));
    let (one, two) = if wide {
        (MTLPixelFormat::R16Unorm, MTLPixelFormat::RG16Unorm)
    } else {
        (MTLPixelFormat::R8Unorm, MTLPixelFormat::RG8Unorm)
    };
    let planar = desc.flags & ffi::AV_PIX_FMT_FLAG_PLANAR as u64 != 0;
    let plane = |i: usize| comps[i].plane as usize;
    if planar {
        let distinct = plane(0) != plane(1) && plane(1) != plane(2) && plane(0) != plane(2);
        if distinct && comps.iter().all(|c| c.step == bytes) {
            return Some(SoftwareLayout {
                layout: Layout::Planar,
                planes: vec![plane(0), plane(1), plane(2)],
                formats: vec![one; 3],
                depth,
                wide,
                shift,
                chroma,
                rgb,
            });
        }
        let semi = !rgb
            && plane(0) == 0
            && plane(1) == 1
            && plane(2) == 1
            && comps[1].step == 2 * bytes
            && comps[2].offset == comps[1].offset + bytes;
        if semi {
            return Some(SoftwareLayout {
                layout: Layout::SemiPlanar,
                planes: vec![0, 1],
                formats: vec![one, two],
                depth,
                wide,
                shift,
                chroma,
                rgb,
            });
        }
        return None;
    }
    // Packed 8-bit RGB with a fourth byte: RGBA/RGB0 or BGRA/BGR0.
    if rgb && !wide && comps.iter().all(|c| c.plane == 0 && c.step == 4) {
        let offsets = (comps[0].offset, comps[1].offset, comps[2].offset);
        let format = match offsets {
            (0, 1, 2) => MTLPixelFormat::RGBA8Unorm,
            (2, 1, 0) => MTLPixelFormat::BGRA8Unorm,
            _ => return None,
        };
        return Some(SoftwareLayout {
            layout: Layout::Packed,
            planes: vec![0],
            formats: vec![format],
            depth,
            wide,
            shift,
            chroma: (0, 0),
            rgb,
        });
    }
    None
}

/// The format a frame the shader cannot sample is converted to: the same
/// kind of signal, at full precision.
pub(crate) fn conversion_target(format: ffi::AVPixelFormat) -> ffi::AVPixelFormat {
    // SAFETY: av_pix_fmt_desc_get returns a static descriptor or null.
    let rgb = unsafe { ffi::av_pix_fmt_desc_get(format).as_ref() }
        .is_some_and(|d| d.flags & ffi::AV_PIX_FMT_FLAG_RGB as u64 != 0);
    if rgb {
        ffi::AVPixelFormat::AV_PIX_FMT_RGBA
    } else {
        ffi::AVPixelFormat::AV_PIX_FMT_YUV444P16LE
    }
}

/// Whether the shader samples frames of `format` without a conversion.
pub(crate) fn samples_directly(format: ffi::AVPixelFormat) -> bool {
    software_layout(format).is_some()
}

impl Uploads {
    /// Copies a software frame into this set's textures. None for a format
    /// `samples_directly` refuses.
    ///
    /// The textures must not be in use by the GPU: the caller waits for the
    /// command buffer that last read them.
    pub(crate) fn upload(
        &mut self,
        device: &ProtocolObject<dyn MTLDevice>,
        frame: &frame::Video,
    ) -> Option<Planes> {
        // SAFETY: a valid decoded frame.
        let raw = unsafe { &*frame.as_ptr() };
        // SAFETY: a software frame's format field holds an AVPixelFormat.
        let format = unsafe { std::mem::transmute::<i32, ffi::AVPixelFormat>(raw.format) };
        let layout = software_layout(format)?;
        let (width, height) = (raw.width.max(0) as usize, raw.height.max(0) as usize);
        let bytes = if layout.wide { 2 } else { 1 };
        let mut textures = Vec::with_capacity(layout.planes.len());
        for (slot, (&plane, &pixel_format)) in layout.planes.iter().zip(&layout.formats).enumerate()
        {
            let chroma = layout.layout != Layout::Packed && slot > 0;
            let (w, h) = if chroma {
                (
                    width.div_ceil(1 << layout.chroma.0),
                    height.div_ceil(1 << layout.chroma.1),
                )
            } else {
                (width, height)
            };
            let shape = PlaneShape {
                format: pixel_format,
                width: w,
                height: h,
            };
            let texture = self.texture(device, slot, shape)?;
            let data = NonNull::new(raw.data[plane].cast::<c_void>())?;
            let stride = raw.linesize[plane];
            if stride <= 0 {
                return None;
            }
            let texel = match pixel_format {
                MTLPixelFormat::RGBA8Unorm | MTLPixelFormat::BGRA8Unorm => 4,
                MTLPixelFormat::RG8Unorm | MTLPixelFormat::RG16Unorm => 2 * bytes,
                _ => bytes,
            };
            debug_assert!(stride as usize >= w * texel);
            let region = MTLRegion {
                origin: MTLOrigin { x: 0, y: 0, z: 0 },
                size: MTLSize {
                    width: w,
                    height: h,
                    depth: 1,
                },
            };
            // SAFETY: the plane holds `h` rows of `stride` bytes (FFmpeg's
            // allocation), each at least `w` texels wide; the texture is not
            // in use by the GPU, per this function's contract.
            unsafe {
                texture.replaceRegion_mipmapLevel_withBytes_bytesPerRow(
                    region,
                    0,
                    data,
                    stride as usize,
                );
            }
            textures.push(texture);
        }
        Some(Planes {
            layout: layout.layout,
            colour: colour_of(
                frame,
                layout.depth,
                if layout.wide { 16 } else { 8 },
                layout.shift,
                layout.chroma,
                layout.rgb,
            ),
            textures,
            keep: Vec::new(),
        })
    }

    fn texture(
        &mut self,
        device: &ProtocolObject<dyn MTLDevice>,
        slot: usize,
        shape: PlaneShape,
    ) -> Option<Texture> {
        if let Some((known, texture)) = self.textures.get(slot)
            && *known == shape
        {
            return Some(texture.clone());
        }
        let texture = new_texture(
            device,
            shape.format,
            shape.width,
            shape.height,
            MTLTextureUsage::ShaderRead,
        )?;
        if slot < self.textures.len() {
            self.textures[slot] = (shape, texture.clone());
        } else {
            self.textures.push((shape, texture.clone()));
        }
        Some(texture)
    }
}

/// A private-to-the-app texture the CPU can write (shared storage: unified
/// memory on Apple silicon, and managed would need a synchronise blit).
pub(crate) fn new_texture(
    device: &ProtocolObject<dyn MTLDevice>,
    format: MTLPixelFormat,
    width: usize,
    height: usize,
    usage: MTLTextureUsage,
) -> Option<Texture> {
    // SAFETY: a plain descriptor; the sizes are checked by Metal.
    let descriptor = unsafe {
        MTLTextureDescriptor::texture2DDescriptorWithPixelFormat_width_height_mipmapped(
            format,
            width.max(1),
            height.max(1),
            false,
        )
    };
    descriptor.setUsage(usage);
    let storage = if device.hasUnifiedMemory() {
        MTLStorageMode::Shared
    } else {
        MTLStorageMode::Managed
    };
    descriptor.setStorageMode(if usage.contains(MTLTextureUsage::RenderTarget) {
        MTLStorageMode::Private
    } else {
        storage
    });
    device.newTextureWithDescriptor(&descriptor)
}
