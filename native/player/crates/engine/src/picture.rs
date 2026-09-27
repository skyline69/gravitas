//! What a renderer hands the embedder, and what it lays over the video --
//! the same on every platform, whatever draws it.

/// A rendered picture the embedder samples in place, on the embedder's own
/// device: on Vulkan a `VK_FORMAT_R8G8B8A8_UNORM` image in
/// `VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL`, on Metal an
/// `MTLPixelFormatRGB10A2Unorm` texture.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SharedImage {
    pub image: u64,
    pub width: u32,
    pub height: u32,
}

/// A subtitle picture to lay over the video: straight-alpha RGBA, the size of
/// the rendered frame.
#[derive(Debug)]
pub(crate) struct OverlayImage<'a> {
    pub(crate) pixels: &'a [u8],
    pub(crate) width: u32,
    pub(crate) height: u32,
}
