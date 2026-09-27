//! The engine's error type.

/// Everything the engine can fail with. Load failures reach the embedder as
/// [`crate::Event::LoadFailed`] rather than as an `Err`, because a load runs on
/// its own thread; the variants here are for calls that fail synchronously.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// FFmpeg reported an error.
    #[error("ffmpeg: {0}")]
    Ffmpeg(#[from] ffmpeg_next::Error),
    /// The audio output could not be opened or configured.
    #[error("audio output: {0}")]
    Audio(String),
    /// A thread the engine needs could not be started.
    #[error("could not start {what}: {source}")]
    Thread {
        what: &'static str,
        #[source]
        source: std::io::Error,
    },
    /// The player is shutting down.
    #[error("the player is shutting down")]
    Closed,
    /// The call needs an open file and there is none.
    #[error("nothing is loaded")]
    NotLoaded,
    /// Subtitles cannot be drawn: libass did not start.
    #[error("subtitles are unavailable (libass did not start)")]
    NoSubtitles,
    /// The stream's host took no connection; the message says why.
    #[error("{0}")]
    Unreachable(String),
    /// A frame could not be copied out of GPU memory.
    #[error("a decoded frame could not be copied out of GPU memory")]
    FrameTransfer,
    /// The GPU renderer could not do what was asked.
    #[error("GPU rendering: {0}")]
    Gpu(String),
    /// The file has no stream the engine can play.
    #[error("no playable audio or video stream")]
    NothingToPlay,
    /// A render target is too small for the size it claims.
    #[error("render target of {len} bytes cannot hold {height} rows of {stride} bytes")]
    RenderTarget {
        len: usize,
        stride: usize,
        height: usize,
    },
}

/// The engine's result type.
pub type Result<T, E = Error> = std::result::Result<T, E>;
