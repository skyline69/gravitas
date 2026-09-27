//! Gravitas' own media player engine.
//!
//! FFmpeg demuxes and decodes, the audio output drives the clock, and video
//! is pulled by the embedder at its own frame rate. The engine has no idea
//! Python or Qt exist: `gravitas-player-python` binds it for the app, and the
//! design, with the milestones still ahead, is in `native/player/README.md`.
//!
//! ```no_run
//! use gravitas_player_engine::{AudioBackend, Event, LoadOptions, Player};
//!
//! let player = Player::new(AudioBackend::System, |event: Event| println!("{event:?}"))?;
//! player.load(LoadOptions { url: "https://example.com/film.mkv".into(), ..Default::default() })?;
//! # Ok::<(), gravitas_player_engine::Error>(())
//! ```

mod audio;
mod audio_queue;
mod clock;
mod core;
#[cfg(target_os = "macos")]
mod coreaudio;
mod decode;
mod error;
mod event;
mod frames;
// libplacebo renders everywhere but macOS, where Metal does (see metal/);
// macOS keeps it for the tests that check the two agree.
#[cfg(any(not(target_os = "macos"), test))]
#[cfg_attr(
    target_os = "macos",
    allow(dead_code, reason = "on macOS only the tests' reference renderer")
)]
mod gpu;
mod hwdec;
#[cfg(target_os = "macos")]
mod metal;
mod net;
mod open;
mod picture;
mod player;
mod queue;
mod render;
mod session;
#[cfg(not(target_os = "macos"))]
mod shared_device;
mod subtitle_decode;
mod subtitles;
mod track;

pub use audio::AudioBackend;
pub use error::{Error, Result};
pub use event::{Event, EventSink};
#[cfg(not(target_os = "macos"))]
pub use gpu::{VulkanDevice, configure_shader_cache};
pub use hwdec::HardwareDecoding;
#[cfg(target_os = "macos")]
pub use metal::{MetalDevice, configure_shader_cache};
pub use net::{NetworkSettings, configure_network, last_network_failure, network_bytes_per_s};
pub use picture::SharedImage;
#[cfg(not(target_os = "macos"))]
pub use player::Teardown;
pub use player::{OwnRenderer, Player};
pub use render::RenderTarget;
pub use session::{Chapter, LoadOptions};
#[cfg(not(target_os = "macos"))]
pub use shared_device::SharedDevice;
pub use subtitles::SubtitleStyle;
pub use track::{Track, TrackKind};

/// The versions of the FFmpeg libraries the engine runs against, for the log.
#[must_use]
pub fn ffmpeg_versions() -> String {
    let split = |v: u32| format!("{}.{}.{}", v >> 16, (v >> 8) & 0xff, v & 0xff);
    format!(
        "libavformat {}, libavcodec {}, libavutil {}",
        split(ffmpeg_next::format::version()),
        split(ffmpeg_next::codec::version()),
        split(ffmpeg_next::util::version()),
    )
}
