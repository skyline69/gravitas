//! `gravitas_player`: the engine, as the Python app sees it.
//!
//! A thin layer on purpose. It converts types, releases the GIL around
//! everything that can wait (a load tears down the previous file's threads,
//! a render scales a frame), and forwards engine events to one Python
//! callable. The `MediaPlayer` port lives on the Python side, in
//! `gravitas/infrastructure/player/native_player.py`.

mod logging;

use std::time::Duration;

use gravitas_player_engine as engine;
use parking_lot::RwLock;
use pyo3::buffer::PyBuffer;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Converts an engine error into the exception Python sees.
fn to_py(error: &engine::Error) -> PyErr {
    PyRuntimeError::new_err(error.to_string())
}

fn kind_of(name: &str) -> PyResult<engine::TrackKind> {
    match name {
        "video" => Ok(engine::TrackKind::Video),
        "audio" => Ok(engine::TrackKind::Audio),
        "sub" => Ok(engine::TrackKind::Subtitle),
        other => Err(PyValueError::new_err(format!(
            "unknown track kind {other:?}"
        ))),
    }
}

/// An event as `(name, value)`, the shape the Python callback receives.
fn describe(event: engine::Event, py: Python<'_>) -> (&'static str, Py<PyAny>) {
    let none = || py.None();
    match event {
        engine::Event::FileLoaded => ("file-loaded", none()),
        engine::Event::LoadFailed(reason) => (
            "load-failed",
            reason
                .into_pyobject(py)
                .map_or_else(|_| none(), |v| v.into_any().unbind()),
        ),
        engine::Event::TracksChanged => ("tracks-changed", none()),
        engine::Event::StateChanged => ("state-changed", none()),
        engine::Event::FirstFrame { ms } => (
            "first-frame",
            ms.into_pyobject(py)
                .map_or_else(|_| none(), |v| v.into_any().unbind()),
        ),
        engine::Event::EndOfFile => ("end-of-file", none()),
    }
}

/// The engine's player.
///
/// Events arrive on an engine thread as `on_event(name, value)`; the callback
/// must not block for long, and should hop to the thread it needs.
#[pyclass(frozen, name = "Player", module = "gravitas_player")]
struct PyPlayer {
    /// `None` once shut down. Read-locked by every call, write-locked only by
    /// `shutdown`, so a render never waits on anything but a shutdown.
    inner: RwLock<Option<engine::Player>>,
}

impl PyPlayer {
    fn with<T>(&self, f: impl FnOnce(&engine::Player) -> T) -> PyResult<T> {
        let inner = self.inner.read();
        let player = inner
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("the player has been shut down"))?;
        Ok(f(player))
    }
}

#[pymethods]
impl PyPlayer {
    /// `own_renderer` is when the GPU renderer on a device of the player's
    /// own is made: "at-once", or "when-needed" for an embedder that will
    /// render zero-copy (see `engine::OwnRenderer`).
    #[new]
    #[pyo3(signature = (on_event, *, audio = "system", own_renderer = "at-once"))]
    fn new(py: Python<'_>, on_event: Py<PyAny>, audio: &str, own_renderer: &str) -> PyResult<Self> {
        let own_renderer = match own_renderer {
            "at-once" => engine::OwnRenderer::AtOnce,
            "when-needed" => engine::OwnRenderer::WhenNeeded,
            other => {
                return Err(PyValueError::new_err(format!(
                    "unknown own_renderer {other:?}"
                )));
            }
        };
        let backend = match audio {
            "system" => engine::AudioBackend::System,
            "null" => engine::AudioBackend::Null,
            other => {
                return Err(PyValueError::new_err(format!(
                    "unknown audio backend {other:?}"
                )));
            }
        };
        let sink = move |event: engine::Event| {
            Python::attach(|py| {
                let (name, value) = describe(event, py);
                if let Err(error) = on_event.call1(py, (name, value)) {
                    // An exception in the app's handler is the app's bug;
                    // report it the way Python reports one it cannot raise.
                    error.write_unraisable(py, None);
                }
            });
        };
        let player = py
            .detach(|| engine::Player::with_own_renderer(backend, sink, own_renderer))
            .map_err(|e| to_py(&e))?;
        Ok(Self {
            inner: RwLock::new(Some(player)),
        })
    }

    /// Opens `url`, replacing whatever was playing. Returns at once; the
    /// outcome arrives as a `file-loaded` or `load-failed` event.
    /// `keyframe_start` starts at the keyframe at or before `start` rather
    /// than exactly at it (a resume: much sooner to the first picture).
    #[pyo3(signature = (
        url,
        *,
        start = 0.0,
        headers = Vec::new(),
        audio_languages = Vec::new(),
        subtitle_languages = Vec::new(),
        network_timeout = None,
        user_agent = None,
        keyframe_start = false,
    ))]
    #[allow(clippy::too_many_arguments, reason = "keyword arguments from Python")]
    fn load(
        &self,
        py: Python<'_>,
        url: String,
        start: f64,
        headers: Vec<(String, String)>,
        audio_languages: Vec<String>,
        subtitle_languages: Vec<String>,
        network_timeout: Option<f64>,
        user_agent: Option<String>,
        keyframe_start: bool,
    ) -> PyResult<()> {
        let options = engine::LoadOptions {
            url,
            start,
            headers,
            audio_languages,
            subtitle_languages,
            network_timeout: network_timeout
                .filter(|s| s.is_finite() && *s > 0.0)
                .map(Duration::from_secs_f64),
            user_agent,
            keyframe_start,
        };
        py.detach(|| self.with(|p| p.load(options)))?
            .map_err(|e| to_py(&e))
    }

    fn stop(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.with(engine::Player::stop))
    }

    fn set_paused(&self, paused: bool) -> PyResult<()> {
        self.with(|p| p.set_paused(paused))
    }

    fn is_paused(&self) -> PyResult<bool> {
        self.with(engine::Player::is_paused)
    }

    fn seek(&self, seconds: f64) -> PyResult<()> {
        self.with(|p| p.seek(seconds))
    }

    fn position(&self) -> PyResult<f64> {
        self.with(engine::Player::position)
    }

    fn duration(&self) -> PyResult<Option<f64>> {
        self.with(engine::Player::duration)
    }

    fn set_volume(&self, volume: f32) -> PyResult<()> {
        self.with(|p| p.set_volume(volume))
    }

    fn volume(&self) -> PyResult<f32> {
        self.with(engine::Player::volume)
    }

    fn set_muted(&self, muted: bool) -> PyResult<()> {
        self.with(|p| p.set_muted(muted))
    }

    fn is_muted(&self) -> PyResult<bool> {
        self.with(engine::Player::is_muted)
    }

    fn is_loading(&self) -> PyResult<bool> {
        self.with(engine::Player::is_loading)
    }

    /// The file's tracks as dicts with mpv's `track-list` keys, so one
    /// labelling function serves both engines.
    fn tracks<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let (tracks, selected, hdr10_plus, audio_profile) = self.with(|p| {
            let tracks = p.tracks();
            let selected = [
                p.selected(engine::TrackKind::Video),
                p.selected(engine::TrackKind::Audio),
                p.selected(engine::TrackKind::Subtitle),
            ];
            (tracks, selected, p.hdr10_plus(), p.audio_profile())
        })?;
        tracks
            .into_iter()
            .map(|track| {
                let dict = PyDict::new(py);
                dict.set_item("id", track.id)?;
                dict.set_item("type", track.kind.as_str())?;
                dict.set_item("lang", track.language)?;
                dict.set_item("title", track.title)?;
                dict.set_item("codec", track.codec)?;
                dict.set_item("default", track.default)?;
                dict.set_item("forced", track.forced)?;
                dict.set_item("hearing-impaired", track.hearing_impaired)?;
                dict.set_item("demux-channel-count", track.channels)?;
                dict.set_item("demux-samplerate", track.sample_rate)?;
                dict.set_item("demux-w", track.width)?;
                dict.set_item("demux-h", track.height)?;
                dict.set_item("external", track.external)?;
                dict.set_item("dolby-vision-profile", track.dolby_vision_profile)?;
                let playing = selected[track.kind as usize] == Some(track.id);
                // The playing audio track's decoder knows its profile even
                // when the header did not say (Atmos, DTS:X).
                let profile = if track.kind == engine::TrackKind::Audio && playing {
                    audio_profile.clone().or(track.codec_profile)
                } else {
                    track.codec_profile
                };
                dict.set_item("codec-profile", profile)?;
                if track.kind == engine::TrackKind::Video {
                    dict.set_item("color-transfer", track.transfer)?;
                    dict.set_item("hdr10-plus", hdr10_plus)?;
                }
                dict.set_item("selected", selected[track.kind as usize] == Some(track.id))?;
                Ok(dict)
            })
            .collect()
    }

    /// Plays track `id` of `kind` ("video", "audio" or "sub"); None turns
    /// that kind off.
    #[pyo3(signature = (kind, id))]
    fn select(&self, kind: &str, id: Option<u32>) -> PyResult<()> {
        let kind = kind_of(kind)?;
        self.with(|p| p.select(kind, id))
    }

    fn selected(&self, kind: &str) -> PyResult<Option<u32>> {
        let kind = kind_of(kind)?;
        self.with(|p| p.selected(kind))
    }

    /// Hands libplacebo the scene graph's own Vulkan device (the fields of
    /// `GvNativeVkDevice`, see `native/linux/gravitas_native_vk.h`) and returns
    /// `(callback, context)`: the teardown the Qt side must call on its render
    /// thread when the scene graph goes away.
    ///
    /// The handles must describe a live device that outlives that call, and
    /// `render_shared` must only be called on Qt's render thread.
    #[cfg(not(target_os = "macos"))]
    #[pyo3(signature = (*, instance, get_instance_proc_addr, physical_device, device, queue_family, queue_index, api_version, features))]
    #[allow(clippy::too_many_arguments, reason = "keyword arguments from Python")]
    fn attach_vulkan(
        &self,
        instance: u64,
        get_instance_proc_addr: u64,
        physical_device: u64,
        device: u64,
        queue_family: u32,
        queue_index: u32,
        api_version: u32,
        features: u64,
    ) -> PyResult<(usize, usize)> {
        let device = engine::VulkanDevice {
            instance,
            get_instance_proc_addr,
            physical_device,
            device,
            queue_family,
            queue_index,
            api_version,
            features,
        };
        // SAFETY: the caller hands over Qt's live device (documented above);
        // this is the Python-facing statement of the same contract.
        let teardown = self
            .with(|p| unsafe { p.attach_vulkan(&device) })?
            .map_err(|e| to_py(&e))?;
        Ok((teardown.callback as usize, teardown.context as usize))
    }

    /// Hands the renderer the scene graph's own Metal device and command
    /// queue (`id<MTLDevice>`, `id<MTLCommandQueue>`, see
    /// `native/macos/gravitas_native_metal.h`): frames then render into
    /// textures the scene graph samples where they lie. Nothing needs tearing
    /// down with the scene graph; a new one attaches again.
    ///
    /// The handles must be Qt's live device and the queue it renders with,
    /// and `render_shared` must only be called on Qt's render thread.
    #[cfg(target_os = "macos")]
    #[pyo3(signature = (*, device, queue))]
    fn attach_metal(&self, py: Python<'_>, device: u64, queue: u64) -> PyResult<()> {
        let device = engine::MetalDevice { device, queue };
        // SAFETY: the caller hands over Qt's live device and queue
        // (documented above); this is the Python-facing statement of the
        // same contract.
        py.detach(|| self.with(|p| unsafe { p.attach_metal(&device) }))?
            .map_err(|e| to_py(&e))
    }

    /// Decodes into `device` from the next file on, where its decode queues
    /// take the codec; those frames never leave the GPU when the scene graph
    /// runs on the same device.
    #[cfg(not(target_os = "macos"))]
    fn use_shared_device(&self, device: &Bound<'_, PySharedDevice>) -> PyResult<()> {
        let device = device.get().inner;
        self.with(|p| p.use_shared_device(device))
    }

    /// Renders the frame due now into an image on the attached device and
    /// returns `(image, width, height)`, or None when the last one is still
    /// right (`again` returns it anyway, for a new video item). Raises when no
    /// device is attached or the renderer fails.
    #[pyo3(signature = (again = false))]
    fn render_shared(&self, py: Python<'_>, again: bool) -> PyResult<Option<(u64, u32, u32)>> {
        let image = py
            .detach(|| self.with(|p| p.render_shared(again)))?
            .map_err(|e| to_py(&e))?;
        Ok(image.map(|i| (i.image, i.width, i.height)))
    }

    /// Renders through the GPU renderer (HDR tone mapping, Dolby Vision) --
    /// libplacebo, or Metal on macOS -- when true and its device is up;
    /// through swscale when false.
    fn set_gpu_rendering(&self, enabled: bool) -> PyResult<()> {
        self.with(|p| p.set_gpu_rendering(enabled))
    }

    /// Video frames dropped as late since the playing file was loaded.
    fn dropped_frames(&self) -> PyResult<u64> {
        self.with(engine::Player::dropped_frames)
    }

    /// Bytes per second off the network while the engine reads the playing
    /// file itself; None while FFmpeg reads it (see `read_rate`).
    fn network_rate(&self) -> PyResult<Option<f64>> {
        self.with(engine::Player::network_rate)
    }

    /// Which hardware decoders later loads may use: "auto" (the platform's
    /// usual order), "no", or one FFmpeg device type ("cuda", "vaapi",
    /// "vulkan", "videotoolbox", "d3d11va", ...). A device that cannot decode
    /// the file leaves it to software.
    fn set_hardware_decoding(&self, mode: &str) -> PyResult<()> {
        let mode = engine::HardwareDecoding::from_name(mode);
        self.with(|p| p.set_hardware_decoding(mode))
    }

    /// The hardware decoder the playing file's video comes from, or None
    /// while it is decoded in software -- mpv's `hwdec-current`.
    fn hardware_decoder(&self) -> PyResult<Option<String>> {
        self.with(engine::Player::hardware_decoder)
    }

    /// Whether frames are rendered by the GPU renderer right now, which converts
    /// Dolby Vision profile 5 correctly.
    fn renders_dolby_vision(&self) -> PyResult<bool> {
        self.with(engine::Player::renders_dolby_vision)
    }

    /// Shows or hides subtitles; the track stays selected, so showing them
    /// again is instant.
    fn set_subtitle_visible(&self, visible: bool) -> PyResult<()> {
        self.with(|p| p.set_subtitle_visible(visible))
    }

    fn subtitle_visible(&self) -> PyResult<bool> {
        self.with(engine::Player::subtitle_visible)
    }

    fn set_subtitle_delay(&self, seconds: f64) -> PyResult<()> {
        self.with(|p| p.set_subtitle_delay(seconds))
    }

    fn subtitle_delay(&self) -> PyResult<f64> {
        self.with(engine::Player::subtitle_delay)
    }

    /// The look of subtitles without styling of their own, on mpv's scale
    /// (sizes in pixels at 720 lines, `color` as 0xRRGGBB, `back_opacity`
    /// 0-100).
    #[pyo3(signature = (*, font_size, color, border_size, back_opacity, bold))]
    fn set_subtitle_style(
        &self,
        font_size: f64,
        color: u32,
        border_size: f64,
        back_opacity: f64,
        bold: bool,
    ) -> PyResult<()> {
        let style = engine::SubtitleStyle {
            font_size,
            color,
            border_size,
            back_opacity,
            bold,
        };
        self.with(|p| p.set_subtitle_style(style))
    }

    /// Loads a subtitle file beside the open one as a new track and returns
    /// its id. Blocks while it downloads (the GIL is released meanwhile).
    #[pyo3(signature = (url, *, title = "", language = "", select = true))]
    fn add_subtitle(
        &self,
        py: Python<'_>,
        url: &str,
        title: &str,
        language: &str,
        select: bool,
    ) -> PyResult<u32> {
        py.detach(|| self.with(|p| p.add_subtitle(url, title, language, select)))?
            .map_err(|e| to_py(&e))
    }

    /// `(start seconds, title)` for every chapter.
    fn chapters(&self) -> PyResult<Vec<(f64, String)>> {
        self.with(|p| {
            p.chapters()
                .into_iter()
                .map(|c| (c.start, c.title))
                .collect()
        })
    }

    fn buffered_to(&self) -> PyResult<Option<f64>> {
        self.with(engine::Player::buffered_to)
    }

    /// Bytes per second the stream is read at.
    fn read_rate(&self) -> PyResult<f64> {
        self.with(engine::Player::read_rate)
    }

    fn video_size(&self) -> PyResult<Option<(u32, u32)>> {
        self.with(engine::Player::video_size)
    }

    fn wants_frames(&self) -> PyResult<bool> {
        self.with(engine::Player::wants_frames)
    }

    /// Draws the frame due now into `buffer`: `height` rows of `stride`
    /// bytes, RGBX. `buffer` must be a writable, C-contiguous byte buffer
    /// (a ctypes `c_ubyte` array, a `bytearray`). True when it was drawn;
    /// false when the picture already in it is still the right one.
    #[allow(
        clippy::needless_pass_by_value,
        reason = "PyO3 extracts arguments by value; the buffer is released when it drops, with the GIL held"
    )]
    fn render(
        &self,
        py: Python<'_>,
        buffer: PyBuffer<u8>,
        width: u32,
        height: u32,
        stride: usize,
    ) -> PyResult<bool> {
        if buffer.readonly() || !buffer.is_c_contiguous() {
            return Err(PyValueError::new_err(
                "render needs a writable, contiguous buffer",
            ));
        }
        let address = buffer.buf_ptr() as usize;
        let len = buffer.len_bytes();
        py.detach(|| {
            self.with(|player| {
                // SAFETY: `buffer` keeps the exporter's memory alive and
                // unmoved until it is dropped after this call, the length is
                // the exporter's own, and nothing else writes to it while the
                // engine does: the caller is blocked in this call.
                let pixels = unsafe { std::slice::from_raw_parts_mut(address as *mut u8, len) };
                let mut target = engine::RenderTarget {
                    pixels,
                    width,
                    height,
                    stride,
                };
                player.render(&mut target)
            })
        })?
        .map_err(|e| to_py(&e))
    }

    /// Stops playback and ends every engine thread. The object is unusable
    /// afterwards.
    fn shutdown(&self, py: Python<'_>) {
        let player = self.inner.write().take();
        // Dropping joins the event thread, which may be waiting for the GIL
        // to deliver one last event.
        py.detach(|| drop(player));
    }
}

impl Drop for PyPlayer {
    fn drop(&mut self) {
        // Collected without `shutdown()`: this runs with the GIL held, and
        // joining the event thread here could wait on it forever. Let the
        // threads wind down elsewhere.
        if let Some(player) = self.inner.get_mut().take() {
            let _ = std::thread::Builder::new()
                .name("player-drop".to_owned())
                .spawn(move || drop(player));
        }
    }
}

/// The FFmpeg libraries the engine runs against.
#[pyfunction]
fn ffmpeg_versions() -> String {
    engine::ffmpeg_versions()
}

/// The Vulkan device the scene graph, the renderer and the decoder share, so
/// decoded frames are sampled where the decoder wrote them. One per process,
/// never destroyed: the window's scene graph holds it until the end.
#[cfg(not(target_os = "macos"))]
#[pyclass(frozen, name = "SharedDevice", module = "gravitas_player")]
struct PySharedDevice {
    inner: &'static engine::SharedDevice,
}

#[cfg(not(target_os = "macos"))]
#[pymethods]
impl PySharedDevice {
    /// Creates the device on `instance` (a `VkInstance` at API 1.3 or newer,
    /// with its `vkGetInstanceProcAddr`), which must live for the process.
    /// Raises when libplacebo cannot create a device there.
    #[new]
    #[pyo3(signature = (*, instance, get_instance_proc_addr))]
    fn new(py: Python<'_>, instance: u64, get_instance_proc_addr: u64) -> PyResult<Self> {
        // SAFETY: the caller hands over a live instance kept for the process
        // (documented above); this is the Python-facing statement of the same
        // contract.
        let inner = py
            .detach(|| unsafe { engine::SharedDevice::create(instance, get_instance_proc_addr) })
            .map_err(|e| to_py(&e))?;
        Ok(Self { inner })
    }

    /// The device as the scene graph adopts it: `physical_device`, `device`,
    /// `queue_family` and `queue_index` (its graphics queue).
    fn handles<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let device = self.inner.device();
        let handles = PyDict::new(py);
        handles.set_item("physical_device", device.physical_device)?;
        handles.set_item("device", device.device)?;
        handles.set_item("queue_family", device.queue_family)?;
        handles.set_item("queue_index", device.queue_index)?;
        Ok(handles)
    }

    /// Whether the device decodes video (it has a decode queue FFmpeg took).
    #[getter]
    fn decodes(&self) -> bool {
        self.inner.decodes()
    }
}

/// How the engine reads network streams, process-wide, from the next load
/// on: `parallel` reads http(s) over several connections with a chunk cache
/// in `cache_dir` (in memory without one); false leaves them to FFmpeg.
/// `keep_dir` keeps files' opening reads across sessions, so reopening one
/// (a resume) skips its header round trips.
#[pyfunction]
#[pyo3(signature = (*, parallel, cache_dir = None, keep_dir = None))]
fn configure_network(
    parallel: bool,
    cache_dir: Option<std::path::PathBuf>,
    keep_dir: Option<std::path::PathBuf>,
) {
    engine::configure_network(engine::NetworkSettings {
        parallel,
        cache_dir,
        keep_dir,
    });
}

/// Keeps libplacebo's compiled shaders in `directory` from now on, so only
/// the first launch compiles them. The first call wins. A no-op on macOS,
/// where Metal keeps its own.
#[pyfunction]
fn configure_shader_cache(directory: &str) {
    engine::configure_shader_cache(std::path::Path::new(directory));
}

/// Bytes per second the engine pulled off the network lately.
#[pyfunction]
fn network_bytes_per_s() -> f64 {
    engine::network_bytes_per_s()
}

/// Why the last stream the engine probed could not be reached, or None.
#[pyfunction]
fn last_network_failure() -> Option<String> {
    engine::last_network_failure()
}

#[pymodule]
fn gravitas_player(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Engine logs flow into Python's logging under their Rust module paths
    // (gravitas_player_engine.session, ...), from a thread of their own: see
    // logging.rs for why no engine thread may take the GIL to log.
    logging::install(m.py())?;
    m.add_class::<PyPlayer>()?;
    #[cfg(not(target_os = "macos"))]
    m.add_class::<PySharedDevice>()?;
    m.add_function(wrap_pyfunction!(ffmpeg_versions, m)?)?;
    m.add_function(wrap_pyfunction!(configure_network, m)?)?;
    m.add_function(wrap_pyfunction!(configure_shader_cache, m)?)?;
    m.add_function(wrap_pyfunction!(network_bytes_per_s, m)?)?;
    m.add_function(wrap_pyfunction!(last_network_failure, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
