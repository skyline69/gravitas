//! One loaded file: the demuxer thread, its decoders, and what they share.

use std::collections::HashMap;
use std::fmt::Write as _;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use crossbeam_channel::Sender;
use ffmpeg_next::format::context::Input;
use ffmpeg_next::{Dictionary, Packet, Rational, codec, ffi};
use parking_lot::{Condvar, Mutex, RwLock};

use crate::audio::{Feed, OutputFormat};
use crate::audio_queue::{AudioPackets, KEEP_BEHIND_S};
use crate::core::Core;
use crate::decode::{self, AudioSwitch};
use crate::error::{Error, Result};
use crate::event::{Event, Events};
use crate::frames::{self, FrameQueue};
use crate::net;
use crate::open::{self, Opened};
use crate::queue::PacketQueue;
use crate::subtitle_decode::{self, SubtitleDecoder};
use crate::subtitles::{Subtitles, TrackKey};
use crate::track::{self, Track, TrackKind};

/// Read ahead until this much media is queued for every playing stream...
pub(crate) const READAHEAD_S: f64 = 30.0;
/// ...or this many bytes are queued in all, whichever comes first.
pub(crate) const MAX_QUEUED_BYTES: usize = 256 * 1024 * 1024;

/// How the demuxer waits when it has nothing to do.
const IDLE_WAIT: Duration = Duration::from_millis(20);

/// Everything needed to open a file.
#[derive(Clone, Debug, Default)]
pub struct LoadOptions {
    pub url: String,
    /// Where to start, in seconds.
    pub start: f64,
    /// Extra HTTP request headers.
    pub headers: Vec<(String, String)>,
    /// Audio languages in order of preference, in every spelling a file may
    /// use ("de", "ger", "deu").
    pub audio_languages: Vec<String>,
    /// Subtitle languages, likewise. Empty: no subtitles.
    pub subtitle_languages: Vec<String>,
    /// How long a network read may stall before it counts as failed.
    pub network_timeout: Option<Duration>,
    pub user_agent: Option<String>,
    /// Start at the keyframe at or before `start` rather than exactly at it
    /// -- for resuming, where a second early costs nothing and decoding (and
    /// first downloading) everything from the keyframe up to the exact point
    /// is most of the wait for the first picture. Off: an exact start.
    pub keyframe_start: bool,
}

/// A chapter of the file.
#[derive(Clone, Debug, PartialEq)]
pub struct Chapter {
    pub start: f64,
    pub title: String,
}

/// What is known about the opened file.
#[derive(Clone, Debug, Default)]
pub(crate) struct MediaInfo {
    pub(crate) tracks: Vec<Track>,
    pub(crate) duration: Option<f64>,
    pub(crate) chapters: Vec<Chapter>,
    /// The picture's display size, sample aspect applied.
    pub(crate) video_size: Option<(u32, u32)>,
    /// The playing track of each kind, by id.
    pub(crate) selected: [Option<u32>; 3],
}

/// What the player asks of the demuxer.
#[derive(Debug, Default)]
struct Requests {
    seek: Option<(f64, u64)>,
    audio: Option<AudioRequest>,
}

/// An audio track to switch to (None: no audio).
#[derive(Clone, Copy, Debug, PartialEq)]
struct AudioRequest {
    track: Option<u32>,
    /// Where the new track takes over, for a switch between two tracks
    /// while playing; None when a seek to the position follows (a switch to
    /// or from no audio), which re-reads everything anyway.
    at: Option<f64>,
    /// The selection this is (see `Core::audio_generation`).
    generation: u64,
}

/// State shared between the player and a session's threads.
#[derive(Debug)]
pub(crate) struct SessionShared {
    stop: AtomicBool,
    pub(crate) video_packets: PacketQueue,
    /// Every audio track's packets (see `audio_queue.rs`).
    pub(crate) audio_packets: AudioPackets,
    /// The audio decoder thread has been started; it lives as long as the
    /// session, idle while no track is selected.
    audio_decoder: AtomicBool,
    /// Packets of every subtitle stream: they are tiny, and decoding them
    /// all is what makes switching between them instant.
    pub(crate) subtitle_packets: PacketQueue,
    pub(crate) frames: FrameQueue,
    requests: Mutex<Requests>,
    wake: Condvar,
    pub(crate) info: RwLock<MediaInfo>,
    pub(crate) bytes_read: AtomicU64,
    demux_eof: AtomicBool,
    opened: AtomicBool,
    failed: AtomicBool,
    pub(crate) started: Instant,
    /// The engine reads the stream itself (net/), rather than FFmpeg.
    pub(crate) reads_network: AtomicBool,
    /// The hardware decoder the video frames come from, once the first
    /// frame says so; None while decoding in software.
    pub(crate) hardware_decoder: Mutex<Option<String>>,
    /// The video's first frame carried HDR10+ dynamic metadata (ST 2094-40),
    /// which only frames state -- the stream's headers do not.
    pub(crate) hdr10_plus: AtomicBool,
    /// The selected audio track's codec profile as its decoder reads it
    /// from the bitstream ("Dolby TrueHD + Dolby Atmos"), which the stream's
    /// header may not state.
    pub(crate) audio_profile: Mutex<Option<String>>,
}

impl SessionShared {
    fn new() -> Self {
        Self {
            stop: AtomicBool::new(false),
            video_packets: PacketQueue::default(),
            audio_packets: AudioPackets::default(),
            audio_decoder: AtomicBool::new(false),
            subtitle_packets: PacketQueue::default(),
            frames: FrameQueue::default(),
            requests: Mutex::new(Requests::default()),
            wake: Condvar::new(),
            info: RwLock::new(MediaInfo::default()),
            bytes_read: AtomicU64::new(0),
            demux_eof: AtomicBool::new(false),
            opened: AtomicBool::new(false),
            failed: AtomicBool::new(false),
            hardware_decoder: Mutex::new(None),
            hdr10_plus: AtomicBool::new(false),
            audio_profile: Mutex::new(None),
            reads_network: AtomicBool::new(false),
            started: Instant::now(),
        }
    }

    pub(crate) fn stopping(&self) -> bool {
        self.stop.load(Ordering::Acquire)
    }

    pub(crate) fn is_opened(&self) -> bool {
        self.opened.load(Ordering::Acquire)
    }

    pub(crate) fn has_failed(&self) -> bool {
        self.failed.load(Ordering::Acquire)
    }

    pub(crate) fn demux_finished(&self) -> bool {
        self.demux_eof.load(Ordering::Acquire)
    }

    /// Asks the demuxer to seek to `target` under `serial`, replacing any
    /// seek it has not started yet.
    pub(crate) fn request_seek(&self, target: f64, serial: u64) {
        self.video_packets.flush(serial);
        self.audio_packets.flush(serial);
        self.subtitle_packets.flush(serial);
        self.frames.flush(serial);
        self.requests.lock().seek = Some((target, serial));
        self.wake.notify_all();
    }

    /// Asks the demuxer to play audio track `track` (None: none) as
    /// selection `generation`. With `at`, the new track takes over from
    /// there, out of the packets already read; without, the caller seeks to
    /// the current position alongside, which re-reads them.
    pub(crate) fn request_audio(&self, track: Option<u32>, at: Option<f64>, generation: u64) {
        self.requests.lock().audio = Some(AudioRequest {
            track,
            at,
            generation,
        });
        self.wake.notify_all();
    }

    /// Stops the session's threads without waiting for them: what `Drop`
    /// does first, for a caller that joins them elsewhere.
    pub(crate) fn request_stop(&self) {
        self.shut_down();
    }

    fn shut_down(&self) {
        self.stop.store(true, Ordering::Release);
        self.video_packets.close();
        self.audio_packets.close();
        self.subtitle_packets.close();
        self.frames.close();
        self.wake.notify_all();
    }
}

/// A loaded file. Dropping it stops and joins every thread it started.
#[derive(Debug)]
pub(crate) struct Session {
    pub(crate) shared: Arc<SessionShared>,
    demuxer: Option<thread::JoinHandle<()>>,
}

impl Session {
    /// Starts opening `options.url` on a new thread under `serial`.
    pub(crate) fn start(
        options: LoadOptions,
        serial: u64,
        core: Arc<Core>,
        events: Events,
        feed: Sender<Feed>,
        output: OutputFormat,
        subtitles: Option<Arc<Subtitles>>,
    ) -> Result<Self> {
        let shared = Arc::new(SessionShared::new());
        shared.video_packets.flush(serial);
        shared.audio_packets.flush(serial);
        shared.subtitle_packets.flush(serial);
        shared.frames.flush(serial);
        let demuxer = {
            let shared = shared.clone();
            thread::Builder::new()
                .name("player-demux".to_owned())
                .spawn(move || {
                    let context = Context {
                        shared,
                        core,
                        events,
                        feed,
                        output,
                        subtitles,
                    };
                    context.run(&options, serial);
                })
                .map_err(|source| Error::Thread {
                    what: "the demuxer",
                    source,
                })?
        };
        Ok(Self {
            shared,
            demuxer: Some(demuxer),
        })
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        self.shared.shut_down();
        if let Some(demuxer) = self.demuxer.take() {
            let _ = demuxer.join();
        }
    }
}

/// What the demuxer thread works with.
struct Context {
    shared: Arc<SessionShared>,
    core: Arc<Core>,
    events: Events,
    feed: Sender<Feed>,
    output: OutputFormat,
    subtitles: Option<Arc<Subtitles>>,
}

/// A selected stream and what its decoder needs.
struct Selected {
    stream_index: usize,
    time_base: Rational,
}

impl Context {
    fn run(&self, options: &LoadOptions, serial: u64) {
        let mut input = match self.open(options) {
            Ok(input) => input,
            Err(error) => {
                if !self.shared.stopping() {
                    log::warn!("could not open {}: {error}", redact(&options.url));
                    self.shared.failed.store(true, Ordering::Release);
                    self.events.send(Event::LoadFailed(error.to_string()));
                }
                return;
            }
        };
        // Stopped while opening: an interrupted open can still hand back a
        // context (the Matroska demuxer shrugs off a read the interrupt cut
        // short), and everything after it -- decoders, a hardware device,
        // the seek -- would run while the next load waits for this thread.
        if self.shared.stopping() {
            return;
        }
        let tracks = track::collect(&input);
        let video = track::choose(&tracks, TrackKind::Video, &[]).cloned();
        let audio = track::choose(&tracks, TrackKind::Audio, &options.audio_languages).cloned();
        let subtitle =
            track::choose(&tracks, TrackKind::Subtitle, &options.subtitle_languages).cloned();
        if video.is_none() && audio.is_none() {
            self.shared.failed.store(true, Ordering::Release);
            if !self.shared.stopping() {
                self.events
                    .send(Event::LoadFailed(Error::NothingToPlay.to_string()));
            }
            return;
        }
        {
            let mut timeline = self.core.timeline.lock();
            if timeline.serial == serial {
                timeline.has_audio = audio.is_some();
                timeline.has_video = video.is_some();
            }
        }
        *self.shared.info.write() = MediaInfo {
            duration: duration(&input),
            chapters: chapters(&input),
            video_size: video
                .as_ref()
                .and_then(|v| display_size(&input, v.stream_index)),
            selected: [
                video.as_ref().map(|t| t.id),
                audio.as_ref().map(|t| t.id),
                subtitle.as_ref().map(|t| t.id),
            ],
            tracks,
        };
        let mut workers = Vec::new();
        let subtitle_streams = self.start_subtitles(&input, subtitle.as_ref(), &mut workers);
        // Every audio track is read, not only the playing one: that is what
        // makes switching between them instant (audio_queue.rs).
        let audio_streams = audio_time_bases(&input, &self.shared.info.read().tracks);
        let playing: Vec<usize> = video
            .iter()
            .map(|t| t.stream_index)
            .chain(audio_streams.keys().copied())
            .chain(subtitle_streams.iter().copied())
            .collect();
        discard_all_but(&mut input, &playing);

        let video_stream = video.as_ref().map(|t| t.stream_index);
        let video = video.and_then(|t| self.start_video(&input, t.stream_index, &mut workers));
        let mut audio = audio.and_then(|t| {
            // The selection the mixer plays is the core's, which outlives
            // files: after a track switch in the last one it is past 0, and
            // a decoder starting at 0 has all its audio dropped as stale.
            let generation = self.core.audio_generation();
            let (selected, decoder) = open_audio(&input, t.stream_index, None, generation)?;
            self.shared
                .audio_packets
                .select(Some(t.stream_index), options.start, None);
            self.spawn_audio(decoder, &mut workers);
            Some(selected)
        });

        // Open means known: duration, tracks and chapters reach the embedder
        // now, before the seek to the start position -- which on a Matroska
        // stream reads the index from the end of the file and can take
        // seconds on its own.
        self.shared.opened.store(true, Ordering::Release);
        self.events.send(Event::TracksChanged);
        self.events.send(Event::FileLoaded);
        log::info!(
            "opened in {} ms: {} tracks",
            self.shared.started.elapsed().as_millis(),
            self.shared.info.read().tracks.len()
        );
        if options.start > 0.0 {
            self.seek_to_start(&mut input, options, serial, video_stream);
        }

        // Open and at the start position: what was read on the way is what
        // the next session opening this file will need first.
        input.mark_opened();
        self.demux(
            &mut input,
            serial,
            &subtitle_streams,
            video.as_ref(),
            &mut audio,
            &audio_streams,
            &mut workers,
        );

        self.shared.shut_down();
        for worker in workers {
            let _ = worker.join();
        }
    }

    /// Seeks to where a resume starts, before the demuxer's loop -- at the
    /// keyframe before `options.start` when the load asks for it.
    fn seek_to_start(
        &self,
        input: &mut Opened,
        options: &LoadOptions,
        serial: u64,
        video_stream: Option<usize>,
    ) {
        let seeking = Instant::now();
        let reached = seek(input, options.start);
        if self.shared.stopping() {
            // Interrupted: neither outcome is news.
        } else if let Err(error) = reached {
            log::warn!("seek to {:.1}s failed: {error}", options.start);
        } else {
            log::info!(
                "reached the start position in {} ms",
                seeking.elapsed().as_millis()
            );
            if options.keyframe_start
                && let Some(keyframe) =
                    video_stream.and_then(|s| keyframe_before(input, s, options.start))
                && options.start - keyframe <= MAX_KEYFRAME_LEAD_S
            {
                // Everything downstream reads the serial's target, and no
                // packet has been read yet: the whole pipeline starts
                // here.
                self.core.retarget(serial, keyframe);
                log::info!(
                    "resuming at the keyframe {:.2}s before {:.1}s",
                    options.start - keyframe,
                    options.start
                );
            }
            // Nothing before a resume point was ever fetched; the
            // engine's reader pulls some in behind it on spare time.
            input.mark_resume_point();
        }
    }

    fn open(&self, options: &LoadOptions) -> Result<Opened, Error> {
        let mut dictionary = Dictionary::new();
        if !options.headers.is_empty() {
            let mut headers = String::new();
            for (key, value) in &options.headers {
                let _ = write!(headers, "{key}: {value}\r\n");
            }
            dictionary.set("headers", &headers);
        }
        if let Some(agent) = &options.user_agent {
            dictionary.set("user_agent", agent);
        }
        // Retry a connection that drops or a socket that fails, like mpv does
        // with --stream-lavf-o; not reconnect_streamed, which breaks
        // byte-range EDLs (see CLAUDE.md).
        dictionary.set("reconnect", "1");
        dictionary.set("reconnect_on_network_error", "1");
        dictionary.set("reconnect_delay_max", "5");
        // Not `multiple_requests`: a kept-alive connection behind a debrid
        // redirect ended mid-read ("File ended prematurely") on the first
        // seek, and nothing played at all.
        if let Some(timeout) = options.network_timeout {
            dictionary.set("rw_timeout", &timeout.as_micros().to_string());
        }
        // The engine reads http(s) itself when it can: several connections
        // and a chunk cache (net/source.rs). Otherwise FFmpeg's own client,
        // with the options above.
        let stopping = || self.shared.stopping();
        let source = net::Source::open(
            &options.url,
            &options.headers,
            options.user_agent.as_deref(),
            &stopping,
        )
        .map_err(|error| match error {
            net::OpenError::Unreachable(why) => Error::Unreachable(why),
            net::OpenError::Stopped => Error::Ffmpeg(ffmpeg_next::Error::Exit),
        })?;
        let io = source.and_then(|source| {
            let shared = self.shared.clone();
            net::CustomIo::new(source, move || shared.stopping())
        });
        self.shared
            .reads_network
            .store(io.is_some(), Ordering::Release);
        let shared = self.shared.clone();
        Ok(open::open(
            &options.url,
            dictionary,
            move || shared.stopping(),
            io,
        )?)
    }

    /// Decoders for every subtitle stream, on one thread, and the file's
    /// fonts for them. Returns the streams whose packets it wants.
    fn start_subtitles(
        &self,
        input: &Input,
        selected: Option<&Track>,
        workers: &mut Vec<thread::JoinHandle<()>>,
    ) -> Vec<usize> {
        let Some(subtitles) = self.subtitles.clone() else {
            return Vec::new();
        };
        subtitle_decode::load_fonts(input, &subtitles);
        let (video, streams) = {
            let info = self.shared.info.read();
            let streams: Vec<usize> = info
                .tracks
                .iter()
                .filter(|t| t.kind == TrackKind::Subtitle && !t.external)
                .map(|t| t.stream_index)
                .collect();
            (info.video_size.unwrap_or((0, 0)), streams)
        };
        let decoders: HashMap<usize, SubtitleDecoder> = streams
            .iter()
            .filter_map(|&index| {
                SubtitleDecoder::open(input, index, TrackKey::Stream(index), &subtitles, video)
                    .map(|decoder| (index, decoder))
            })
            .collect();
        subtitles.select(selected.map(|t| TrackKey::Stream(t.stream_index)));
        if decoders.is_empty() {
            return Vec::new();
        }
        let wanted: Vec<usize> = decoders.keys().copied().collect();
        let shared = self.shared.clone();
        match thread::Builder::new()
            .name("player-subtitles".to_owned())
            .spawn(move || subtitle_decode::run(decoders, &shared, &subtitles))
        {
            Ok(handle) => {
                workers.push(handle);
                wanted
            }
            Err(error) => {
                log::warn!("could not start the subtitle decoder: {error}");
                Vec::new()
            }
        }
    }

    fn start_video(
        &self,
        input: &Input,
        stream_index: usize,
        workers: &mut Vec<thread::JoinHandle<()>>,
    ) -> Option<Selected> {
        let stream = input.stream(stream_index)?;
        let time_base = stream.time_base();
        let rate = stream.avg_frame_rate();
        let frame_duration = if rate.numerator() > 0 && rate.denominator() > 0 {
            1.0 / f64::from(rate)
        } else {
            1.0 / 24.0
        };
        let mut context = match codec::Context::from_parameters(stream.parameters()) {
            Ok(context) => context,
            Err(error) => {
                log::warn!("no decoder context for video: {error}");
                return None;
            }
        };
        context.set_threading(codec::threading::Config {
            kind: codec::threading::Type::Frame,
            count: 0,
        });
        // The queue, the frame on screen and one being rendered.
        let hardware = self.core.hwdec.attach(&mut context, frames::CAPACITY + 2);
        let decoder = match context.decoder().video() {
            Ok(decoder) => decoder,
            Err(error) => {
                log::warn!("cannot decode the video track: {error}");
                return None;
            }
        };
        let shared = self.shared.clone();
        let core = self.core.clone();
        match thread::Builder::new()
            .name("player-video".to_owned())
            .spawn(move || {
                decode::run_video(decoder, hardware, time_base, frame_duration, &shared, &core);
            }) {
            Ok(handle) => workers.push(handle),
            Err(error) => {
                log::warn!("could not start the video decoder: {error}");
                return None;
            }
        }
        Some(Selected {
            stream_index,
            time_base,
        })
    }

    fn spawn_audio(&self, initial: AudioSwitch, workers: &mut Vec<thread::JoinHandle<()>>) {
        let shared = self.shared.clone();
        let core = self.core.clone();
        let feed = self.feed.clone();
        let output = self.output;
        match thread::Builder::new()
            .name("player-audio-decode".to_owned())
            .spawn(move || decode::run_audio(initial, &feed, output, &shared, &core))
        {
            Ok(handle) => {
                self.shared.audio_decoder.store(true, Ordering::Release);
                workers.push(handle);
            }
            Err(error) => log::warn!("could not start the audio decoder: {error}"),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn demux(
        &self,
        input: &mut Opened,
        mut serial: u64,
        subtitle_streams: &[usize],
        video: Option<&Selected>,
        audio: &mut Option<Selected>,
        audio_streams: &HashMap<usize, Rational>,
        workers: &mut Vec<thread::JoinHandle<()>>,
    ) {
        let mut packet = Packet::empty();
        let mut paused = false;
        loop {
            if self.shared.stopping() {
                return;
            }
            // The reader fills the cache while paused (see
            // `Source::set_paused`); this loop keeps turning while the
            // packet queues are full, so it is where the pause is seen.
            if self.core.is_paused() != paused {
                paused = !paused;
                input.set_paused(paused);
            }
            let (seek_to, switch_audio) = {
                let mut requests = self.shared.requests.lock();
                (requests.seek.take(), requests.audio.take())
            };
            if let Some(request) = switch_audio {
                self.switch_audio(input, audio, request, workers);
            }
            self.shared
                .audio_packets
                .forget_before(self.core.clock.now() - KEEP_BEHIND_S);
            if let Some((target, new_serial)) = seek_to {
                if let Err(error) = seek(input, target)
                    && !self.shared.stopping()
                {
                    log::warn!("seek to {target:.1}s failed: {error}");
                }
                serial = new_serial;
                self.shared.demux_eof.store(false, Ordering::Release);
                continue;
            }
            if self.shared.demux_finished() || self.buffer_full(video.is_some(), audio.is_some()) {
                let mut requests = self.shared.requests.lock();
                if requests.seek.is_none() && requests.audio.is_none() && !self.shared.stopping() {
                    self.shared.wake.wait_for(&mut requests, IDLE_WAIT);
                }
                continue;
            }
            match packet.read(input) {
                Ok(()) => {
                    let index = packet.stream();
                    self.shared
                        .bytes_read
                        .fetch_add(packet.size() as u64, Ordering::Relaxed);
                    let taken = std::mem::replace(&mut packet, Packet::empty());
                    if let Some(v) = video.filter(|v| v.stream_index == index) {
                        let time = taken.pts().map(|ts| ts as f64 * f64::from(v.time_base));
                        self.shared.video_packets.push(taken, serial, time);
                    } else if let Some(&time_base) = audio_streams.get(&index) {
                        let time = taken.pts().map(|ts| ts as f64 * f64::from(time_base));
                        self.shared.audio_packets.push(index, taken, serial, time);
                    } else if subtitle_streams.contains(&index) {
                        self.shared.subtitle_packets.push(taken, serial, None);
                    }
                }
                Err(ffmpeg_next::Error::Eof) => self.finish(serial),
                Err(ffmpeg_next::Error::Exit) if self.shared.stopping() => return,
                Err(error) => {
                    // A read that fails after ffmpeg's own reconnects is where
                    // the stream ends, as it would in mpv: the embedder tells a
                    // drop from the real end by position.
                    log::warn!("reading the stream failed: {error}");
                    self.finish(serial);
                }
            }
        }
    }

    fn finish(&self, serial: u64) {
        self.shared.demux_eof.store(true, Ordering::Release);
        self.shared.video_packets.set_eof(serial);
        self.shared.audio_packets.set_eof(serial);
    }

    fn buffer_full(&self, has_video: bool, has_audio: bool) -> bool {
        let shared = &self.shared;
        if shared.video_packets.bytes() + shared.audio_packets.bytes() >= MAX_QUEUED_BYTES {
            return true;
        }
        let video_full = !has_video || shared.video_packets.span() >= READAHEAD_S;
        let audio_full = !has_audio || shared.audio_packets.span() >= READAHEAD_S;
        video_full && audio_full
    }

    /// Carries out an audio track request. Between two tracks while
    /// playing, the new track's decoder is queued ahead of its packets and
    /// nothing is read again; to or from no audio, the seek the player sends
    /// alongside re-reads everything.
    fn switch_audio(
        &self,
        input: &Input,
        audio: &mut Option<Selected>,
        request: AudioRequest,
        workers: &mut Vec<thread::JoinHandle<()>>,
    ) {
        let stream_index = request.track.and_then(|id| {
            self.shared
                .info
                .read()
                .tracks
                .iter()
                .find(|t| t.kind == TrackKind::Audio && t.id == id)
                .map(|t| t.stream_index)
        });
        let Some(stream_index) = stream_index else {
            *audio = None;
            self.shared.info.write().selected[TrackKind::Audio as usize] = None;
            self.shared.audio_packets.select(None, 0.0, None);
            return;
        };
        let Some((selected, decoder)) =
            open_audio(input, stream_index, request.at, request.generation)
        else {
            return;
        };
        *audio = Some(selected);
        self.shared.info.write().selected[TrackKind::Audio as usize] = request.track;
        let from = request.at.unwrap_or_else(|| self.core.clock.now());
        if self.shared.audio_decoder.load(Ordering::Acquire) {
            self.shared
                .audio_packets
                .select(Some(stream_index), from, Some(decoder));
        } else {
            self.shared
                .audio_packets
                .select(Some(stream_index), from, None);
            self.spawn_audio(decoder, workers);
        }
        if request.at.is_some() {
            log::info!(
                "audio switched to track {} in place",
                request.track.unwrap_or(0)
            );
        }
    }
}

/// Every audio track's stream, with its time base.
fn audio_time_bases(input: &Input, tracks: &[Track]) -> HashMap<usize, Rational> {
    tracks
        .iter()
        .filter(|t| t.kind == TrackKind::Audio)
        .filter_map(|t| {
            let stream = input.stream(t.stream_index)?;
            Some((t.stream_index, stream.time_base()))
        })
        .collect()
}

fn open_audio(
    input: &Input,
    stream_index: usize,
    start: Option<f64>,
    generation: u64,
) -> Option<(Selected, AudioSwitch)> {
    let stream = input.stream(stream_index)?;
    let time_base = stream.time_base();
    let decoder = codec::Context::from_parameters(stream.parameters())
        .and_then(|context| context.decoder().audio());
    match decoder {
        Ok(decoder) => Some((
            Selected {
                stream_index,
                time_base,
            },
            AudioSwitch {
                decoder,
                time_base,
                start,
                generation,
            },
        )),
        Err(error) => {
            log::warn!("cannot decode audio stream {stream_index}: {error}");
            None
        }
    }
}

/// How far before the resume point a keyframe may be and still be where a
/// resume starts. Remuxes put one every second or few; a file with a much
/// longer interval starts exactly instead, rather than replaying half a scene.
const MAX_KEYFRAME_LEAD_S: f64 = 10.0;

/// The time of the keyframe at or before `target` seconds on `stream`, from
/// the file's own index (Matroska's cues, MP4's sample table), which the seek
/// to `target` has just read. None without one.
fn keyframe_before(input: &Input, stream: usize, target: f64) -> Option<f64> {
    let stream = input.stream(stream)?;
    let time_base = stream.time_base();
    if time_base.numerator() <= 0 || time_base.denominator() <= 0 {
        return None;
    }
    let units = f64::from(time_base.denominator()) / f64::from(time_base.numerator());
    let ts = (target * units) as i64;
    // SAFETY: a live stream of `input`; the index is read only, and the
    // entry returned stays valid while the index is not modified, which it
    // is not before this returns.
    unsafe {
        let raw = stream.as_ptr().cast_mut();
        let index = ffi::av_index_search_timestamp(raw, ts, ffi::AVSEEK_FLAG_BACKWARD);
        if index < 0 {
            return None;
        }
        let entry = ffi::avformat_index_get_entry(raw, index);
        if entry.is_null() {
            return None;
        }
        let seconds = (*entry).timestamp as f64 / units;
        (seconds <= target && seconds >= 0.0).then_some(seconds)
    }
}

/// Seeks to the keyframe at or before `target` seconds; decoders drop what
/// lies between it and the target.
fn seek(input: &mut Input, target: f64) -> Result<(), ffmpeg_next::Error> {
    let ts = (target * f64::from(ffi::AV_TIME_BASE)) as i64;
    input.seek(ts, ..ts)
}

/// Tells the demuxer to skip streams nobody decodes, so it does not parse
/// them.
fn discard_all_but(input: &mut Input, playing: &[usize]) {
    // SAFETY: the format context is valid for `input`'s lifetime and its
    // stream array has nb_streams entries.
    unsafe {
        let context = input.as_mut_ptr();
        for index in 0..(*context).nb_streams as usize {
            let stream = *(*context).streams.add(index);
            (*stream).discard = if playing.contains(&index) {
                ffi::AVDiscard::AVDISCARD_DEFAULT
            } else {
                ffi::AVDiscard::AVDISCARD_ALL
            };
        }
    }
}

fn duration(input: &Input) -> Option<f64> {
    let duration = input.duration();
    (duration > 0).then(|| duration as f64 / f64::from(ffi::AV_TIME_BASE))
}

fn chapters(input: &Input) -> Vec<Chapter> {
    input
        .chapters()
        .map(|chapter| Chapter {
            start: chapter.start() as f64 * f64::from(chapter.time_base()),
            title: chapter
                .metadata()
                .get("title")
                .unwrap_or_default()
                .to_owned(),
        })
        .collect()
}

/// The size the picture is shown at: stored size with the sample aspect
/// ratio applied, as mpv's `dwidth`/`dheight`.
fn display_size(input: &Input, stream_index: usize) -> Option<(u32, u32)> {
    let stream = input.stream(stream_index)?;
    // SAFETY: the parameters belong to a stream of the open input.
    let raw = unsafe { &*stream.parameters().as_ptr() };
    let (width, height) = (
        u32::try_from(raw.width).ok()?,
        u32::try_from(raw.height).ok()?,
    );
    if width == 0 || height == 0 {
        return None;
    }
    let sar = raw.sample_aspect_ratio;
    if sar.num > 0 && sar.den > 0 && sar.num != sar.den {
        let scaled = (f64::from(width) * f64::from(sar.num) / f64::from(sar.den)).round() as u32;
        return Some((scaled, height));
    }
    Some((width, height))
}

/// A URL for the log: scheme and host, never a path, which for a debrid
/// link is a credential.
pub(crate) fn redact(url: &str) -> String {
    match url.split_once("://") {
        Some((scheme, rest)) => {
            let host = rest.split(['/', '?']).next().unwrap_or_default();
            format!("{scheme}://{host}/…")
        }
        None => "a local file".to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::redact;

    #[test]
    fn a_logged_url_keeps_only_its_host() {
        assert_eq!(
            redact("https://cdn.example/a/b/token?x=1"),
            "https://cdn.example/…"
        );
        assert_eq!(redact("/tmp/file.mkv"), "a local file");
    }
}
