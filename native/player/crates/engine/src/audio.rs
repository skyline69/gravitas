//! Audio output: the device, the mixer that feeds it, and the clock it drives.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use crossbeam_channel::{Receiver, Sender, TryRecvError, bounded};

use ffmpeg_next::util::channel_layout::ChannelLayout;

use crate::core::{AudioState, Core};
use crate::error::{Error, Result};

/// Decoded audio, resampled to the output's format: interleaved `f32` at the
/// output's rate and channel count.
#[derive(Debug)]
pub(crate) struct Chunk {
    pub(crate) serial: u64,
    /// The audio track selection it was decoded for.
    pub(crate) generation: u64,
    /// Media time of the first sample, in seconds.
    pub(crate) pts: f64,
    pub(crate) samples: Vec<f32>,
}

/// What travels from the audio decoder to the mixer.
#[derive(Debug)]
pub(crate) enum Feed {
    Chunk(Chunk),
    /// Every sample of this serial has been sent.
    End(u64),
}

/// The format the output plays, which the decoder resamples to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct OutputFormat {
    pub(crate) rate: u32,
    pub(crate) channels: u16,
    /// Which speakers the channels are, when there are more than two.
    pub(crate) speakers: Speakers,
    /// The device's channel `i` plays the resampler's channel `order[i]`:
    /// FFmpeg's order in, the device's out.
    pub(crate) order: [u8; 8],
    /// The audio system it plays through, for the log.
    pub(crate) host: &'static str,
}

/// A multichannel speaker set the output plays, or none.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Speakers {
    /// Mono or stereo, in FFmpeg's default order.
    Plain,
    /// FFmpeg's `5.1(back)`: front left, front right, centre, LFE, rear left,
    /// rear right.
    FivePointOneBack,
    /// FFmpeg's `5.1`: the surrounds at the sides.
    FivePointOneSide,
    /// FFmpeg's `7.1`: `5.1(back)`, then side left and side right.
    SevenPointOne,
}

/// The identity: the device takes FFmpeg's order.
const IN_ORDER: [u8; 8] = [0, 1, 2, 3, 4, 5, 6, 7];

impl OutputFormat {
    fn plain(rate: u32, channels: u16, host: &'static str) -> Self {
        Self {
            rate,
            channels,
            speakers: Speakers::Plain,
            order: IN_ORDER,
            host,
        }
    }

    /// The speakers the resampler produces, in FFmpeg's order. The resampler
    /// maps a source's side channels onto rear ones, and the other way, where
    /// it has to.
    pub(crate) fn layout(self) -> ChannelLayout {
        match self.speakers {
            Speakers::FivePointOneBack => ChannelLayout::_5POINT1_BACK,
            Speakers::FivePointOneSide => ChannelLayout::_5POINT1,
            Speakers::SevenPointOne => ChannelLayout::_7POINT1,
            Speakers::Plain => ChannelLayout::default(i32::from(self.channels)),
        }
    }

    fn reordered(self) -> bool {
        self.order[..usize::from(self.channels)] != IN_ORDER[..usize::from(self.channels)]
    }
}

/// A speaker, as far as 5.1 and 7.1 name them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Speaker {
    FrontLeft,
    FrontRight,
    Centre,
    Lfe,
    SideLeft,
    SideRight,
    BackLeft,
    BackRight,
}

/// The speaker a CoreAudio channel label names (`kAudioChannelLabel_*`), if
/// it is one of 5.1's or 7.1's. CoreAudio's "surround" pair are the sides of
/// its 7.1 layouts, and its "rear surround" pair the backs.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn coreaudio_speaker(label: u32) -> Option<Speaker> {
    Some(match label {
        1 => Speaker::FrontLeft,
        2 => Speaker::FrontRight,
        3 => Speaker::Centre,
        4 => Speaker::Lfe,
        5 | 10 => Speaker::SideLeft,
        6 | 11 => Speaker::SideRight,
        33 => Speaker::BackLeft,
        34 => Speaker::BackRight,
        _ => return None,
    })
}

/// The speaker set and channel order for a device whose channels are
/// `labels` (CoreAudio channel labels, in channel order): 5.1 or 7.1 when
/// every channel names a distinct one of their speakers, else None -- an
/// unlabelled or unusual device plays stereo, since a centre channel in the
/// wrong speaker is worse than a downmix.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn speaker_order(labels: &[u32]) -> Option<(Speakers, [u8; 8])> {
    use Speaker::{BackLeft, BackRight, Centre, FrontLeft, FrontRight, Lfe, SideLeft, SideRight};
    let device: Vec<Speaker> = labels
        .iter()
        .map(|&l| coreaudio_speaker(l))
        .collect::<Option<_>>()?;
    let has = |s: Speaker| device.contains(&s);
    let (speakers, ffmpeg): (Speakers, &[Speaker]) = match device.len() {
        6 if has(SideLeft) && has(SideRight) => (
            Speakers::FivePointOneSide,
            &[FrontLeft, FrontRight, Centre, Lfe, SideLeft, SideRight],
        ),
        6 if has(BackLeft) && has(BackRight) => (
            Speakers::FivePointOneBack,
            &[FrontLeft, FrontRight, Centre, Lfe, BackLeft, BackRight],
        ),
        8 => (
            Speakers::SevenPointOne,
            &[
                FrontLeft, FrontRight, Centre, Lfe, BackLeft, BackRight, SideLeft, SideRight,
            ],
        ),
        _ => return None,
    };
    let mut order = IN_ORDER;
    for (channel, speaker) in device.iter().enumerate() {
        // Each speaker once: a device naming one twice is not trusted.
        if device.iter().filter(|&&s| s == *speaker).count() != 1 {
            return None;
        }
        order[channel] = ffmpeg.iter().position(|s| s == speaker)? as u8;
    }
    Some((speakers, order))
}

/// The period the output asks the device for. A PulseAudio server left to
/// choose targets two seconds of buffer, which is two seconds between a
/// load, seek, pause or audio switch and hearing it (measured: the clock sat
/// at 0 for 2.2 s). Twenty milliseconds, double-buffered by the host, plus
/// whatever the sink itself adds (which the playback timestamps report).
/// Other hosts keep their own defaults, which are already short.
fn buffer_size(host: &cpal::Host, rate: u32) -> cpal::BufferSize {
    #[cfg(target_os = "linux")]
    if host.id() == cpal::HostId::PulseAudio {
        return cpal::BufferSize::Fixed(rate / 50);
    }
    let _ = (host, rate);
    cpal::BufferSize::Default
}

/// What to play on a device that takes `device_channels`: 5.1 and 7.1 as
/// they are only where the host states which speaker each channel is --
/// PulseAudio's channel map is explicit and the server maps it onto whatever
/// the sink is; a CoreAudio device states its own layout, and the channels
/// go out in its order. Elsewhere the order of a multichannel buffer is the
/// host's convention, unchecked here, and a centre channel in the wrong
/// place is worse than a downmix: everything else plays stereo.
fn output_speakers(host: &cpal::Host, device_channels: u16) -> (u16, Speakers, [u8; 8]) {
    #[cfg(target_os = "linux")]
    if host.id() == cpal::HostId::PulseAudio {
        match device_channels {
            6 => return (6, Speakers::FivePointOneBack, IN_ORDER),
            8 => return (8, Speakers::SevenPointOne, IN_ORDER),
            _ => {}
        }
    }
    #[cfg(target_os = "macos")]
    if matches!(device_channels, 6 | 8)
        && let Some(labels) =
            crate::coreaudio::default_output().and_then(crate::coreaudio::speakers)
        && labels.len() == usize::from(device_channels)
        && let Some((speakers, order)) = speaker_order(&labels)
    {
        return (device_channels, speakers, order);
    }
    let _ = host;
    (device_channels.clamp(1, 2), Speakers::Plain, IN_ORDER)
}

/// Where the engine's audio goes.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum AudioBackend {
    /// The system's default output device, falling back to [`Self::Null`]
    /// when there is none.
    #[default]
    System,
    /// Consumes samples in real time and plays nothing. The clock still runs
    /// from it, so playback behaves exactly as with a device -- which is what
    /// tests and machines without one need.
    Null,
}

/// How many chunks may wait between the decoder and the device. At ~1024
/// samples a chunk that is around a second of audio at 48 kHz.
const FEED_CHUNKS: usize = 48;

/// How long a track switch may leave the output silent before that counts
/// as running dry. The new track's decoder normally delivers within a
/// callback or two; until then the clock runs on rather than stopping video
/// for a gap of milliseconds.
const SWITCH_GRACE: Duration = Duration::from_millis(500);

/// How much of what the output played `OutputTap` keeps: 100 ms.
const TAP_SECONDS: f64 = 0.1;

/// The last moments of what the output played, for checks and diagnostics
/// (`Player::recent_output`). Off until first asked for; after that the
/// mixer copies each buffer in, skipping any buffer whose lock is contended
/// rather than ever waiting on the device's thread.
#[derive(Debug, Default)]
pub(crate) struct OutputTap {
    enabled: AtomicBool,
    samples: parking_lot::Mutex<std::collections::VecDeque<f32>>,
}

impl OutputTap {
    /// What was played lately, interleaved; starts keeping it on first call.
    pub(crate) fn recent(&self) -> Vec<f32> {
        self.enabled.store(true, Ordering::Release);
        self.samples.lock().iter().copied().collect()
    }

    fn record(&self, played: &[f32], format: OutputFormat) {
        if !self.enabled.load(Ordering::Acquire) {
            return;
        }
        let Some(mut samples) = self.samples.try_lock() else {
            return;
        };
        let keep = (f64::from(format.rate) * TAP_SECONDS) as usize * usize::from(format.channels);
        samples.extend(played);
        let excess = samples.len().saturating_sub(keep);
        samples.drain(..excess);
    }
}

/// The null output's period.
const NULL_PERIOD: Duration = Duration::from_millis(10);

/// The running output. Dropping it stops the device.
#[derive(Debug)]
pub(crate) struct AudioOutput {
    pub(crate) format: OutputFormat,
    pub(crate) feed: Sender<Feed>,
    stop: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}

impl AudioOutput {
    /// Opens `backend`. The device lives on a thread of its own for the
    /// player's whole life: cpal's stream is not `Send` on every platform,
    /// and a device opened once cannot fail halfway through a playback.
    pub(crate) fn open(backend: AudioBackend, core: Arc<Core>) -> Result<Self> {
        let (feed, receiver) = bounded(FEED_CHUNKS);
        let stop = Arc::new(AtomicBool::new(false));
        let (ready_tx, ready_rx) = bounded::<Result<OutputFormat>>(1);
        let thread = {
            let stop = stop.clone();
            thread::Builder::new()
                .name("player-audio".to_owned())
                .spawn(move || run_output(backend, core, receiver, &stop, &ready_tx))
                .map_err(|source| Error::Thread {
                    what: "the audio output",
                    source,
                })?
        };
        let format = ready_rx
            .recv()
            .map_err(|_| Error::Audio("the audio thread ended before it started".to_owned()))??;
        Ok(Self {
            format,
            feed,
            stop,
            thread: Some(thread),
        })
    }
}

impl Drop for AudioOutput {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            thread.thread().unpark();
            let _ = thread.join();
        }
    }
}

fn run_output(
    backend: AudioBackend,
    core: Arc<Core>,
    receiver: Receiver<Feed>,
    stop: &AtomicBool,
    ready: &Sender<Result<OutputFormat>>,
) {
    if backend == AudioBackend::System {
        let extra_latency = Arc::new(AtomicU64::new(0));
        match open_device(core.clone(), receiver.clone(), extra_latency.clone()) {
            Ok((stream, format)) => {
                log::info!(
                    "audio output through {}: {} Hz, {} channels{}",
                    format.host,
                    format.rate,
                    format.channels,
                    if format.reordered() {
                        " (reordered to the device's speakers)"
                    } else {
                        ""
                    }
                );
                let _ = ready.send(Ok(format));
                while !stop.load(Ordering::Acquire) {
                    refresh_latency(&extra_latency);
                    thread::park_timeout(LATENCY_REFRESH);
                }
                drop(stream);
                return;
            }
            Err(error) => log::warn!("no audio device ({error}); playing to a silent output"),
        }
    }
    let format = OutputFormat::plain(48_000, 2, "a silent output");
    let _ = ready.send(Ok(format));
    let mut mixer = Mixer::new(core, receiver, format);
    let frames = (format.rate as usize * NULL_PERIOD.as_millis() as usize) / 1000;
    let mut buffer = vec![0.0f32; frames * usize::from(format.channels)];
    let mut next = Instant::now();
    while !stop.load(Ordering::Acquire) {
        mixer.fill(&mut buffer, Instant::now());
        next += NULL_PERIOD;
        let now = Instant::now();
        if next > now {
            thread::park_timeout(next - now);
        } else {
            next = now;
        }
    }
}

/// How often the output's extra latency is read again: a default device
/// that changes (headphones plugged in, AirPods connected) takes its
/// latency with it.
const LATENCY_REFRESH: Duration = Duration::from_secs(1);

/// Latency the host's playback timestamp leaves out, in nanoseconds: on
/// macOS the output stream's own (see `coreaudio.rs`).
fn refresh_latency(extra: &AtomicU64) {
    #[cfg(target_os = "macos")]
    {
        let latency = crate::coreaudio::default_output()
            .and_then(crate::coreaudio::stream_latency)
            .unwrap_or_default();
        let nanos = u64::try_from(latency.as_nanos()).unwrap_or(u64::MAX);
        if extra.swap(nanos, Ordering::Relaxed) != nanos {
            log::info!(
                "audio output stream latency: {:.1} ms",
                latency.as_secs_f64() * 1000.0
            );
        }
    }
    let _ = extra;
}

fn open_device(
    core: Arc<Core>,
    receiver: Receiver<Feed>,
    extra_latency: Arc<AtomicU64>,
) -> Result<(cpal::Stream, OutputFormat)> {
    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .ok_or_else(|| Error::Audio("no default output device".to_owned()))?;
    let supported = device
        .default_output_config()
        .map_err(|e| Error::Audio(e.to_string()))?;
    let (channels, speakers, order) = output_speakers(&host, supported.channels());
    let format = OutputFormat {
        rate: supported.sample_rate(),
        channels,
        speakers,
        order,
        host: host.id().name(),
    };
    refresh_latency(&extra_latency);
    let config = cpal::StreamConfig {
        channels,
        sample_rate: format.rate,
        buffer_size: buffer_size(&host, format.rate),
    };
    let mut mixer = Mixer::new(core, receiver, format);
    let stream = device
        .build_output_stream(
            config,
            move |data: &mut [f32], info: &cpal::OutputCallbackInfo| {
                let stamp = info.timestamp();
                let latency = stamp.playback.duration_since(stamp.callback)
                    + Duration::from_nanos(extra_latency.load(Ordering::Relaxed));
                mixer.fill(data, Instant::now() + latency);
            },
            |error| log::warn!("audio output error: {error}"),
            None,
        )
        .map_err(|e| Error::Audio(e.to_string()))?;
    stream.play().map_err(|e| Error::Audio(e.to_string()))?;
    Ok((stream, format))
}

/// Turns chunks into device buffers and keeps the clock on the samples.
///
/// It runs on the device's own callback thread, so it never blocks: chunks
/// arrive through a bounded channel it only ever `try_recv`s, and everything
/// else it reads is atomic or held for a few instructions.
struct Mixer {
    core: Arc<Core>,
    receiver: Receiver<Feed>,
    format: OutputFormat,
    current: Option<(Chunk, usize)>,
    /// The track selection last heard.
    generation: u64,
    /// Since when a newer selection has had nothing to play.
    switching_since: Option<Instant>,
}

impl Mixer {
    fn new(core: Arc<Core>, receiver: Receiver<Feed>, format: OutputFormat) -> Self {
        Self {
            core,
            receiver,
            format,
            current: None,
            generation: 0,
            switching_since: None,
        }
    }

    /// Fills `out`, whose first sample will be heard at `heard_at`.
    fn fill(&mut self, out: &mut [f32], heard_at: Instant) {
        let serial = self.core.serial();
        let generation = self.core.audio_generation();
        let state = if self.core.is_paused() {
            AudioState::Idle
        } else {
            self.core.timeline.lock().audio_state(serial)
        };
        match state {
            AudioState::Idle => {
                // Paused, prerolling or no audio at all. Old chunks still have
                // to go, or a decoder blocked on a full channel could never
                // deliver the first chunk of a seek.
                self.discard_stale(serial, generation);
                out.fill(0.0);
                return;
            }
            AudioState::Finished => {
                out.fill(0.0);
                // Audio is over; video may still have frames to show, and
                // time goes on without it.
                self.core.clock.start();
                return;
            }
            AudioState::Playing => {}
        }
        let (written, first_pts) = self.mix(out, serial, generation, heard_at);
        out[written..].fill(0.0);
        self.core.tap.record(out, self.format);
        let clock = &self.core.clock;
        if let Some(pts) = first_pts {
            clock.anchor(pts, heard_at);
            self.core.set_starved(false);
            self.switching_since = None;
        } else if self.core.timeline.lock().audio_state(serial) == AudioState::Finished {
            clock.start();
        } else if self.generation != generation
            && self
                .switching_since
                .get_or_insert_with(Instant::now)
                .elapsed()
                < SWITCH_GRACE
        {
            // A track switch, its first audio still being decoded: the
            // clock extrapolates on, and video with it.
        } else {
            // Nothing to play: the network or the decoder is behind. Holding
            // the clock is what makes video wait with it.
            clock.stop();
            self.core.set_starved(true);
        }
    }

    /// Copies what the channel holds into `out`, scaled by the volume.
    /// Returns how many samples were written and the media time of the first.
    fn mix(
        &mut self,
        out: &mut [f32],
        serial: u64,
        generation: u64,
        heard_at: Instant,
    ) -> (usize, Option<f64>) {
        let channels = usize::from(self.format.channels);
        let rate = f64::from(self.format.rate);
        let gain = self.core.gain();
        if self
            .current
            .as_ref()
            .is_some_and(|(chunk, _)| chunk.serial != serial || chunk.generation != generation)
        {
            self.current = None;
        }
        let mut written = 0;
        let mut first_pts = None;
        while written < out.len() {
            if self.current.is_none() && !self.next_chunk(serial, generation) {
                break;
            }
            let Some((chunk, offset)) = self.current.as_mut() else {
                break;
            };
            if chunk.generation != self.generation {
                // The new track's first audio: it joins at the time these
                // samples will be heard, not where its decoding began.
                let at = heard_at + Duration::from_secs_f64((written / channels) as f64 / rate);
                let late = self.core.clock.at(at) - chunk.pts;
                let skip = if late > 0.0 {
                    (late * rate) as usize * channels
                } else {
                    0
                };
                if *offset + skip >= chunk.samples.len() {
                    self.current = None;
                    continue;
                }
                *offset += skip;
                self.generation = chunk.generation;
            }
            if first_pts.is_none() {
                first_pts = Some(chunk.pts + (*offset / channels) as f64 / rate);
            }
            let n = (chunk.samples.len() - *offset).min(out.len() - written);
            let (dst, src) = (
                &mut out[written..written + n],
                &chunk.samples[*offset..*offset + n],
            );
            if self.format.reordered() {
                // Both are whole frames: chunks and device buffers hold
                // whole frames, and every step here moves by whole frames.
                let order = &self.format.order[..channels];
                for (dst, src) in dst
                    .chunks_exact_mut(channels)
                    .zip(src.chunks_exact(channels))
                {
                    for (out, &from) in dst.iter_mut().zip(order) {
                        *out = src[usize::from(from)] * gain;
                    }
                }
            } else {
                for (dst, src) in dst.iter_mut().zip(src) {
                    *dst = src * gain;
                }
            }
            written += n;
            *offset += n;
            if *offset >= chunk.samples.len() {
                self.current = None;
            }
        }
        (written, first_pts)
    }

    /// Takes the next chunk of `serial` into `current`, dropping older ones.
    /// False when there is none to take right now.
    fn next_chunk(&mut self, serial: u64, generation: u64) -> bool {
        loop {
            match self.receiver.try_recv() {
                Ok(Feed::Chunk(chunk))
                    if chunk.serial == serial && chunk.generation == generation =>
                {
                    self.current = Some((chunk, 0));
                    return true;
                }
                Ok(Feed::End(end)) if end == serial => {
                    self.core.timeline.lock().audio_ended(serial);
                    return false;
                }
                Ok(_) => {} // from before the last seek or track switch
                Err(TryRecvError::Empty | TryRecvError::Disconnected) => return false,
            }
        }
    }

    /// Drops chunks from before the last seek or track switch without
    /// playing anything, keeping the first one of `serial` for when playback
    /// starts.
    fn discard_stale(&mut self, serial: u64, generation: u64) {
        if self
            .current
            .as_ref()
            .is_some_and(|(chunk, _)| chunk.serial != serial || chunk.generation != generation)
        {
            self.current = None;
        }
        if self.current.is_none() {
            self.next_chunk(serial, generation);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_labelled_5_1_device_gets_its_own_order() {
        // An HDMI receiver as CoreAudio commonly lays it out: L R C LFE Ls Rs.
        let (speakers, order) = speaker_order(&[1, 2, 3, 4, 5, 6]).expect("5.1");
        assert_eq!(speakers, Speakers::FivePointOneSide);
        assert_eq!(&order[..6], &[0, 1, 2, 3, 4, 5]);
        // One that puts the centre and LFE last: L R Ls Rs C LFE.
        let (_, order) = speaker_order(&[1, 2, 5, 6, 3, 4]).expect("5.1");
        assert_eq!(&order[..6], &[0, 1, 4, 5, 2, 3]);
        // Rear surrounds instead of side ones.
        let (speakers, _) = speaker_order(&[1, 2, 3, 4, 33, 34]).expect("5.1");
        assert_eq!(speakers, Speakers::FivePointOneBack);
    }

    #[test]
    fn a_labelled_7_1_device_gets_its_own_order() {
        // L R C LFE Ls Rs Rls Rrs: CoreAudio's sides first, FFmpeg's backs.
        let (speakers, order) = speaker_order(&[1, 2, 3, 4, 5, 6, 33, 34]).expect("7.1");
        assert_eq!(speakers, Speakers::SevenPointOne);
        assert_eq!(order, [0, 1, 2, 3, 6, 7, 4, 5]);
    }

    #[test]
    fn an_unlabelled_or_odd_device_plays_stereo() {
        // Unknown labels (0xFFFFFFFF), as the built-in speakers report.
        assert!(speaker_order(&[u32::MAX; 6]).is_none());
        // A speaker twice.
        assert!(speaker_order(&[1, 2, 3, 4, 5, 5]).is_none());
        // A height channel among the six.
        assert!(speaker_order(&[1, 2, 3, 4, 5, 13]).is_none());
        assert!(speaker_order(&[1, 2]).is_none());
    }

    #[test]
    fn the_mixer_writes_the_device_order() {
        let core = Arc::new(Core::new());
        let (feed, receiver) = bounded(4);
        let format = OutputFormat {
            rate: 48_000,
            channels: 6,
            speakers: Speakers::FivePointOneSide,
            // The device's L R Ls Rs C LFE.
            order: [0, 1, 4, 5, 2, 3, 6, 7],
            host: "test",
        };
        let mut mixer = Mixer::new(core, receiver, format);
        // Two frames of FFmpeg's FL FR FC LFE SL SR.
        let samples = vec![
            1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0,
        ];
        feed.send(Feed::Chunk(Chunk {
            serial: 0,
            generation: 0,
            pts: 0.0,
            samples,
        }))
        .unwrap();
        let mut out = [0.0; 12];
        let (written, _) = mixer.mix(&mut out, 0, 0, Instant::now());
        assert_eq!(written, 12);
        let expected = [
            1.0, 2.0, 5.0, 6.0, 3.0, 4.0, 11.0, 12.0, 15.0, 16.0, 13.0, 14.0,
        ];
        assert!(
            out.iter().zip(expected).all(|(a, b)| (a - b).abs() < 1e-6),
            "{out:?}"
        );
    }
}
