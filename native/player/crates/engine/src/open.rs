//! Opening a URL: the container's header, and probing only when it is not enough.
//!
//! `avformat_find_stream_info` reads packets until every stream's parameters
//! are known. For formats whose header already states them -- Matroska and
//! MP4, which is nearly every debrid release -- that read is pure delay: it
//! goes on until each stream has produced something, and a sparse subtitle
//! track can keep it reading megabytes into the file over the network before
//! the first frame is even asked for. mpv never pays it, because it reads
//! Matroska with its own demuxer. So the probe runs only when the header left
//! something out.

use std::ffi::CString;
use std::ops::{Deref, DerefMut};
use std::ptr;
use std::time::Instant;

use ffmpeg_next::format::context::Input;
use ffmpeg_next::util::interrupt;
use ffmpeg_next::{Dictionary, Error, ffi};

use crate::net::CustomIo;

/// Containers whose header describes every stream completely.
const SELF_DESCRIBING: [&str; 2] = ["matroska", "mov"];

/// An opened input, and the custom I/O it reads through when the engine
/// reads the stream itself (see `net`). Fields drop in declaration order: the
/// format context is closed before its I/O goes.
pub(crate) struct Opened {
    input: Input,
    io: Option<CustomIo>,
}

impl std::fmt::Debug for Opened {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Opened")
            .field("custom_io", &self.io.is_some())
            .finish_non_exhaustive()
    }
}

impl Opened {
    /// Tells the engine's reader that reading from here on is the viewer's
    /// playback: the backfill starts behind it.
    pub(crate) fn mark_opened(&self) {
        if let Some(io) = &self.io {
            io.mark_opened();
        }
    }

    /// Tells the engine's reader whether playback is paused: a paused
    /// viewer's line is free, so it reads as far ahead as the cache holds.
    pub(crate) fn set_paused(&self, paused: bool) {
        if let Some(io) = &self.io {
            io.set_paused(paused);
        }
    }

    pub(crate) fn mark_resume_point(&self) {
        if let Some(io) = &self.io {
            io.mark_resume_point();
        }
    }
}

impl Deref for Opened {
    type Target = Input;
    fn deref(&self) -> &Input {
        &self.input
    }
}

impl DerefMut for Opened {
    fn deref_mut(&mut self) -> &mut Input {
        &mut self.input
    }
}

/// Opens `url` with `options` -- through `io` when given, else FFmpeg's own
/// protocols -- aborting whenever `interrupted` says so.
pub(crate) fn open<F>(
    url: &str,
    options: Dictionary,
    interrupted: F,
    io: Option<CustomIo>,
) -> Result<Opened, Error>
where
    F: FnMut() -> bool + Send + 'static,
{
    let path = CString::new(url).map_err(|_| Error::InvalidData)?;
    let started = Instant::now();
    // SAFETY: every pointer handed to FFmpeg below is either freshly
    // allocated here or owned by the returned Input; on each failure path the
    // context is closed exactly once, by avformat_open_input itself (which
    // frees it on error) or by avformat_close_input.
    unsafe {
        let mut context = ffi::avformat_alloc_context();
        if context.is_null() {
            return Err(Error::Other { errno: ffi::ENOMEM });
        }
        let interrupt = interrupt::new(Box::new(interrupted));
        (*context).interrupt_callback = interrupt.interrupt;
        if let Some(io) = &io {
            // The format context reads through it and must not free it.
            (*context).pb = io.context();
            (*context).flags |= ffi::AVFMT_FLAG_CUSTOM_IO;
        }
        let mut raw_options = options.disown();
        let opened = ffi::avformat_open_input(
            &raw mut context,
            path.as_ptr(),
            ptr::null(),
            &raw mut raw_options,
        );
        drop(Dictionary::own(raw_options));
        if opened < 0 {
            return Err(Error::from(opened));
        }
        let header_ms = started.elapsed().as_millis();
        let format = format_name(context);
        if needs_probe(context, &format) {
            let probed = ffi::avformat_find_stream_info(context, ptr::null_mut());
            if probed < 0 {
                ffi::avformat_close_input(&raw mut context);
                return Err(Error::from(probed));
            }
            log::info!(
                "{format}: header in {header_ms} ms, probed streams in {} ms more",
                started.elapsed().as_millis() - header_ms
            );
        } else {
            log::info!("{format}: header in {header_ms} ms, describes every stream");
        }
        Ok(Opened {
            input: Input::wrap_with_interrupt(context, interrupt.guard),
            io,
        })
    }
}

/// Whether the header left a stream the decoders could not be set up from.
///
/// # Safety
/// `context` must be an opened input format context.
unsafe fn needs_probe(context: *mut ffi::AVFormatContext, format: &str) -> bool {
    if !SELF_DESCRIBING
        .iter()
        .any(|name| format.split(',').any(|f| f == *name))
    {
        return true;
    }
    // SAFETY: the caller guarantees an opened context, whose stream array
    // holds nb_streams valid streams with codec parameters.
    unsafe {
        let count = (*context).nb_streams as usize;
        if count == 0 {
            return true;
        }
        (0..count).any(|index| {
            let parameters = &*(*(*(*context).streams.add(index))).codecpar;
            !described(parameters)
        })
    }
}

/// Enough to open a decoder: the codec, and the shape the decoder cannot
/// work out for itself from the first packet.
fn described(parameters: &ffi::AVCodecParameters) -> bool {
    use ffi::AVMediaType as M;
    if parameters.codec_id == ffi::AVCodecID::AV_CODEC_ID_NONE {
        return false;
    }
    match parameters.codec_type {
        M::AVMEDIA_TYPE_VIDEO => parameters.width > 0 && parameters.height > 0,
        M::AVMEDIA_TYPE_AUDIO => parameters.sample_rate > 0 && parameters.ch_layout.nb_channels > 0,
        _ => true,
    }
}

/// The demuxer's name ("matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2").
///
/// # Safety
/// `context` must be an opened input format context.
unsafe fn format_name(context: *mut ffi::AVFormatContext) -> String {
    // SAFETY: an opened context has its input format set, and the format's
    // name is a static C string.
    unsafe {
        let format = (*context).iformat;
        if format.is_null() || (*format).name.is_null() {
            return String::new();
        }
        std::ffi::CStr::from_ptr((*format).name)
            .to_string_lossy()
            .into_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parameters(kind: ffi::AVMediaType) -> ffi::AVCodecParameters {
        // SAFETY: AVCodecParameters is plain data; zeroed is a valid,
        // "nothing known" value for every field this test reads.
        let mut parameters: ffi::AVCodecParameters = unsafe { std::mem::zeroed() };
        parameters.codec_type = kind;
        parameters.codec_id = ffi::AVCodecID::AV_CODEC_ID_H264;
        parameters
    }

    #[test]
    fn a_stream_is_described_once_its_shape_is_known() {
        let mut video = parameters(ffi::AVMediaType::AVMEDIA_TYPE_VIDEO);
        assert!(!described(&video));
        video.width = 1920;
        video.height = 1080;
        assert!(described(&video));

        let mut audio = parameters(ffi::AVMediaType::AVMEDIA_TYPE_AUDIO);
        audio.sample_rate = 48_000;
        assert!(!described(&audio));
        audio.ch_layout.nb_channels = 6;
        assert!(described(&audio));

        let mut unknown = parameters(ffi::AVMediaType::AVMEDIA_TYPE_SUBTITLE);
        assert!(described(&unknown));
        unknown.codec_id = ffi::AVCodecID::AV_CODEC_ID_NONE;
        assert!(!described(&unknown));
    }
}
