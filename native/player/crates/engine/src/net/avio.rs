//! FFmpeg reads a `Source` through custom I/O: an `AVIOContext` whose read
//! and seek callbacks are the source's, so a seek is a new read position
//! rather than a new connection.

use std::ffi::{c_int, c_void};
use std::io;
use std::sync::Arc;

use ffmpeg_next::ffi;

use super::source::Source;

/// FFmpeg's own read buffer in front of the callbacks.
const BUFFER_BYTES: usize = 64 * 1024;

/// What the callbacks work on.
struct Reader {
    source: Source,
    position: u64,
    /// The last whole chunk read (see `Source::read`).
    hot: Option<(u64, Arc<[u8]>)>,
    interrupted: Box<dyn Fn() -> bool + Send>,
}

/// An `AVIOContext` reading a `Source`. Must outlive the format context it
/// is given to, which does not free it (`AVFMT_FLAG_CUSTOM_IO`).
#[derive(Debug)]
pub(crate) struct CustomIo {
    context: *mut ffi::AVIOContext,
    reader: *mut Reader,
}

// SAFETY: the context and its reader are used by one thread at a time -- the
// demuxer that owns the format context -- and moved, never shared.
unsafe impl Send for CustomIo {}

impl CustomIo {
    /// Custom I/O over `source`; reads give up when `interrupted` says so.
    /// None when FFmpeg cannot allocate it.
    pub(crate) fn new(
        source: Source,
        interrupted: impl Fn() -> bool + Send + 'static,
    ) -> Option<Self> {
        let reader = Box::into_raw(Box::new(Reader {
            source,
            position: 0,
            hot: None,
            interrupted: Box::new(interrupted),
        }));
        // SAFETY: plain allocations; on failure each is freed here, and on
        // success both belong to the returned value, freed in Drop.
        unsafe {
            let buffer = ffi::av_malloc(BUFFER_BYTES).cast::<u8>();
            if buffer.is_null() {
                drop(Box::from_raw(reader));
                return None;
            }
            let context = ffi::avio_alloc_context(
                buffer,
                BUFFER_BYTES as c_int,
                0,
                reader.cast::<c_void>(),
                Some(read_packet),
                None,
                Some(seek),
            );
            if context.is_null() {
                ffi::av_free(buffer.cast());
                drop(Box::from_raw(reader));
                return None;
            }
            Some(Self { context, reader })
        }
    }

    pub(crate) fn context(&self) -> *mut ffi::AVIOContext {
        self.context
    }

    /// The file is open: its opening reads may be kept (see `keep.rs`).
    pub(crate) fn mark_opened(&self) {
        // SAFETY: the reader lives as long as self, and the demuxer that
        // calls this is the only thread using it.
        let reader = unsafe { &*self.reader };
        reader.source.mark_opened();
    }

    /// Playback paused or resumed (see `Source::set_paused`).
    pub(crate) fn set_paused(&self, paused: bool) {
        // SAFETY: the reader lives as long as self, and the demuxer that
        // calls this is the only thread using it.
        let reader = unsafe { &*self.reader };
        reader.source.set_paused(paused);
    }

    /// Where reading is now: the resume point, once the demuxer has sought
    /// to it. The backfill starts behind it.
    pub(crate) fn mark_resume_point(&self) {
        // SAFETY: the reader lives as long as self, and the demuxer that
        // calls this is the only thread using it.
        let reader = unsafe { &*self.reader };
        reader.source.backfill_from(reader.position);
    }
}

impl Drop for CustomIo {
    fn drop(&mut self) {
        // SAFETY: the format context that used this is already closed (see
        // open::Opened, whose fields drop in order); the buffer may have been
        // replaced by FFmpeg, so it is freed from the context.
        unsafe {
            ffi::av_freep((&raw mut (*self.context).buffer).cast());
            ffi::avio_context_free(&raw mut self.context);
            drop(Box::from_raw(self.reader));
        }
    }
}

unsafe extern "C" fn read_packet(opaque: *mut c_void, buffer: *mut u8, size: c_int) -> c_int {
    // SAFETY: opaque is the Reader this context was made with; FFmpeg hands
    // a buffer of `size` bytes and calls from one thread at a time.
    let (reader, buffer) = unsafe {
        (
            &mut *opaque.cast::<Reader>(),
            std::slice::from_raw_parts_mut(buffer, size.max(0) as usize),
        )
    };
    match reader.source.read(
        reader.position,
        buffer,
        &mut reader.hot,
        &*reader.interrupted,
    ) {
        Ok(0) => ffi::AVERROR_EOF,
        Ok(count) => {
            reader.position += count as u64;
            count as c_int
        }
        Err(error) if error.kind() == io::ErrorKind::Interrupted => ffi::AVERROR_EXIT,
        Err(error) => {
            log::warn!("reading the stream failed: {error}");
            ffi::AVERROR(ffi::EIO)
        }
    }
}

unsafe extern "C" fn seek(opaque: *mut c_void, offset: i64, whence: c_int) -> i64 {
    // SAFETY: as in read_packet.
    let reader = unsafe { &mut *opaque.cast::<Reader>() };
    let size = reader.source.size() as i64;
    let target = match whence & !ffi::AVSEEK_FORCE {
        ffi::AVSEEK_SIZE => return size,
        0 => offset,                          // SEEK_SET
        1 => reader.position as i64 + offset, // SEEK_CUR
        2 => size + offset,                   // SEEK_END
        _ => return i64::from(ffi::AVERROR(ffi::EINVAL)),
    };
    if target < 0 {
        return i64::from(ffi::AVERROR(ffi::EINVAL));
    }
    reader.position = target as u64;
    target
}
