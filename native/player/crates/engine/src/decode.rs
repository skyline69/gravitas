//! The decoder threads: packets in, frames (video) or chunks (audio) out.

use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::time::Duration;

use crossbeam_channel::{SendTimeoutError, Sender};
use ffmpeg_next::format::Sample;
use ffmpeg_next::format::sample::Type as SampleLayout;
use ffmpeg_next::software::resampling;
use ffmpeg_next::util::channel_layout::ChannelLayout;
use ffmpeg_next::{Rational, codec, ffi, frame};

use crate::audio::{Chunk, Feed, OutputFormat};
use crate::audio_queue::AudioPop;
use crate::core::{Core, Stage};
use crate::frames::{Pushed, VideoFrame};
use crate::hwdec::{self, Attached};
use crate::queue::Pop;
use crate::session::SessionShared;

/// How long a decoder waits for a packet before looking at its stop flag.
const POLL: Duration = Duration::from_millis(50);

/// A decoder to switch to, with the time base of the stream it decodes.
pub(crate) struct AudioSwitch {
    pub(crate) decoder: codec::decoder::Audio,
    pub(crate) time_base: Rational,
    /// Where the new track takes over (the playhead when it was selected):
    /// what it decodes before that is dropped. None for a first decoder,
    /// which starts where its serial does.
    pub(crate) start: Option<f64>,
    /// The track selection this decoder belongs to (see
    /// `Core::audio_generation`).
    pub(crate) generation: u64,
}

impl std::fmt::Debug for AudioSwitch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("AudioSwitch")
            .field("time_base", &self.time_base)
            .finish_non_exhaustive()
    }
}

/// Where a serial begins, as the timeline knows it: the seek target that
/// decoded output before it is trimmed against.
fn target_of(core: &Core, serial: u64) -> Option<f64> {
    let timeline = core.timeline.lock();
    (timeline.serial == serial).then_some(timeline.target)
}

fn seconds(ts: i64, time_base: Rational) -> f64 {
    ts as f64 * f64::from(time_base)
}

/// Decodes video until the session stops.
///
/// `hardware` is the device the decoder was set up for, if any; FFmpeg can
/// still decide on software for a stream that device cannot take, so the
/// frames themselves say which one is in use.
pub(crate) fn run_video(
    mut decoder: codec::decoder::Video,
    hardware: Option<Attached>,
    time_base: Rational,
    frame_duration: f64,
    session: &Arc<SessionShared>,
    core: &Arc<Core>,
) {
    let mut state = VideoState {
        serial: None,
        target: 0.0,
        reported: false,
        next_pts: 0.0,
        time_base,
        frame_duration,
        hardware,
        decided: false,
    };
    while !session.stopping() {
        let Some(item) = session.video_packets.pop(POLL) else {
            continue;
        };
        match item {
            Pop::Closed => return,
            Pop::Packet(packet, serial) => {
                state.enter(serial, &mut decoder, core);
                if serial != core.serial() {
                    continue;
                }
                if let Err(error) = decoder.send_packet(&packet) {
                    log::debug!("video decoder refused a packet: {error}");
                }
                if !state.drain(&mut decoder, session, core) {
                    return;
                }
            }
            Pop::Eof(serial) => {
                state.enter(serial, &mut decoder, core);
                let _ = decoder.send_eof();
                if !state.drain(&mut decoder, session, core) {
                    return;
                }
                decoder.flush();
                core.timeline.lock().video_ended(serial);
            }
        }
    }
}

struct VideoState {
    serial: Option<u64>,
    target: f64,
    reported: bool,
    /// Where the next frame falls when the decoder gives it no timestamp.
    next_pts: f64,
    time_base: Rational,
    frame_duration: f64,
    /// The hardware device the decoder was set up for.
    hardware: Option<Attached>,
    /// Whether the first frame has told which decoder is in use.
    decided: bool,
}

impl VideoState {
    /// Resets the decoder when packets of a new serial arrive.
    fn enter(&mut self, serial: u64, decoder: &mut codec::decoder::Video, core: &Core) {
        if self.serial == Some(serial) {
            return;
        }
        decoder.flush();
        self.serial = Some(serial);
        self.target = target_of(core, serial).unwrap_or(0.0);
        self.next_pts = self.target;
        self.reported = false;
    }

    /// Hands every frame the decoder has to the queue. False when the
    /// session is shutting down.
    fn drain(
        &mut self,
        decoder: &mut codec::decoder::Video,
        session: &SessionShared,
        core: &Core,
    ) -> bool {
        let Some(serial) = self.serial else {
            return true;
        };
        let frames = &session.frames;
        let mut picture = frame::Video::empty();
        while decoder.receive_frame(&mut picture).is_ok() {
            if !self.decided {
                self.decided = true;
                self.report_decoder(&picture, session);
            }
            let pts = picture
                .timestamp()
                .map_or(self.next_pts, |ts| seconds(ts, self.time_base));
            self.next_pts = pts + self.frame_duration;
            // Exact seek: frames before the target were only decoded to reach
            // it. One that is still on screen at the target is kept.
            if pts + self.frame_duration <= self.target {
                continue;
            }
            let surface = std::mem::replace(&mut picture, frame::Video::empty());
            let keep = self.hardware.as_ref().is_some_and(|h| h.on_gpu);
            let Some(frame) = (if keep {
                Some(surface)
            } else {
                hwdec::to_memory(surface)
            }) else {
                log::debug!("a decoded frame could not be copied out of video memory");
                continue;
            };
            match frames.push(VideoFrame { frame, pts, serial }) {
                Pushed::Queued => {
                    if !self.reported {
                        self.reported = true;
                        core.ready(Stage::Video, serial);
                    }
                }
                Pushed::Dropped => return true,
                Pushed::Closed => return false,
            }
        }
        true
    }

    fn report_decoder(&self, picture: &frame::Video, session: &SessionShared) {
        // SAFETY: a valid decoded frame; the lookup only reads its side data.
        let hdr10_plus = !unsafe {
            ffi::av_frame_get_side_data(
                picture.as_ptr(),
                ffi::AVFrameSideDataType::AV_FRAME_DATA_DYNAMIC_HDR_PLUS,
            )
        }
        .is_null();
        session.hdr10_plus.store(hdr10_plus, Ordering::Release);
        let engaged = self
            .hardware
            .as_ref()
            .filter(|_| hwdec::on_hardware(picture));
        match (engaged, &self.hardware) {
            (Some(Attached { name, on_gpu: true }), _) => {
                log::info!("video decoding on the GPU ({name}); frames stay there");
            }
            (Some(Attached { name, .. }), _) => {
                log::info!("video decoding on the GPU ({name}, copied back)");
            }
            (None, Some(Attached { name, .. })) => {
                log::info!("video decoding in software ({name} declined the stream)");
            }
            (None, None) => log::info!("video decoding in software"),
        }
        session
            .hardware_decoder
            .lock()
            .clone_from(&engaged.map(|h| h.name.clone()));
    }
}

/// Decodes audio until the session stops. A track switch arrives in the
/// packet stream itself (`AudioPop::Switch`), ahead of the new track's first
/// packet.
pub(crate) fn run_audio(
    initial: AudioSwitch,
    feed: &Sender<Feed>,
    format: OutputFormat,
    session: &Arc<SessionShared>,
    core: &Arc<Core>,
) {
    let mut state = AudioState {
        decoder: initial.decoder,
        time_base: initial.time_base,
        generation: initial.generation,
        resampler: None,
        serial: None,
        target: 0.0,
        reported: false,
        next_pts: 0.0,
        format,
        profile: None,
    };
    while !session.stopping() {
        let Some(item) = session.audio_packets.pop(POLL) else {
            continue;
        };
        match item {
            AudioPop::Closed => return,
            AudioPop::Switch(switch) => state.switch(switch),
            AudioPop::Packet(packet, serial) => {
                state.enter(serial, core);
                if serial != core.serial() {
                    continue;
                }
                if let Err(error) = state.decoder.send_packet(&packet) {
                    log::debug!("audio decoder refused a packet: {error}");
                }
                if !state.drain(feed, session, core) {
                    return;
                }
            }
            AudioPop::Eof(serial) => {
                state.enter(serial, core);
                let _ = state.decoder.send_eof();
                if !state.drain(feed, session, core) {
                    return;
                }
                state.decoder.flush();
                if !send(feed, Feed::End(serial), serial, session, core) {
                    return;
                }
            }
        }
    }
}

struct AudioState {
    decoder: codec::decoder::Audio,
    time_base: Rational,
    generation: u64,
    resampler: Option<Resampler>,
    serial: Option<u64>,
    target: f64,
    reported: bool,
    next_pts: f64,
    format: OutputFormat,
    /// The decoder's profile last published to the session.
    profile: Option<i32>,
}

impl AudioState {
    /// Takes over another track's decoder mid-serial: its output starts at
    /// the switch point, and preroll (if it is over) stays over.
    fn switch(&mut self, switch: AudioSwitch) {
        self.decoder = switch.decoder;
        self.profile = None;
        self.time_base = switch.time_base;
        self.generation = switch.generation;
        // Samples buffered in the resampler are the old track's.
        self.resampler = None;
        if let Some(start) = switch.start {
            self.target = start;
            self.next_pts = start;
        }
    }

    /// Tells the session the profile the decoder has read off the
    /// bitstream, once it is known and whenever it changes.
    fn publish_profile(&mut self, session: &SessionShared) {
        // SAFETY: the decoder's live codec context, read only.
        let (codec, profile) = unsafe {
            let context = &*self.decoder.as_ptr();
            (context.codec_id, context.profile)
        };
        if self.profile == Some(profile) {
            return;
        }
        self.profile = Some(profile);
        *session.audio_profile.lock() = crate::track::profile_name(codec, profile);
    }

    fn enter(&mut self, serial: u64, core: &Core) {
        if self.serial == Some(serial) {
            return;
        }
        self.decoder.flush();
        // Samples buffered inside the resampler belong to the old position.
        self.resampler = None;
        self.serial = Some(serial);
        self.target = target_of(core, serial).unwrap_or(0.0);
        self.next_pts = self.target;
        self.reported = false;
    }

    fn drain(&mut self, feed: &Sender<Feed>, session: &SessionShared, core: &Core) -> bool {
        let Some(serial) = self.serial else {
            return true;
        };
        let mut sound = frame::Audio::empty();
        while self.decoder.receive_frame(&mut sound).is_ok() {
            self.publish_profile(session);
            let pts = sound
                .timestamp()
                .map_or(self.next_pts, |ts| seconds(ts, self.time_base));
            let Some(mut samples) = self.resample(&sound) else {
                continue;
            };
            let channels = usize::from(self.format.channels);
            let rate = f64::from(self.format.rate);
            let duration = (samples.len() / channels) as f64 / rate;
            self.next_pts = pts + duration;
            let mut start = pts;
            if pts + duration <= self.target {
                continue;
            }
            if pts < self.target {
                // The seek target falls inside this frame: play from it.
                let skip = ((self.target - pts) * rate) as usize * channels;
                samples.drain(..skip.min(samples.len()));
                start = self.target;
            }
            if samples.is_empty() {
                continue;
            }
            let chunk = Chunk {
                serial,
                generation: self.generation,
                pts: start,
                samples,
            };
            if !send(feed, Feed::Chunk(chunk), serial, session, core) {
                return false;
            }
            if !self.reported {
                self.reported = true;
                core.ready(Stage::Audio, serial);
            }
        }
        true
    }

    /// `sound`, converted to the output's format.
    fn resample(&mut self, sound: &frame::Audio) -> Option<Vec<f32>> {
        let input = InputFormat::of(sound);
        if self.resampler.as_ref().is_none_or(|r| r.input != input) {
            match Resampler::new(input, self.format) {
                Ok(resampler) => self.resampler = Some(resampler),
                Err(error) => {
                    log::warn!("cannot resample {input:?} audio: {error}");
                    self.resampler = None;
                    return None;
                }
            }
        }
        let resampler = self.resampler.as_mut()?;
        match resampler.convert(sound) {
            Ok(samples) => Some(samples),
            Err(error) => {
                log::debug!("resampling failed: {error}");
                None
            }
        }
    }
}

/// Sends `item` to the output, waiting while the channel is full. Gives up
/// quietly when a seek makes it stale; false when the session is stopping.
fn send(
    feed: &Sender<Feed>,
    mut item: Feed,
    serial: u64,
    session: &SessionShared,
    core: &Core,
) -> bool {
    loop {
        if session.stopping() {
            return false;
        }
        if serial != core.serial() {
            return true;
        }
        match feed.send_timeout(item, POLL) {
            Ok(()) => return true,
            Err(SendTimeoutError::Timeout(back)) => item = back,
            Err(SendTimeoutError::Disconnected(_)) => return false,
        }
    }
}

/// What a decoded audio frame looks like, which decides the resampler.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct InputFormat {
    format: Sample,
    channels: u16,
    layout_mask: u64,
    rate: u32,
}

impl InputFormat {
    fn of(frame: &frame::Audio) -> Self {
        let layout = ffi::AVChannelLayout::from(frame.channel_layout());
        // Only a native-order layout is a channel mask; anything else (none,
        // custom, ambisonic) is played as the default layout for its count.
        let layout_mask = if layout.order == ffi::AVChannelOrder::AV_CHANNEL_ORDER_NATIVE {
            // SAFETY: `mask` is the active union member for native order.
            unsafe { layout.u.mask }
        } else {
            0
        };
        Self {
            format: frame.format(),
            channels: frame.channels(),
            layout_mask,
            rate: frame.rate(),
        }
    }

    fn layout(self) -> ChannelLayout {
        if self.layout_mask == 0 {
            return ChannelLayout::default(i32::from(self.channels));
        }
        // SAFETY: a zeroed AVChannelLayout is the documented starting state
        // for av_channel_layout_from_mask, which fills every field.
        unsafe {
            let mut layout: ffi::AVChannelLayout = std::mem::zeroed();
            if ffi::av_channel_layout_from_mask(&raw mut layout, self.layout_mask) < 0 {
                return ChannelLayout::default(i32::from(self.channels));
            }
            ChannelLayout::from(layout)
        }
    }
}

struct Resampler {
    input: InputFormat,
    context: resampling::Context,
    output: OutputFormat,
}

impl Resampler {
    fn new(input: InputFormat, output: OutputFormat) -> Result<Self, ffmpeg_next::Error> {
        let context = resampling::Context::get(
            input.format,
            input.layout(),
            input.rate,
            Sample::F32(SampleLayout::Packed),
            output.layout(),
            output.rate,
        )?;
        Ok(Self {
            input,
            context,
            output,
        })
    }

    fn convert(&mut self, sound: &frame::Audio) -> Result<Vec<f32>, ffmpeg_next::Error> {
        // Room for everything this frame can produce, rate change included:
        // an output sized to the input would leave the excess inside the
        // resampler and shift every later timestamp by it.
        let capacity =
            sound.samples() * self.output.rate as usize / self.input.rate.max(1) as usize + 256;
        let mut converted = frame::Audio::new(
            Sample::F32(SampleLayout::Packed),
            capacity,
            self.output.layout(),
        );
        self.context.run(sound, &mut converted)?;
        let values = converted.samples() * usize::from(self.output.channels);
        let bytes = &converted.data(0)[..values * size_of::<f32>()];
        Ok(bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|b| f32::from_ne_bytes(*b))
            .collect())
    }
}
