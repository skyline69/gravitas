//! Software rendering: a decoded frame scaled into the embedder's buffer.
//!
//! This is milestone 1's video path. libplacebo on Qt's Vulkan device
//! replaces it (see the roadmap in `native/player/README.md`); until then
//! every shown frame costs one swscale pass on the render thread.

use std::ptr;

use ffmpeg_next::ffi;
use ffmpeg_next::frame;
use ffmpeg_next::software::scaling::Flags;

use crate::error::{Error, Result};

/// The layout written into the target: R, G, B and one padding byte per
/// pixel, in that order in memory (Qt's `QImage::Format_RGBX8888`).
const TARGET_FORMAT: ffi::AVPixelFormat = ffi::AVPixelFormat::AV_PIX_FMT_RGB0;

/// A memory region to render into: `height` rows of `stride` bytes.
#[derive(Debug)]
pub struct RenderTarget<'a> {
    pub pixels: &'a mut [u8],
    pub width: u32,
    pub height: u32,
    pub stride: usize,
}

impl RenderTarget<'_> {
    pub(crate) fn check(&self) -> Result<()> {
        let needed = self.stride * self.height as usize;
        if self.width == 0
            || self.height == 0
            || self.stride < self.width as usize * 4
            || self.pixels.len() < needed
        {
            return Err(Error::RenderTarget {
                len: self.pixels.len(),
                stride: self.stride,
                height: self.height as usize,
            });
        }
        Ok(())
    }
}

/// A cached swscale context. Rebuilt only when the source or target shape
/// changes -- building one costs far more than using it.
#[derive(Debug)]
pub(crate) struct Scaler {
    context: *mut ffi::SwsContext,
    shape: Option<Shape>,
}

// SAFETY: the context is only ever used through `&mut self`, so it is never
// touched from two threads at once; swscale keeps no thread-local state.
unsafe impl Send for Scaler {}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Shape {
    format: ffi::AVPixelFormat,
    width: i32,
    height: i32,
    target_width: i32,
    target_height: i32,
    matrix: i32,
    full_range: bool,
}

impl Default for Scaler {
    fn default() -> Self {
        Self {
            context: ptr::null_mut(),
            shape: None,
        }
    }
}

impl Scaler {
    /// Scales `source` into `target`, stretching it to the target's size.
    /// The caller letterboxes; the target is exactly the picture.
    pub(crate) fn scale(
        &mut self,
        source: &frame::Video,
        target: &mut RenderTarget<'_>,
    ) -> Result<()> {
        target.check()?;
        // SAFETY: `source` wraps a valid decoded AVFrame for this call.
        let raw = unsafe { &*source.as_ptr() };
        let shape = Shape {
            format: pixel_format(raw.format),
            width: raw.width,
            height: raw.height,
            target_width: target.width as i32,
            target_height: target.height as i32,
            matrix: matrix(raw.colorspace, raw.height),
            full_range: raw.color_range == ffi::AVColorRange::AVCOL_RANGE_JPEG,
        };
        if self.shape != Some(shape) {
            self.configure(shape)?;
        }
        let destination = [
            target.pixels.as_mut_ptr(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
        ];
        let strides = [target.stride as i32, 0, 0, 0];
        // SAFETY: the context matches the frame's format and size (checked
        // above), the source planes come from a valid AVFrame, and the
        // destination was checked to hold `height` rows of `stride` bytes.
        let rows = unsafe {
            ffi::sws_scale(
                self.context,
                raw.data.as_ptr().cast(),
                raw.linesize.as_ptr(),
                0,
                raw.height,
                destination.as_ptr(),
                strides.as_ptr(),
            )
        };
        if rows < 0 {
            return Err(Error::Ffmpeg(ffmpeg_next::Error::from(rows)));
        }
        Ok(())
    }

    fn configure(&mut self, shape: Shape) -> Result<()> {
        // SAFETY: sws_getCachedContext frees and replaces `self.context`
        // when it cannot reuse it; a null return leaves nothing to free.
        let context = unsafe {
            ffi::sws_getCachedContext(
                self.context,
                shape.width,
                shape.height,
                shape.format,
                shape.target_width,
                shape.target_height,
                TARGET_FORMAT,
                Flags::BILINEAR.bits(),
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null(),
            )
        };
        if context.is_null() {
            self.context = ptr::null_mut();
            self.shape = None;
            return Err(Error::Ffmpeg(ffmpeg_next::Error::InvalidData));
        }
        self.context = context;
        // Without this swscale converts every YUV source with BT.601 at
        // limited range, which shifts the colours of all HD video.
        // SAFETY: `context` is valid; the coefficient tables are static.
        unsafe {
            ffi::sws_setColorspaceDetails(
                context,
                ffi::sws_getCoefficients(shape.matrix),
                i32::from(shape.full_range),
                ffi::sws_getCoefficients(SWS_CS_ITU601),
                1,
                0,
                1 << 16,
                1 << 16,
            );
        }
        self.shape = Some(shape);
        Ok(())
    }
}

impl Drop for Scaler {
    fn drop(&mut self) {
        // SAFETY: the context is owned here and used nowhere else.
        unsafe { ffi::sws_freeContext(self.context) };
    }
}

const SWS_CS_ITU709: i32 = 1;
const SWS_CS_ITU601: i32 = 5;
const SWS_CS_BT2020: i32 = 9;

fn pixel_format(raw: i32) -> ffi::AVPixelFormat {
    // SAFETY: AVFrame::format holds an AVPixelFormat value for video frames.
    unsafe { std::mem::transmute::<i32, ffi::AVPixelFormat>(raw) }
}

/// The YUV matrix a frame was encoded with. Untagged video follows the usual
/// convention: HD and up is BT.709, SD is BT.601.
fn matrix(space: ffi::AVColorSpace, height: i32) -> i32 {
    use ffi::AVColorSpace as S;
    match space {
        S::AVCOL_SPC_BT709 => SWS_CS_ITU709,
        S::AVCOL_SPC_BT2020_NCL | S::AVCOL_SPC_BT2020_CL => SWS_CS_BT2020,
        S::AVCOL_SPC_BT470BG | S::AVCOL_SPC_SMPTE170M => SWS_CS_ITU601,
        _ if height >= 720 => SWS_CS_ITU709,
        _ => SWS_CS_ITU601,
    }
}

#[cfg(test)]
mod tests {
    use ffmpeg_next::format::Pixel;

    use super::*;

    fn grey_frame(width: u32, height: u32, luma: u8) -> frame::Video {
        let mut frame = frame::Video::new(Pixel::YUV420P, width, height);
        frame.data_mut(0).fill(luma);
        frame.data_mut(1).fill(128);
        frame.data_mut(2).fill(128);
        frame
    }

    #[test]
    fn a_frame_is_scaled_into_the_target() {
        let source = grey_frame(64, 36, 235); // limited-range white
        let mut pixels = vec![0u8; 32 * 18 * 4];
        let mut target = RenderTarget {
            pixels: &mut pixels,
            width: 32,
            height: 18,
            stride: 32 * 4,
        };
        Scaler::default()
            .scale(&source, &mut target)
            .expect("scales");
        // Limited-range white comes out full-range white.
        assert!(
            pixels[..3].iter().all(|&c| c >= 250),
            "got {:?}",
            &pixels[..4]
        );
    }

    #[test]
    fn a_target_too_small_for_its_claims_is_refused() {
        let source = grey_frame(16, 16, 16);
        let mut pixels = vec![0u8; 10];
        let mut target = RenderTarget {
            pixels: &mut pixels,
            width: 16,
            height: 16,
            stride: 64,
        };
        assert!(Scaler::default().scale(&source, &mut target).is_err());
    }

    #[test]
    fn untagged_video_takes_its_matrix_from_its_height() {
        let unspecified = ffi::AVColorSpace::AVCOL_SPC_UNSPECIFIED;
        assert_eq!(matrix(unspecified, 1080), SWS_CS_ITU709);
        assert_eq!(matrix(unspecified, 480), SWS_CS_ITU601);
    }
}
