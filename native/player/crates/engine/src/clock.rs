//! The playback clock every stage times itself against.

use std::time::Instant;

use parking_lot::Mutex;

/// Media time, in seconds, as of now.
///
/// It is stored as an anchor -- "media time `pts` at instant `at`" -- and read
/// by extrapolating from it while running. The audio output re-anchors it on
/// every callback with the time the written samples will actually be heard,
/// so audio drives it; without audio the anchor is set once and the wall
/// clock does the rest. Stopping it (pause, underrun, a seek in progress)
/// freezes it at its current value, which is what every consumer wants: video
/// waits and the position holds still.
#[derive(Debug)]
pub struct Clock {
    state: Mutex<State>,
}

#[derive(Clone, Copy, Debug)]
struct State {
    pts: f64,
    at: Instant,
    running: bool,
}

impl State {
    fn read(&self, instant: Instant) -> f64 {
        if !self.running {
            return self.pts;
        }
        // An anchor in the future (audio not yet audible) reads backwards
        // from it, which is exactly right: that audio has not started.
        if instant >= self.at {
            self.pts + instant.duration_since(self.at).as_secs_f64()
        } else {
            self.pts - self.at.duration_since(instant).as_secs_f64()
        }
    }
}

impl Clock {
    #[must_use]
    pub fn new(pts: f64) -> Self {
        Self {
            state: Mutex::new(State {
                pts,
                at: Instant::now(),
                running: false,
            }),
        }
    }

    /// The media time now.
    #[must_use]
    pub fn now(&self) -> f64 {
        self.at(Instant::now())
    }

    /// The media time at `instant`.
    #[must_use]
    pub fn at(&self, instant: Instant) -> f64 {
        self.state.lock().read(instant)
    }

    /// Whether the clock is advancing.
    #[must_use]
    pub fn is_running(&self) -> bool {
        self.state.lock().running
    }

    /// Media time `pts` is heard or shown at `at`, and time moves on from there.
    pub fn anchor(&self, pts: f64, at: Instant) {
        *self.state.lock() = State {
            pts,
            at,
            running: true,
        };
    }

    /// Hold at the current media time.
    pub fn stop(&self) {
        let now = Instant::now();
        let mut state = self.state.lock();
        if state.running {
            *state = State {
                pts: state.read(now),
                at: now,
                running: false,
            };
        }
    }

    /// Hold at `pts`: a seek, a new file.
    pub fn reset(&self, pts: f64) {
        *self.state.lock() = State {
            pts,
            at: Instant::now(),
            running: false,
        };
    }

    /// Continue from where the clock stands, on the wall clock.
    pub fn start(&self) {
        let now = Instant::now();
        let mut state = self.state.lock();
        if !state.running {
            state.at = now;
            state.running = true;
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[test]
    fn a_stopped_clock_holds_still() {
        let clock = Clock::new(12.5);
        let later = Instant::now() + Duration::from_secs(3);
        assert!((clock.at(later) - 12.5).abs() < f64::EPSILON);
    }

    #[test]
    fn a_running_clock_advances_from_its_anchor() {
        let clock = Clock::new(0.0);
        let at = Instant::now();
        clock.anchor(100.0, at);
        let read = clock.at(at + Duration::from_millis(1500));
        assert!((read - 101.5).abs() < 1e-9);
        // Before the anchor: audio written but not yet heard.
        let read = clock.at(at
            .checked_sub(Duration::from_millis(200))
            .expect("an instant 200ms ago"));
        assert!((read - 99.8).abs() < 1e-9);
    }

    #[test]
    fn stopping_keeps_the_time_reached() {
        let clock = Clock::new(0.0);
        clock.anchor(
            10.0,
            Instant::now()
                .checked_sub(Duration::from_secs(2))
                .expect("an instant 2s ago"),
        );
        clock.stop();
        let held = clock.now();
        assert!((held - 12.0).abs() < 0.05, "held at {held}");
        assert!(!clock.is_running());
        clock.start();
        assert!(clock.is_running());
        assert!(clock.now() >= held);
    }
}
