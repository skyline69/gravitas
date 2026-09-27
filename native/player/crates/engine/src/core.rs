//! State shared by every thread of a player, for the player's whole life.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::time::{Duration, Instant};

use parking_lot::Mutex;

use crate::audio::OutputTap;
use crate::clock::Clock;
use crate::hwdec::HwDecoders;

/// How long preroll waits for the slower of audio and video once the other
/// is ready. A file whose audio starts late, or whose video track is only a
/// few stray frames, plays after this rather than never.
pub(crate) const PREROLL_TIMEOUT: Duration = Duration::from_secs(2);

/// Timing and control state every stage reads.
#[derive(Debug)]
pub(crate) struct Core {
    /// Bumped by every load and seek. Work produced under an older serial is
    /// dropped wherever it is found.
    serial: AtomicU64,
    paused: AtomicBool,
    /// Volume as mpv counts it (0-100, more is amplification), as `f32` bits.
    volume: AtomicU32,
    muted: AtomicBool,
    /// The output ran dry while it should have been playing.
    starved: AtomicBool,
    /// Bumped by every audio track switch: audio decoded for an earlier
    /// selection is dropped wherever it is found, like work of an older
    /// serial, but without a seek.
    audio_generation: AtomicU64,
    /// What the output played lately, when asked for.
    pub(crate) tap: OutputTap,
    pub(crate) clock: Clock,
    pub(crate) timeline: Mutex<Timeline>,
    /// Hardware decoding devices, kept for the player's life: creating one
    /// costs far more than a file's decoder.
    pub(crate) hwdec: HwDecoders,
}

impl Core {
    /// Moves where `serial` starts, before anything of it has been decoded --
    /// a resume that starts at the keyframe before the asked-for point.
    pub(crate) fn retarget(&self, serial: u64, target: f64) {
        let mut timeline = self.timeline.lock();
        if timeline.serial != serial {
            return;
        }
        timeline.target = target;
        drop(timeline);
        self.clock.reset(target);
    }

    pub(crate) fn new() -> Self {
        Self {
            serial: AtomicU64::new(0),
            paused: AtomicBool::new(false),
            volume: AtomicU32::new(100f32.to_bits()),
            muted: AtomicBool::new(false),
            starved: AtomicBool::new(false),
            audio_generation: AtomicU64::new(0),
            tap: OutputTap::default(),
            clock: Clock::new(0.0),
            timeline: Mutex::new(Timeline::idle()),
            hwdec: HwDecoders::default(),
        }
    }

    pub(crate) fn serial(&self) -> u64 {
        self.serial.load(Ordering::Acquire)
    }

    /// Starts a new serial: a load or a seek.
    pub(crate) fn next_serial(&self) -> u64 {
        self.serial.fetch_add(1, Ordering::AcqRel) + 1
    }

    pub(crate) fn audio_generation(&self) -> u64 {
        self.audio_generation.load(Ordering::Acquire)
    }

    /// Starts a new audio track selection.
    pub(crate) fn next_audio_generation(&self) -> u64 {
        self.audio_generation.fetch_add(1, Ordering::AcqRel) + 1
    }

    pub(crate) fn is_paused(&self) -> bool {
        self.paused.load(Ordering::Acquire)
    }

    pub(crate) fn set_paused(&self, paused: bool) {
        self.paused.store(paused, Ordering::Release);
        if paused {
            self.clock.stop();
        } else if self.timeline.lock().runs_on_wall_clock(self.serial()) {
            self.clock.start();
        }
        // With audio playing, the output re-anchors the clock on its next
        // callback; starting it here would run it ahead of the sound.
    }

    pub(crate) fn volume(&self) -> f32 {
        f32::from_bits(self.volume.load(Ordering::Relaxed))
    }

    pub(crate) fn set_volume(&self, volume: f32) {
        self.volume
            .store(volume.max(0.0).to_bits(), Ordering::Relaxed);
    }

    pub(crate) fn is_muted(&self) -> bool {
        self.muted.load(Ordering::Relaxed)
    }

    pub(crate) fn set_muted(&self, muted: bool) {
        self.muted.store(muted, Ordering::Relaxed);
    }

    /// The factor samples are multiplied by: mpv's cubic curve, so the same
    /// slider position is equally loud whichever engine plays.
    pub(crate) fn gain(&self) -> f32 {
        if self.is_muted() {
            return 0.0;
        }
        (self.volume() / 100.0).powi(3)
    }

    pub(crate) fn is_starved(&self) -> bool {
        self.starved.load(Ordering::Relaxed)
    }

    pub(crate) fn set_starved(&self, starved: bool) {
        self.starved.store(starved, Ordering::Relaxed);
    }

    /// A stage has its first output for `serial`. Ends preroll once both are
    /// there, and starts the wall clock when nothing else will.
    pub(crate) fn ready(&self, stage: Stage, serial: u64) {
        let mut timeline = self.timeline.lock();
        timeline.mark_ready(stage, serial);
        self.maybe_start(&mut timeline, serial);
    }

    /// Ends a preroll that has waited long enough for its slower half.
    pub(crate) fn check_preroll(&self) {
        let serial = self.serial();
        let mut timeline = self.timeline.lock();
        self.maybe_start(&mut timeline, serial);
    }

    fn maybe_start(&self, timeline: &mut Timeline, serial: u64) {
        if timeline.try_start(serial, Instant::now()) && !timeline.has_audio && !self.is_paused() {
            self.clock.start();
        }
    }
}

/// Which pipeline a readiness report comes from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Stage {
    Audio,
    Video,
}

/// What the audio output should do right now.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum AudioState {
    /// Output silence and leave the clock alone: no audio, preroll, or a
    /// stale serial.
    Idle,
    /// Play what the decoder sends.
    Playing,
    /// Every sample of this serial has been played.
    Finished,
}

/// Where the current serial stands: preroll, and which halves have ended.
#[derive(Debug)]
#[allow(
    clippy::struct_excessive_bools,
    reason = "independent facts about one serial, not a state machine in disguise"
)]
pub(crate) struct Timeline {
    pub(crate) serial: u64,
    /// The media time this serial starts at.
    pub(crate) target: f64,
    pub(crate) has_audio: bool,
    pub(crate) has_video: bool,
    audio_ready: bool,
    video_ready: bool,
    audio_done: bool,
    video_done: bool,
    /// Preroll is over.
    started: bool,
    /// When this serial began, and when the first of its halves was ready.
    pub(crate) since: Instant,
    first_ready: Option<Instant>,
    pub(crate) first_frame_reported: bool,
    pub(crate) end_reported: bool,
}

impl Timeline {
    fn idle() -> Self {
        Self {
            serial: 0,
            target: 0.0,
            has_audio: false,
            has_video: false,
            audio_ready: false,
            video_ready: false,
            audio_done: false,
            video_done: false,
            started: false,
            since: Instant::now(),
            first_ready: None,
            first_frame_reported: false,
            end_reported: false,
        }
    }

    /// A load or seek: `serial` starts at `target`, and preroll begins.
    pub(crate) fn begin(&mut self, serial: u64, target: f64, has_audio: bool, has_video: bool) {
        *self = Self {
            serial,
            target,
            has_audio,
            has_video,
            ..Self::idle()
        };
    }

    fn mark_ready(&mut self, stage: Stage, serial: u64) {
        if serial != self.serial {
            return;
        }
        log::debug!(
            "{stage:?} ready {} ms into serial {serial}",
            self.since.elapsed().as_millis()
        );
        match stage {
            Stage::Audio => self.audio_ready = true,
            Stage::Video => self.video_ready = true,
        }
        self.first_ready.get_or_insert_with(Instant::now);
    }

    /// Ends preroll when it can. True only on the call that ends it.
    fn try_start(&mut self, serial: u64, now: Instant) -> bool {
        if self.started || serial != self.serial {
            return false;
        }
        let audio = self.audio_ready || self.audio_done || !self.has_audio;
        let video = self.video_ready || self.video_done || !self.has_video;
        let waited = self
            .first_ready
            .is_some_and(|first| now.duration_since(first) >= PREROLL_TIMEOUT);
        if (audio && video) || waited {
            self.started = true;
            return true;
        }
        false
    }

    pub(crate) fn is_prerolling(&self) -> bool {
        !self.started
    }

    pub(crate) fn audio_state(&self, serial: u64) -> AudioState {
        // Once the end is reported the clock stays where it stopped; an output
        // still running on "audio finished" would start it again.
        if serial != self.serial || !self.has_audio || !self.started || self.end_reported {
            AudioState::Idle
        } else if self.audio_done {
            AudioState::Finished
        } else {
            AudioState::Playing
        }
    }

    pub(crate) fn audio_ended(&mut self, serial: u64) {
        if serial == self.serial {
            self.audio_done = true;
        }
    }

    pub(crate) fn video_ended(&mut self, serial: u64) {
        if serial == self.serial {
            self.video_done = true;
        }
    }

    /// Every half this serial has has delivered all it will.
    pub(crate) fn all_ended(&self) -> bool {
        (self.audio_done || !self.has_audio) && (self.video_done || !self.has_video)
    }

    /// Nothing but the wall clock will move time forward.
    fn runs_on_wall_clock(&self, serial: u64) -> bool {
        serial == self.serial && self.started && (!self.has_audio || self.audio_done)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preroll_waits_for_both_halves() {
        let core = Core::new();
        let serial = core.next_serial();
        core.timeline.lock().begin(serial, 0.0, true, true);
        core.ready(Stage::Video, serial);
        assert!(core.timeline.lock().is_prerolling());
        core.ready(Stage::Audio, serial);
        assert!(!core.timeline.lock().is_prerolling());
        // With audio, the output anchors the clock; preroll does not.
        assert!(!core.clock.is_running());
    }

    #[test]
    fn video_alone_starts_the_wall_clock() {
        let core = Core::new();
        let serial = core.next_serial();
        core.timeline.lock().begin(serial, 5.0, false, true);
        core.ready(Stage::Video, serial);
        assert!(core.clock.is_running());
    }

    #[test]
    fn readiness_from_an_old_serial_counts_for_nothing() {
        let core = Core::new();
        let old = core.next_serial();
        let serial = core.next_serial();
        core.timeline.lock().begin(serial, 0.0, false, true);
        core.ready(Stage::Video, old);
        assert!(core.timeline.lock().is_prerolling());
    }

    #[test]
    fn preroll_gives_up_on_a_late_half() {
        let mut timeline = Timeline::idle();
        timeline.begin(1, 0.0, true, true);
        timeline.mark_ready(Stage::Video, 1);
        let now = Instant::now();
        assert!(!timeline.try_start(1, now));
        assert!(timeline.try_start(1, now + PREROLL_TIMEOUT));
    }

    #[test]
    fn the_output_leaves_the_clock_alone_after_the_end() {
        let mut timeline = Timeline::idle();
        timeline.begin(1, 0.0, true, true);
        timeline.mark_ready(Stage::Audio, 1);
        timeline.mark_ready(Stage::Video, 1);
        assert!(timeline.try_start(1, Instant::now()));
        timeline.audio_ended(1);
        assert_eq!(timeline.audio_state(1), AudioState::Finished);
        timeline.end_reported = true;
        assert_eq!(timeline.audio_state(1), AudioState::Idle);
    }

    #[test]
    fn the_volume_curve_is_mpvs() {
        let core = Core::new();
        core.set_volume(50.0);
        assert!((core.gain() - 0.125).abs() < 1e-6);
        core.set_muted(true);
        assert!(core.gain().abs() < f32::EPSILON);
    }
}
