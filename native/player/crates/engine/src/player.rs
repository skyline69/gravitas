//! The public face of the engine.

use std::sync::Arc;
#[cfg(not(target_os = "macos"))]
use std::sync::atomic::AtomicU64;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use parking_lot::Mutex;

use crate::audio::{AudioBackend, AudioOutput};
use crate::core::Core;
use crate::error::{Error, Result};
use crate::event::{self, Event, EventSink, Events};
use crate::frames::VideoFrame;
#[cfg(not(target_os = "macos"))]
use crate::gpu::{GpuRenderer as Renderer, VulkanDevice};
use crate::hwdec::{self, HardwareDecoding};
#[cfg(target_os = "macos")]
use crate::metal::{MetalDevice, MetalRenderer as Renderer};
use crate::picture::{OverlayImage, SharedImage};
use crate::render::{RenderTarget, Scaler};
use crate::session::{Chapter, LoadOptions, Session, SessionShared};
#[cfg(not(target_os = "macos"))]
use crate::shared_device::SharedDevice;
use crate::subtitle_decode;
use crate::subtitles::{Overlay, SubtitleStyle, Subtitles, TrackKey};
use crate::track::{Track, TrackKind};

/// How often the monitor derives loading, end of file and the read rate.
const MONITOR_PERIOD: Duration = Duration::from_millis(50);
/// How long a subtitle file may take to arrive.
const SUBTITLE_FILE_TIMEOUT: Duration = Duration::from_secs(30);
/// A subtitle track picked by language that has said at most this much...
const QUIET_TRACK_MAX_EVENTS: usize = 1;
/// ...while another track in the same language has said at least this much
/// is a forced-only track in disguise, and the other one is shown instead.
const TALKATIVE_TRACK_MIN_EVENTS: usize = 8;

/// A media player: open a URL, and it plays.
///
/// Every method is cheap and never waits on the network: loading, seeking
/// and track switches are requests the engine's threads carry out. Results
/// arrive as [`Event`]s on the sink given to [`Player::new`].
pub struct Player {
    shared: Arc<Shared>,
    session: Mutex<Option<Session>>,
    audio: AudioOutput,
    display: Mutex<Display>,
    monitor: Option<thread::JoinHandle<()>>,
    event_thread: Option<thread::JoinHandle<()>>,
    /// Creates the GPU renderer's device in the background. Joined on drop:
    /// a Vulkan device still being made while the process exits races the
    /// driver's own teardown, which aborted in NVIDIA's driver.
    gpu_start: Mutex<Option<thread::JoinHandle<()>>>,
    /// Creates the hardware decoding device alongside the first open (see
    /// `HwDecoders::warm`). Joined on drop for the same reason as
    /// `gpu_start`: a driver mid-creation at process exit.
    hwdec_warm: Mutex<Option<thread::JoinHandle<()>>>,
    /// Stopped sessions whose threads are still winding down (see `stop`).
    /// Joined on drop: dropping the player still means every thread it
    /// started is gone.
    retiring: Mutex<Vec<thread::JoinHandle<()>>>,
}

impl std::fmt::Debug for Player {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Player")
            .field("serial", &self.shared.core.serial())
            .finish_non_exhaustive()
    }
}

/// What the player's own threads share with it.
#[derive(Debug)]
struct Shared {
    core: Arc<Core>,
    events: Mutex<Option<Events>>,
    current: Mutex<Option<Arc<SessionShared>>>,
    stop: AtomicBool,
    /// Bytes per second the demuxer is reading, smoothed.
    read_rate: Mutex<f64>,
    /// None when libass could not start: then no subtitle is drawn.
    subtitles: Option<Arc<Subtitles>>,
    /// The subtitle track was chosen by language, not by the viewer, and
    /// may still be corrected (see `correct_subtitle_choice`).
    subtitle_auto: AtomicBool,
    /// The GPU renderer, once its device is up (see `start_gpu`): libplacebo
    /// on Vulkan, or Metal on macOS.
    gpu: Mutex<GpuState>,
    /// The renderer on the embedder's own device, for zero-copy video (see
    /// `attach_vulkan` and `attach_metal`). On Vulkan it is shared with the
    /// embedder's teardown callback, which is what frees it.
    imported: Arc<Mutex<Option<Renderer>>>,
    gpu_enabled: AtomicBool,
    /// The shared device's `VkDevice` (0 without one): frames decoded into it
    /// stay on the GPU (see `use_shared_device`).
    #[cfg(not(target_os = "macos"))]
    decode_device: AtomicU64,
    /// The attached device is the shared one, so frames decoded into it can
    /// be sampled by the zero-copy renderer as they are.
    #[cfg(not(target_os = "macos"))]
    attached_decode_device: AtomicBool,
}

/// When the GPU renderer on a device of the player's own is made.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum OwnRenderer {
    /// As the player is created, so it is up by the first frame.
    #[default]
    AtOnce,
    /// When a frame first needs it. For an embedder that renders zero-copy
    /// on a device of its own ([`Player::attach_vulkan`]), which never uses
    /// a second one -- and making one is not free there: on NVIDIA it held
    /// the embedder's render thread in the driver for ~0.8 s, measured as
    /// the GUI freezing on the click that started playback. Zero-copy that
    /// fails falls back to this device, which then starts.
    WhenNeeded,
}

/// Where the GPU renderer stands. Frames render through swscale in every
/// state but `Ready`.
#[derive(Debug)]
enum GpuState {
    /// Not made yet, and not until a frame needs it (`OwnRenderer::WhenNeeded`).
    Deferred,
    Starting,
    Ready(Box<Renderer>),
    Unavailable,
}

impl Shared {
    fn send(&self, event: Event) {
        if let Some(events) = self.events.lock().as_ref() {
            events.send(event);
        }
    }

    fn current(&self) -> Option<Arc<SessionShared>> {
        self.current.lock().clone()
    }
}

/// The frame on screen and what it was last drawn into.
#[derive(Debug, Default)]
struct Display {
    shown: Option<VideoFrame>,
    scaler: Scaler,
    /// Size of the last target drawn: a new size redraws the same frame.
    drawn: Option<(u32, u32)>,
    /// The image last handed to the embedder on the zero-copy path.
    shared: Option<SharedImage>,
    /// Where subtitles are rasterised for the GPU overlay.
    overlay: Vec<u8>,
}

#[cfg(not(target_os = "macos"))]
/// What the embedder calls when its scene graph goes away: frees libplacebo's
/// objects on the embedder's device, on the embedder's render thread, before
/// the device itself goes. `context` is only valid for one call.
#[derive(Clone, Copy, Debug)]
pub struct Teardown {
    pub callback: unsafe extern "C" fn(*mut std::ffi::c_void),
    pub context: *mut std::ffi::c_void,
}

// SAFETY: the context is an Arc's raw pointer, meant to cross threads.
#[cfg(not(target_os = "macos"))]
unsafe impl Send for Teardown {}

impl Player {
    /// A player whose events go to `sink`.
    ///
    /// # Errors
    /// When a thread the engine needs cannot be started. A missing audio
    /// device is not an error: the player falls back to a silent output.
    pub fn new(audio: AudioBackend, sink: impl EventSink) -> Result<Self> {
        Self::with_own_renderer(audio, sink, OwnRenderer::AtOnce)
    }

    /// A player whose events go to `sink`, making its own GPU renderer when
    /// `own_renderer` says.
    ///
    /// # Errors
    /// As [`Player::new`].
    pub fn with_own_renderer(
        audio: AudioBackend,
        sink: impl EventSink,
        own_renderer: OwnRenderer,
    ) -> Result<Self> {
        ffmpeg_next::init()?;
        // FFmpeg prints straight to stderr; its warnings about a damaged
        // packet are noise next to the engine's own log, and so is its
        // hardware-device probing (see hwdec::install_log_callback).
        ffmpeg_next::util::log::set_level(ffmpeg_next::util::log::Level::Error);
        hwdec::install_log_callback();
        let core = Arc::new(Core::new());
        let (events, event_thread) = event::spawn(Arc::new(sink))?;
        let shared = Arc::new(Shared {
            core: core.clone(),
            events: Mutex::new(Some(events)),
            current: Mutex::new(None),
            stop: AtomicBool::new(false),
            read_rate: Mutex::new(0.0),
            subtitles: Subtitles::new().map(Arc::new),
            subtitle_auto: AtomicBool::new(false),
            gpu: Mutex::new(match own_renderer {
                OwnRenderer::AtOnce => GpuState::Starting,
                OwnRenderer::WhenNeeded => GpuState::Deferred,
            }),
            imported: Arc::new(Mutex::new(None)),
            gpu_enabled: AtomicBool::new(true),
            #[cfg(not(target_os = "macos"))]
            decode_device: AtomicU64::new(0),
            #[cfg(not(target_os = "macos"))]
            attached_decode_device: AtomicBool::new(false),
        });
        let gpu_start = match own_renderer {
            OwnRenderer::AtOnce => start_gpu(&shared),
            OwnRenderer::WhenNeeded => None,
        };
        if shared.subtitles.is_none() {
            log::warn!("libass could not start; subtitles will not be drawn");
        }
        let audio = AudioOutput::open(audio, core)?;
        let monitor = {
            let shared = shared.clone();
            thread::Builder::new()
                .name("player-monitor".to_owned())
                .spawn(move || monitor(&shared))
                .map_err(|source| Error::Thread {
                    what: "the monitor",
                    source,
                })?
        };
        Ok(Self {
            shared,
            session: Mutex::new(None),
            audio,
            display: Mutex::new(Display::default()),
            monitor: Some(monitor),
            event_thread: Some(event_thread),
            gpu_start: Mutex::new(gpu_start),
            hwdec_warm: Mutex::new(None),
            retiring: Mutex::new(Vec::new()),
        })
    }

    /// Opens `options.url`, replacing whatever was playing. The outcome
    /// arrives as [`Event::FileLoaded`] or [`Event::LoadFailed`].
    ///
    /// # Errors
    /// When the demuxer thread cannot be started.
    pub fn load(&self, options: LoadOptions) -> Result<()> {
        self.stop();
        let core = &self.shared.core;
        let serial = core.next_serial();
        core.timeline
            .lock()
            .begin(serial, options.start, true, true);
        core.clock.reset(options.start);
        core.set_starved(false);
        if let Some(subtitles) = &self.shared.subtitles {
            subtitles.reset();
        }
        self.shared.subtitle_auto.store(true, Ordering::Release);
        self.warm_hardware_decoding();
        let events = self.shared.events.lock().clone().ok_or(Error::Closed)?;
        let session = Session::start(
            options,
            serial,
            core.clone(),
            events,
            self.audio.feed.clone(),
            self.audio.format,
            self.shared.subtitles.clone(),
        )?;
        *self.shared.current.lock() = Some(session.shared.clone());
        *self.session.lock() = Some(session);
        self.shared.send(Event::StateChanged);
        Ok(())
    }

    /// Stops playback and closes the file. The last frame stays available
    /// to `render` until the next load draws over it.
    pub fn stop(&self) {
        let previous = self.session.lock().take();
        *self.shared.current.lock() = None;
        if let Some(session) = previous {
            // Invalidate everything in flight first, so nothing the old file
            // produced plays while its threads wind down.
            self.shared.core.next_serial();
            self.retire(session);
        }
        self.shared.core.clock.stop();
    }

    /// Stops `session` and joins its threads on a thread of their own. A
    /// session still opening can be inside a blocking network call no flag
    /// interrupts -- a probe against a host slow to answer took 7 s -- and
    /// joining it here held the caller that long: the app's GUI thread, on
    /// the viewer pressing back, or on the next source's load. Nothing of a
    /// stopping session reaches the embedder (its events are dropped once it
    /// is stopping, its frames by serial), so it may finish in the
    /// background.
    fn retire(&self, session: Session) {
        session.shared.request_stop();
        let mut retiring = self.retiring.lock();
        retiring.retain(|thread| !thread.is_finished());
        match thread::Builder::new()
            .name("player-retire".to_owned())
            .spawn(move || drop(session))
        {
            Ok(thread) => retiring.push(thread),
            // No thread to spare: the session's own drop joins here, as before.
            Err(error) => log::warn!("could not retire a session in the background: {error}"),
        }
    }

    pub fn set_paused(&self, paused: bool) {
        self.shared.core.set_paused(paused);
        self.shared.send(Event::StateChanged);
    }

    #[must_use]
    pub fn is_paused(&self) -> bool {
        self.shared.core.is_paused()
    }

    /// Seeks to `seconds`, exactly: playback resumes at that frame, not at
    /// the keyframe before it.
    pub fn seek(&self, seconds: f64) {
        let Some(session) = self.shared.current() else {
            return;
        };
        let target = match session.info.read().duration {
            Some(duration) => seconds.clamp(0.0, duration),
            None => seconds.max(0.0),
        };
        let core = &self.shared.core;
        let serial = core.next_serial();
        {
            let mut timeline = core.timeline.lock();
            let (has_audio, has_video) = (timeline.has_audio, timeline.has_video);
            timeline.begin(serial, target, has_audio, has_video);
        }
        core.clock.reset(target);
        session.request_seek(target, serial);
        self.shared.send(Event::StateChanged);
    }

    /// The current position in seconds; 0 until the file is open, as mpv's
    /// `time-pos` is unavailable until then (a timeline with no duration yet
    /// has nowhere to put anything else).
    #[must_use]
    pub fn position(&self) -> f64 {
        if !self.shared.current().is_some_and(|s| s.is_opened()) {
            return 0.0;
        }
        let core = &self.shared.core;
        let timeline = core.timeline.lock();
        let position = if timeline.is_prerolling() {
            timeline.target
        } else {
            core.clock.now()
        };
        drop(timeline);
        let duration = self.duration().unwrap_or(f64::INFINITY);
        position.clamp(0.0, duration)
    }

    /// The file's duration, once known.
    #[must_use]
    pub fn duration(&self) -> Option<f64> {
        self.shared.current().and_then(|s| s.info.read().duration)
    }

    /// Volume on mpv's scale: 0-100, higher amplifies.
    pub fn set_volume(&self, volume: f32) {
        self.shared.core.set_volume(volume);
    }

    #[must_use]
    pub fn volume(&self) -> f32 {
        self.shared.core.volume()
    }

    pub fn set_muted(&self, muted: bool) {
        self.shared.core.set_muted(muted);
    }

    #[must_use]
    pub fn is_muted(&self) -> bool {
        self.shared.core.is_muted()
    }

    /// Opening, prerolling after a seek, or waiting on the network.
    #[must_use]
    pub fn is_loading(&self) -> bool {
        loading(&self.shared)
    }

    /// The tracks of the open file.
    #[must_use]
    pub fn tracks(&self) -> Vec<Track> {
        self.shared
            .current()
            .map(|s| s.info.read().tracks.clone())
            .unwrap_or_default()
    }

    /// The playing track of `kind`.
    #[must_use]
    pub fn selected(&self, kind: TrackKind) -> Option<u32> {
        self.shared
            .current()
            .and_then(|s| s.info.read().selected[kind as usize])
    }

    /// Plays track `id` of `kind` (None: none of that kind).
    ///
    /// Switching between two audio tracks is instant: every track's packets
    /// are kept (`audio_queue.rs`), so the new track takes over at the playhead
    /// without reading anything again. Turning audio off or back on seeks to
    /// the current position instead, which re-reads the stream.
    pub fn select(&self, kind: TrackKind, id: Option<u32>) {
        let Some(session) = self.shared.current() else {
            return;
        };
        let known = id.is_none_or(|id| {
            session
                .info
                .read()
                .tracks
                .iter()
                .any(|t| t.kind == kind && t.id == id)
        });
        if !known {
            return;
        }
        if kind == TrackKind::Subtitle {
            // The viewer's choice is final, even of the track already
            // showing; nothing corrects it afterwards. Under the info lock,
            // which the correction takes too: without it, a correction that
            // had decided a moment earlier wrote over the pick.
            let _info = session.info.write();
            self.shared.subtitle_auto.store(false, Ordering::Release);
        }
        if session.info.read().selected[kind as usize] == id {
            return;
        }
        match kind {
            TrackKind::Audio => {
                // Reads back at once, as mpv's `aid` does; the demuxer
                // carries the switch out behind it.
                let previous =
                    std::mem::replace(&mut session.info.write().selected[kind as usize], id);
                let core = &self.shared.core;
                let generation = core.next_audio_generation();
                if previous.is_some() && id.is_some() {
                    session.request_audio(id, Some(core.clock.now()), generation);
                } else {
                    core.timeline.lock().has_audio = id.is_some();
                    session.request_audio(id, None, generation);
                    self.seek(self.position());
                }
            }
            TrackKind::Subtitle => {
                log::info!("subtitles: the viewer picked track {id:?}");
                let key = {
                    let mut info = session.info.write();
                    info.selected[kind as usize] = id;
                    id.and_then(|id| {
                        info.tracks
                            .iter()
                            .find(|t| t.kind == kind && t.id == id)
                            .map(subtitle_key)
                    })
                };
                if let Some(subtitles) = &self.shared.subtitles {
                    subtitles.select(key);
                }
            }
            // One video track plays; switching it is not offered.
            TrackKind::Video => return,
        }
        self.shared.send(Event::TracksChanged);
    }

    /// Shows or hides subtitles without changing the track, so turning them
    /// back on is instant.
    pub fn set_subtitle_visible(&self, visible: bool) {
        if let Some(subtitles) = &self.shared.subtitles {
            subtitles.set_visible(visible);
        }
    }

    #[must_use]
    pub fn subtitle_visible(&self) -> bool {
        self.shared
            .subtitles
            .as_ref()
            .is_some_and(|s| s.is_visible())
    }

    /// Shows subtitles `seconds` later (negative: earlier). Reset by each load.
    pub fn set_subtitle_delay(&self, seconds: f64) {
        if let Some(subtitles) = &self.shared.subtitles {
            subtitles.set_delay(seconds);
        }
    }

    #[must_use]
    pub fn subtitle_delay(&self) -> f64 {
        self.shared.subtitles.as_ref().map_or(0.0, |s| s.delay())
    }

    /// The look of subtitles that carry no styling of their own.
    pub fn set_subtitle_style(&self, style: SubtitleStyle) {
        if let Some(subtitles) = &self.shared.subtitles {
            subtitles.set_style(style);
        }
    }

    /// Loads a subtitle file (an addon's SubRip or WebVTT) beside the open
    /// file, as a new subtitle track, and returns its id. Blocks while the
    /// file downloads, so call it off any thread that must stay responsive.
    ///
    /// # Errors
    /// When nothing is open, libass is unavailable, or the file cannot be
    /// read as subtitles.
    pub fn add_subtitle(
        &self,
        url: &str,
        title: &str,
        language: &str,
        select: bool,
    ) -> Result<u32> {
        let session = self.shared.current().ok_or(Error::NotLoaded)?;
        let subtitles = self.shared.subtitles.clone().ok_or(Error::NoSubtitles)?;
        let id = {
            let mut info = session.info.write();
            let id = info
                .tracks
                .iter()
                .filter(|t| t.kind == TrackKind::Subtitle)
                .map(|t| t.id)
                .max()
                .unwrap_or(0)
                + 1;
            info.tracks.push(Track {
                kind: TrackKind::Subtitle,
                id,
                stream_index: usize::MAX,
                language: (!language.is_empty()).then(|| language.to_owned()),
                title: (!title.is_empty()).then(|| title.to_owned()),
                codec: "subrip".to_owned(),
                default: false,
                forced: false,
                hearing_impaired: false,
                channels: None,
                sample_rate: None,
                width: None,
                height: None,
                external: true,
                dolby_vision_profile: None,
                codec_profile: None,
                transfer: None,
            });
            id
        };
        if let Err(error) = subtitle_decode::load_file(
            url,
            TrackKey::External(id),
            &subtitles,
            SUBTITLE_FILE_TIMEOUT,
        ) {
            session
                .info
                .write()
                .tracks
                .retain(|t| !(t.external && t.id == id));
            return Err(error);
        }
        if select {
            let mut info = session.info.write();
            // Loaded to be shown is a choice, like a pick from the menu.
            self.shared.subtitle_auto.store(false, Ordering::Release);
            info.selected[TrackKind::Subtitle as usize] = Some(id);
            subtitles.select(Some(TrackKey::External(id)));
            subtitles.set_visible(true);
        }
        log::info!(
            "subtitles: added track {id} ({title}){}",
            if select { ", showing it" } else { "" }
        );
        self.shared.send(Event::TracksChanged);
        Ok(id)
    }

    #[must_use]
    pub fn chapters(&self) -> Vec<Chapter> {
        self.shared
            .current()
            .map(|s| s.info.read().chapters.clone())
            .unwrap_or_default()
    }

    /// The media time the read-ahead reaches.
    #[must_use]
    pub fn buffered_to(&self) -> Option<f64> {
        let session = self.shared.current()?;
        match (
            session.video_packets.newest(),
            session.audio_packets.newest(),
        ) {
            (Some(video), Some(audio)) => Some(video.min(audio)),
            (video, audio) => video.or(audio),
        }
    }

    /// Bytes per second the stream is being read at.
    #[must_use]
    pub fn read_rate(&self) -> f64 {
        *self.shared.read_rate.lock()
    }

    /// The picture's display size.
    #[must_use]
    pub fn video_size(&self) -> Option<(u32, u32)> {
        self.shared.current().and_then(|s| s.info.read().video_size)
    }

    /// Whether the embedder should keep asking for frames: something is
    /// playing, or a frame is waiting to be shown for the first time.
    #[must_use]
    pub fn wants_frames(&self) -> bool {
        let Some(session) = self.shared.current() else {
            return false;
        };
        if !self.shared.core.is_paused() {
            return true;
        }
        // A subtitle change while paused (a delay nudge, a new track) has to
        // be drawn over the frame that is already there.
        if self.shared.subtitles.as_ref().is_some_and(|s| s.is_dirty()) {
            return true;
        }
        let serial = self.shared.core.serial();
        let display = self.display.lock();
        display.shown.as_ref().is_none_or(|f| f.serial != serial) && !session.frames.is_empty()
    }

    /// Draws the frame due now into `target`. Returns false when nothing new
    /// is due and the target was last drawn at this size: the embedder keeps
    /// the picture it has.
    ///
    /// # Errors
    /// When `target` cannot hold the size it claims, or swscale fails.
    pub fn render(&self, target: &mut RenderTarget<'_>) -> Result<bool> {
        let core = &self.shared.core;
        let mut display = self.display.lock();
        let changed = self.advance(&mut display);
        let Display {
            shown,
            scaler,
            drawn,
            ..
        } = &mut *display;
        let Some(shown) = shown.as_ref() else {
            return Ok(false);
        };
        let size = (target.width, target.height);
        let video = self.video_size().unwrap_or(size);
        let overlay = self
            .shared
            .subtitles
            .as_ref()
            .map(|s| s.overlay(core.clock.now(), target.width, target.height, video));
        let subtitles_changed = overlay.as_ref().is_some_and(Overlay::changed);
        if !changed && !subtitles_changed && *drawn == Some(size) {
            return Ok(false);
        }
        target.check()?;
        if !self.render_on_gpu(&shown.frame, target)? {
            // swscale reads memory only.
            let copied;
            let frame = if hwdec::on_hardware(&shown.frame) {
                copied = hwdec::copy_to_memory(&shown.frame).ok_or(Error::FrameTransfer)?;
                &copied
            } else {
                &shown.frame
            };
            scaler.scale(frame, target)?;
        }
        if let Some(overlay) = overlay {
            overlay.blend(target);
        }
        *drawn = Some(size);
        Ok(true)
    }

    /// Takes the frame due now onto the display. True when it changed.
    fn advance(&self, display: &mut Display) -> bool {
        let core = &self.shared.core;
        let serial = core.serial();
        let first = display.shown.as_ref().is_none_or(|f| f.serial != serial);
        let Some(session) = self.shared.current() else {
            return false;
        };
        let Some(frame) = session.frames.take_due(core.clock.now(), serial, first) else {
            return false;
        };
        display.shown = Some(frame);
        if first {
            self.report_first_frame(serial);
        }
        true
    }

    /// Hands libplacebo the embedder's own Vulkan device, so frames render
    /// into images the embedder samples where they lie (`render_shared`).
    ///
    /// The returned [`Teardown`] must be called on the embedder's render
    /// thread when its scene graph goes away, before the device is destroyed;
    /// until then the images stay valid.
    ///
    /// # Safety
    /// `device` must describe a live device -- its feature chain included --
    /// that outlives the teardown call, and `render_shared` must only be
    /// called on the thread that submits the embedder's work to its queue.
    ///
    /// # Errors
    /// When libplacebo cannot work on that device.
    #[cfg(not(target_os = "macos"))]
    pub unsafe fn attach_vulkan(&self, device: &VulkanDevice) -> Result<Teardown> {
        // SAFETY: per this function's contract.
        let renderer =
            unsafe { Renderer::import(device) }.map_err(|e| Error::Gpu(e.to_string()))?;
        *self.shared.imported.lock() = Some(renderer);
        let shared = self.shared.decode_device.load(Ordering::Acquire);
        self.shared
            .attached_decode_device
            .store(shared != 0 && shared == device.device, Ordering::Release);
        self.display.lock().shared = None;
        log::info!(
            "video renders zero-copy: libplacebo draws on the scene graph's own Vulkan device"
        );
        let context = Arc::into_raw(self.shared.imported.clone())
            .cast_mut()
            .cast::<std::ffi::c_void>();
        Ok(Teardown {
            callback: release_imported,
            context,
        })
    }

    /// Hands the renderer the embedder's own Metal device and command queue,
    /// so frames render into textures the embedder samples where they lie
    /// (`render_shared`), and VideoToolbox's frames are sampled where the
    /// decoder wrote them.
    ///
    /// Nothing has to be torn down with the embedder's scene graph: the
    /// renderer holds its own references to the device and queue, and the
    /// embedder to every texture it wraps. A new scene graph attaches again.
    ///
    /// # Safety
    /// `device` must hold a live `id<MTLDevice>` and the `id<MTLCommandQueue>`
    /// the embedder submits its frames on, and `render_shared` must only be
    /// called on the thread that encodes the embedder's frames.
    ///
    /// # Errors
    /// When the renderer cannot start on that device.
    #[cfg(target_os = "macos")]
    pub unsafe fn attach_metal(&self, device: &MetalDevice) -> Result<()> {
        let started = Instant::now();
        // SAFETY: per this function's contract.
        let renderer =
            unsafe { Renderer::import(device) }.map_err(|e| Error::Gpu(e.to_string()))?;
        *self.shared.imported.lock() = Some(renderer);
        self.display.lock().shared = None;
        log::info!(
            "video renders zero-copy: Metal draws on the scene graph's own device ({} ms to start)",
            started.elapsed().as_millis()
        );
        Ok(())
    }

    /// Renders the frame due now into an image on the embedder's device
    /// (see `attach_vulkan` and `attach_metal`). `None` when the image last returned is still
    /// the right one -- unless `again` asks for it anyway, as a newly created
    /// video item must.
    ///
    /// # Errors
    /// When no device is attached, or libplacebo fails; the embedder should
    /// fall back to `render`.
    pub fn render_shared(&self, again: bool) -> Result<Option<SharedImage>> {
        let core = &self.shared.core;
        let mut display = self.display.lock();
        let changed = self.advance(&mut display);
        let Display {
            shown,
            shared,
            overlay,
            ..
        } = &mut *display;
        let Some(shown) = shown.as_ref() else {
            return Ok(None);
        };
        // At the picture's own size: the scene graph scales the quad, and
        // the size changes only with the file.
        let (width, height) = self
            .video_size()
            .unwrap_or((shown.frame.width(), shown.frame.height()));
        let subtitles = self
            .shared
            .subtitles
            .as_ref()
            .map(|s| s.overlay(core.clock.now(), width, height, (width, height)));
        let subtitles_changed = subtitles.as_ref().is_some_and(Overlay::changed);
        let same_size = shared.is_some_and(|s| (s.width, s.height) == (width, height));
        if !changed && !subtitles_changed && same_size {
            return Ok(if again { *shared } else { None });
        }
        let overlay_image = match subtitles.as_ref().filter(|s| s.has_content()) {
            Some(subtitles) => {
                overlay.resize(width as usize * height as usize * 4, 0);
                subtitles.rasterize(&mut RenderTarget {
                    pixels: overlay,
                    width,
                    height,
                    stride: width as usize * 4,
                });
                Some(OverlayImage {
                    pixels: overlay,
                    width,
                    height,
                })
            }
            None => None,
        };
        // A frame in the shared device's memory (or VideoToolbox's) is
        // sampled where it lies -- unless the attached device is another one
        // (the embedder did not adopt the shared device), which cannot see it.
        let copied;
        let frame = if hwdec::on_hardware(&shown.frame) && !self.samples_in_place() {
            copied = hwdec::copy_to_memory(&shown.frame).ok_or(Error::FrameTransfer)?;
            &copied
        } else {
            &shown.frame
        };
        let mut imported = self.shared.imported.lock();
        let renderer = imported
            .as_mut()
            .ok_or(Error::Gpu("no Vulkan device attached".to_owned()))?;
        let image = renderer
            .render_shared(frame, width, height, overlay_image.as_ref())
            .map_err(|e| Error::Gpu(e.to_string()))?;
        *shared = Some(image);
        Ok(Some(image))
    }

    /// Copies the picture `render_shared` last returned into `target`, which
    /// must be its size -- for checks and screenshots of the zero-copy path.
    /// Same thread as `render_shared`.
    ///
    /// # Errors
    /// When nothing has been rendered on the attached device, the size is
    /// wrong, or the download fails.
    pub fn read_shared(&self, target: &mut RenderTarget<'_>) -> Result<()> {
        target.check()?;
        let mut imported = self.shared.imported.lock();
        let renderer = imported
            .as_mut()
            .ok_or(Error::Gpu("no Vulkan device attached".to_owned()))?;
        renderer
            .read_last_shared(target)
            .map_err(|e| Error::Gpu(e.to_string()))
    }

    /// Whether the zero-copy renderer reads hardware frames as they are:
    /// always for VideoToolbox's on Metal, only on the shared device on
    /// Vulkan.
    #[cfg(target_os = "macos")]
    #[allow(
        clippy::unused_self,
        reason = "the Vulkan build reads the player's state"
    )]
    fn samples_in_place(&self) -> bool {
        true
    }

    #[cfg(not(target_os = "macos"))]
    fn samples_in_place(&self) -> bool {
        self.shared.attached_decode_device.load(Ordering::Acquire)
    }

    /// Renders through the GPU renderer when it is up and wanted. False
    /// sends the frame to swscale instead; a renderer that fails is not tried
    /// again.
    ///
    /// # Errors
    /// When a hardware frame the renderer cannot read cannot be copied out.
    fn render_on_gpu(
        &self,
        frame: &ffmpeg_next::frame::Video,
        target: &mut RenderTarget<'_>,
    ) -> Result<bool> {
        if !self.shared.gpu_enabled.load(Ordering::Relaxed) {
            return Ok(false);
        }
        let mut gpu = self.shared.gpu.lock();
        if matches!(*gpu, GpuState::Deferred) {
            // Needed after all: swscale draws until the device is up.
            *gpu = GpuState::Starting;
            drop(gpu);
            *self.gpu_start.lock() = start_gpu(&self.shared);
            return Ok(false);
        }
        let GpuState::Ready(renderer) = &mut *gpu else {
            return Ok(false);
        };
        // Metal reads VideoToolbox's frames where they lie; libplacebo on a
        // device of its own cannot read the shared device's.
        let copied;
        let frame = if hwdec::on_hardware(frame) && !cfg!(target_os = "macos") {
            copied = hwdec::copy_to_memory(frame).ok_or(Error::FrameTransfer)?;
            &copied
        } else {
            frame
        };
        Ok(match renderer.render(frame, target) {
            Ok(()) => true,
            Err(error) => {
                log::warn!(
                    "the GPU renderer could not render a frame ({error}); rendering with swscale from now on"
                );
                *gpu = GpuState::Unavailable;
                false
            }
        })
    }

    /// Turns GPU rendering off (or back on); off means swscale, which knows
    /// nothing of HDR or Dolby Vision.
    pub fn set_gpu_rendering(&self, enabled: bool) {
        self.shared.gpu_enabled.store(enabled, Ordering::Relaxed);
    }

    /// Whether frames are rendered by a GPU renderer (libplacebo, or Metal
    /// on macOS), which converts Dolby Vision (profile 5 included) and
    /// tone-maps HDR: the zero-copy one on the embedder's device, or the
    /// player's own -- up, or deferred because zero-copy is expected. False
    /// while the own one is still starting, and for good once it failed.
    #[must_use]
    pub fn renders_dolby_vision(&self) -> bool {
        self.shared.gpu_enabled.load(Ordering::Relaxed)
            && (self.shared.imported.lock().is_some()
                || matches!(
                    *self.shared.gpu.lock(),
                    GpuState::Ready(_) | GpuState::Deferred
                ))
    }

    /// Video frames dropped as late since the playing file was loaded --
    /// mpv's `frame-drop-count`: a decoder or renderer that is not keeping up.
    #[must_use]
    pub fn dropped_frames(&self) -> u64 {
        self.shared.current().map_or(0, |s| s.frames.dropped())
    }

    /// Bytes per second pulled off the network lately, while the playing
    /// file is read by the engine itself; None while FFmpeg reads it (then
    /// `read_rate` is the line's rate). Some(0.0) means nothing was fetched
    /// lately -- the read-ahead is full -- and is no sample of the line: the
    /// demuxer's own rate then measures the local cache.
    #[must_use]
    pub fn network_rate(&self) -> Option<f64> {
        self.shared
            .current()
            .filter(|s| s.reads_network.load(Ordering::Acquire))
            .map(|_| crate::network_bytes_per_s())
    }

    /// The last ~100 ms the audio output played, interleaved at the
    /// output's rate and channel count -- for checks and diagnostics. The
    /// first call starts keeping it, so it is empty then.
    #[must_use]
    pub fn recent_output(&self) -> Vec<f32> {
        self.shared.core.tap.recent()
    }

    /// Starts making the hardware decoding device, once per player.
    fn warm_hardware_decoding(&self) {
        let mut warm = self.hwdec_warm.lock();
        if warm.is_some() {
            return;
        }
        let core = self.shared.core.clone();
        *warm = thread::Builder::new()
            .name("player-hwdec".to_owned())
            .spawn(move || core.hwdec.warm())
            .inspect_err(|error| log::debug!("could not start the hwdec warm-up: {error}"))
            .ok();
    }

    /// Decodes into `device` from the next file on, whenever its decode
    /// queues take the codec; those frames then never leave the GPU. Pair it
    /// with the embedder adopting the same device for its scene graph and
    /// passing it to [`Player::attach_vulkan`]: any other path copies each
    /// shown frame out of it, which costs more than decoding elsewhere.
    #[cfg(not(target_os = "macos"))]
    pub fn use_shared_device(&self, device: &'static SharedDevice) {
        let Some((decoder, operations)) = device.decoder() else {
            return;
        };
        self.shared
            .decode_device
            .store(device.device().device, Ordering::Release);
        self.shared.core.hwdec.set_shared(decoder, operations);
    }

    /// Which hardware decoders later loads may use. The playing file keeps
    /// the decoder it opened with.
    pub fn set_hardware_decoding(&self, mode: HardwareDecoding) {
        self.shared.core.hwdec.set_mode(mode);
    }

    /// The hardware decoder the playing file's video comes from (an FFmpeg
    /// device type, such as "cuda" or "vaapi"), or None while it is decoded
    /// in software -- mpv's `hwdec-current`.
    #[must_use]
    pub fn hardware_decoder(&self) -> Option<String> {
        self.shared.current()?.hardware_decoder.lock().clone()
    }

    /// The selected audio track's codec profile as its decoder reads it
    /// ("Dolby TrueHD + Dolby Atmos", "DTS-HD MA + DTS:X"), once it has
    /// decoded some; the stream's header often does not say.
    #[must_use]
    pub fn audio_profile(&self) -> Option<String> {
        self.shared.current()?.audio_profile.lock().clone()
    }

    /// Whether the playing file's video carries HDR10+ dynamic metadata,
    /// known once its first frame is decoded.
    #[must_use]
    pub fn hdr10_plus(&self) -> bool {
        self.shared
            .current()
            .is_some_and(|s| s.hdr10_plus.load(Ordering::Acquire))
    }

    fn report_first_frame(&self, serial: u64) {
        let mut timeline = self.shared.core.timeline.lock();
        if timeline.serial != serial || timeline.first_frame_reported {
            return;
        }
        timeline.first_frame_reported = true;
        let ms = timeline.since.elapsed().as_millis() as u64;
        drop(timeline);
        self.shared.send(Event::FirstFrame { ms });
    }
}

impl Drop for Player {
    fn drop(&mut self) {
        self.stop();
        self.shared.stop.store(true, Ordering::Release);
        if let Some(monitor) = self.monitor.take() {
            let _ = monitor.join();
        }
        // The last sender goes, so the event thread drains and ends.
        self.shared.events.lock().take();
        if let Some(thread) = self.event_thread.take() {
            let _ = thread.join();
        }
        if let Some(thread) = self.gpu_start.lock().take() {
            let _ = thread.join();
        }
        if let Some(thread) = self.hwdec_warm.lock().take() {
            let _ = thread.join();
        }
        for thread in self.retiring.lock().drain(..) {
            let _ = thread.join();
        }
    }
}

fn loading(shared: &Shared) -> bool {
    let Some(session) = shared.current() else {
        return false;
    };
    if session.has_failed() {
        return false;
    }
    if !session.is_opened() {
        return true;
    }
    let core = &shared.core;
    if core.timeline.lock().is_prerolling() {
        return true;
    }
    core.is_starved() && !core.is_paused() && !session.demux_finished()
}

/// Derives what no single thread can see: preroll timeouts, video-only
/// buffering, end of file, the loading state and the read rate.
fn monitor(shared: &Shared) {
    let mut was_loading = false;
    let mut last_bytes = 0u64;
    let mut last_session: Option<*const SessionShared> = None;
    let mut last_tick = Instant::now();
    while !shared.stop.load(Ordering::Acquire) {
        thread::sleep(MONITOR_PERIOD);
        let core = &shared.core;
        core.check_preroll();
        let Some(session) = shared.current() else {
            continue;
        };
        if last_session != Some(Arc::as_ptr(&session)) {
            last_session = Some(Arc::as_ptr(&session));
            last_bytes = 0;
        }
        if !core.timeline.lock().is_prerolling() {
            session.frames.drop_late(core.clock.now());
        }
        if let Some(subtitles) = &shared.subtitles {
            subtitles.prune(core.clock.now());
        }
        watch_video_only(core, &session);
        correct_subtitle_choice(shared, &session);
        check_end(shared, &session);

        let now = loading(shared);
        if now != was_loading {
            was_loading = now;
            shared.send(Event::StateChanged);
        }

        let bytes = session.bytes_read.load(Ordering::Relaxed);
        let elapsed = last_tick.elapsed().as_secs_f64();
        last_tick = Instant::now();
        if elapsed > 0.0 {
            let rate = bytes.saturating_sub(last_bytes) as f64 / elapsed;
            let mut smoothed = shared.read_rate.lock();
            *smoothed = *smoothed * 0.8 + rate * 0.2;
        }
        last_bytes = bytes;
    }
}

/// Without audio nothing stops the clock when the network falls behind, so
/// the monitor does: no frame queued and nothing left to decode is an
/// underrun.
fn watch_video_only(core: &Core, session: &SessionShared) {
    let serial = core.serial();
    let timeline = core.timeline.lock();
    if timeline.has_audio
        || timeline.serial != serial
        || timeline.is_prerolling()
        || core.is_paused()
    {
        return;
    }
    drop(timeline);
    let starved =
        session.frames.is_empty() && session.video_packets.is_empty() && !session.demux_finished();
    if starved && core.clock.is_running() {
        core.clock.stop();
        core.set_starved(true);
    } else if !starved && core.is_starved() {
        core.set_starved(false);
        core.clock.start();
    }
}

/// Swaps a subtitle track picked by language for another in the same
/// language when the pick turns out to say nothing.
///
/// Releases label a forced-only track (signs, foreign-language lines) as a
/// plain default one often enough: measured on a DUAL WEB-DL of South Park,
/// its default English track had no line from 8:00 to 10:00 while the second
/// English track had dozens. The file's flags cannot tell them apart, but
/// every subtitle track is decoded as it is read, so their event counts can:
/// once the pick has said next to nothing while a sibling has plainly been
/// talking, the sibling is shown. Once, and never over the viewer's choice.
fn correct_subtitle_choice(shared: &Shared, session: &SessionShared) {
    if !shared.subtitle_auto.load(Ordering::Acquire) {
        return;
    }
    let Some(subtitles) = &shared.subtitles else {
        return;
    };
    let counts = subtitles.event_counts();
    let count = |key: TrackKey| {
        counts
            .iter()
            .find(|(k, _)| *k == key)
            .map_or(0, |(_, n)| *n)
    };
    let replacement = {
        let info = session.info.read();
        let Some(chosen) = info.selected[TrackKind::Subtitle as usize].and_then(|id| {
            info.tracks
                .iter()
                .find(|t| t.kind == TrackKind::Subtitle && t.id == id)
        }) else {
            return;
        };
        if count(subtitle_key(chosen)) > QUIET_TRACK_MAX_EVENTS {
            // It is talking: the pick stands for the rest of the file.
            shared.subtitle_auto.store(false, Ordering::Release);
            return;
        }
        info.tracks
            .iter()
            .filter(|t| {
                t.kind == TrackKind::Subtitle
                    && t.id != chosen.id
                    && !t.external
                    && !t.forced
                    && t.language.is_some()
                    && t.language == chosen.language
            })
            .map(|t| (t.clone(), count(subtitle_key(t))))
            .filter(|(_, n)| *n >= TALKATIVE_TRACK_MIN_EVENTS)
            .max_by_key(|(_, n)| *n)
            .map(|(t, n)| (chosen.id, t, n))
    };
    let Some((quiet, track, said)) = replacement else {
        return;
    };
    {
        let mut info = session.info.write();
        // The viewer may have picked since the decision above; the pick wins
        // (see select, which clears this under the same lock).
        if !shared.subtitle_auto.swap(false, Ordering::AcqRel)
            || info.selected[TrackKind::Subtitle as usize] != Some(quiet)
        {
            return;
        }
        info.selected[TrackKind::Subtitle as usize] = Some(track.id);
        subtitles.select(Some(subtitle_key(&track)));
    }
    log::info!(
        "subtitle track {quiet} has said nothing while track {} ({said} lines) has; showing that one",
        track.id
    );
    shared.send(Event::TracksChanged);
}

fn check_end(shared: &Shared, session: &SessionShared) {
    let core = &shared.core;
    // Ended once the last frame's time has come, whether or not anyone drew
    // it: a hidden window still reaches the end of the file.
    let now = core.clock.now();
    if !session.demux_finished() || session.frames.last_pts().is_some_and(|pts| pts > now) {
        return;
    }
    let mut timeline = core.timeline.lock();
    if timeline.end_reported || timeline.is_prerolling() || !timeline.all_ended() {
        return;
    }
    timeline.end_reported = true;
    drop(timeline);
    core.clock.stop();
    shared.send(Event::EndOfFile);
}

/// The embedder's teardown: frees the imported renderer on its render thread,
/// then lets go of the reference `attach_vulkan` gave it.
#[cfg(not(target_os = "macos"))]
unsafe extern "C" fn release_imported(context: *mut std::ffi::c_void) {
    if context.is_null() {
        return;
    }
    // SAFETY: `context` came from Arc::into_raw in attach_vulkan and is
    // released exactly once, here.
    let slot = unsafe { Arc::from_raw(context.cast_const().cast::<Mutex<Option<Renderer>>>()) };
    drop(slot.lock().take());
    log::info!("zero-copy renderer released with the scene graph");
}

/// Brings the GPU renderer up on a thread of its own: creating a Vulkan
/// device (or compiling the Metal shaders) takes long enough to show as a
/// stall if the first frame waited for it, and swscale covers the frames
/// rendered meanwhile.
fn start_gpu(shared: &Arc<Shared>) -> Option<thread::JoinHandle<()>> {
    let shared = shared.clone();
    let spawned = thread::Builder::new()
        .name("player-gpu".to_owned())
        .spawn(move || {
            let started = Instant::now();
            let state = match Renderer::new() {
                Ok(renderer) => {
                    log::info!(
                        "video renders through {} ({} ms to start): HDR is tone-mapped, Dolby Vision converted",
                        if cfg!(target_os = "macos") { "Metal" } else { "libplacebo on Vulkan" },
                        started.elapsed().as_millis()
                    );
                    GpuState::Ready(Box::new(renderer))
                }
                Err(error) => {
                    log::info!("no GPU renderer ({error}); video renders through swscale");
                    GpuState::Unavailable
                }
            };
            *shared.gpu.lock() = state;
        });
    spawned
        .inspect_err(|error| log::warn!("could not start the GPU renderer: {error}"))
        .ok()
}

/// Where a subtitle track's events live.
fn subtitle_key(track: &Track) -> TrackKey {
    if track.external {
        TrackKey::External(track.id)
    } else {
        TrackKey::Stream(track.stream_index)
    }
}
