//! Decoded video frames waiting for their presentation time.

use std::collections::VecDeque;
use std::time::Duration;

use ffmpeg_next::frame;
use parking_lot::{Condvar, Mutex};

/// How many decoded frames may wait. Enough to ride out a slow decode of a
/// keyframe, few enough that 4K frames do not pile up in memory.
pub(crate) const CAPACITY: usize = 6;

/// A decoded frame and when to show it.
pub(crate) struct VideoFrame {
    pub(crate) frame: frame::Video,
    pub(crate) pts: f64,
    pub(crate) serial: u64,
}

impl std::fmt::Debug for VideoFrame {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("VideoFrame")
            .field("pts", &self.pts)
            .field("serial", &self.serial)
            .finish_non_exhaustive()
    }
}

/// The bounded queue between the video decoder and the renderer.
#[derive(Debug, Default)]
pub(crate) struct FrameQueue {
    inner: Mutex<Inner>,
    space: Condvar,
}

#[derive(Debug, Default)]
struct Inner {
    frames: VecDeque<VideoFrame>,
    serial: u64,
    closed: bool,
    /// Frames thrown away because their time passed before anyone showed
    /// them: mpv's frame-drop-count, for the decoder-capacity verdicts.
    dropped: u64,
}

/// What [`FrameQueue::push`] did with a frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Pushed {
    Queued,
    /// A seek made it stale.
    Dropped,
    /// The queue is shutting down.
    Closed,
}

impl FrameQueue {
    /// Queues `frame`, waiting while the queue is full. Returns as soon as
    /// the frame goes stale or the queue closes, so a seek never waits for a
    /// full queue to drain.
    pub(crate) fn push(&self, frame: VideoFrame) -> Pushed {
        let mut inner = self.inner.lock();
        loop {
            if inner.closed {
                return Pushed::Closed;
            }
            if frame.serial != inner.serial {
                return Pushed::Dropped;
            }
            if inner.frames.len() < CAPACITY {
                inner.frames.push_back(frame);
                return Pushed::Queued;
            }
            self.space.wait_for(&mut inner, Duration::from_millis(50));
        }
    }

    /// The frame to show at media time `now`: the newest one due, with every
    /// older one dropped as late. With `first` set (nothing of this serial is
    /// on screen yet), the oldest frame is returned whether due or not -- a
    /// paused seek and the start of playback both need a picture before the
    /// clock moves.
    pub(crate) fn take_due(&self, now: f64, serial: u64, first: bool) -> Option<VideoFrame> {
        let mut inner = self.inner.lock();
        if inner.serial != serial {
            return None;
        }
        let mut due = if first {
            inner.frames.pop_front()
        } else {
            None
        };
        while inner.frames.front().is_some_and(|f| f.pts <= now) {
            if due.is_some() {
                inner.dropped += 1;
            }
            due = inner.frames.pop_front();
        }
        drop(inner);
        if due.is_some() {
            self.space.notify_all();
        }
        due
    }

    /// Drops frames whose time has passed, keeping the newest due one for
    /// the next render. Nothing else consumes frames when nobody renders (a
    /// hidden window, no video item at all), and a full queue would stall
    /// the decoder -- and with it the end of the file.
    pub(crate) fn drop_late(&self, now: f64) {
        let mut inner = self.inner.lock();
        let mut dropped = false;
        while inner.frames.len() >= 2 && inner.frames[1].pts <= now {
            inner.frames.pop_front();
            inner.dropped += 1;
            dropped = true;
        }
        drop(inner);
        if dropped {
            self.space.notify_all();
        }
    }

    /// Frames dropped as late since the file was loaded.
    pub(crate) fn dropped(&self) -> u64 {
        self.inner.lock().dropped
    }

    /// When the last queued frame is due.
    pub(crate) fn last_pts(&self) -> Option<f64> {
        self.inner.lock().frames.back().map(|f| f.pts)
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.inner.lock().frames.is_empty()
    }

    /// Drops every frame and starts accepting `serial`.
    pub(crate) fn flush(&self, serial: u64) {
        let mut inner = self.inner.lock();
        inner.frames.clear();
        inner.serial = serial;
        drop(inner);
        self.space.notify_all();
    }

    pub(crate) fn close(&self) {
        self.inner.lock().closed = true;
        self.space.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(pts: f64, serial: u64) -> VideoFrame {
        VideoFrame {
            frame: frame::Video::empty(),
            pts,
            serial,
        }
    }

    #[test]
    fn frames_passed_over_are_counted_as_dropped() {
        let queue = FrameQueue::default();
        queue.flush(1);
        for pts in [0.0, 0.04, 0.08, 0.12] {
            assert_eq!(queue.push(frame(pts, 1)), Pushed::Queued);
        }
        // The first picture of a serial is never a drop.
        assert!(queue.take_due(0.0, 1, true).is_some());
        assert_eq!(queue.dropped(), 0);
        // Rendering fell behind: 0.04 and 0.08 were never shown.
        assert_eq!(queue.take_due(0.1, 1, false).map(|f| f.pts), Some(0.08));
        assert_eq!(queue.dropped(), 1);
        // Nobody rendering at all: the monitor drops what went past.
        queue.push(frame(0.16, 1));
        queue.drop_late(0.2);
        assert_eq!(queue.dropped(), 2);
    }

    #[test]
    fn the_newest_due_frame_wins_and_late_ones_are_dropped() {
        let queue = FrameQueue::default();
        for pts in [1.0, 1.04, 1.08, 1.12] {
            assert_eq!(queue.push(frame(pts, 0)), Pushed::Queued);
        }
        let shown = queue.take_due(1.09, 0, false).expect("a frame is due");
        assert!((shown.pts - 1.08).abs() < f64::EPSILON);
        let next = queue
            .take_due(2.0, 0, false)
            .expect("the last frame stays queued");
        assert!((next.pts - 1.12).abs() < f64::EPSILON);
    }

    #[test]
    fn the_first_frame_of_a_serial_shows_before_it_is_due() {
        let queue = FrameQueue::default();
        queue.push(frame(30.0, 0));
        assert!(queue.take_due(0.0, 0, false).is_none());
        assert!(queue.take_due(0.0, 0, true).is_some());
    }

    #[test]
    fn late_frames_go_without_a_render_but_the_due_one_stays() {
        let queue = FrameQueue::default();
        for pts in [1.0, 2.0, 3.0, 4.0] {
            queue.push(frame(pts, 0));
        }
        queue.drop_late(3.5);
        assert_eq!(queue.last_pts(), Some(4.0));
        let shown = queue
            .take_due(3.5, 0, false)
            .expect("the due frame was kept");
        assert!((shown.pts - 3.0).abs() < f64::EPSILON);
    }

    #[test]
    fn a_flush_makes_waiting_frames_stale() {
        let queue = FrameQueue::default();
        queue.push(frame(1.0, 0));
        queue.flush(1);
        assert!(queue.is_empty());
        assert_eq!(queue.push(frame(2.0, 0)), Pushed::Dropped);
        assert!(queue.take_due(10.0, 0, true).is_none());
    }
}
