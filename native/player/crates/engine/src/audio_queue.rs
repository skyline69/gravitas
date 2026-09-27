//! Packets of every audio track, so that switching tracks is a decoder swap
//! rather than a re-read.
//!
//! A demuxer that keeps only the playing track's packets has nothing to give
//! a newly selected one: switching means seeking back to the current position
//! and reading the stream again -- a fresh request, the read-ahead thrown
//! away and a re-buffer (2.1 s measured on a real debrid stream, and mpv pays
//! the same). The file carries every track's packets anyway, so keeping them
//! costs no bandwidth, and compressed audio is small (six AC3 tracks at
//! 448 kbit/s are ~10 MB for the whole 30 s read-ahead).
//!
//! So every audio track has its own run of packets, from a little behind the
//! playhead (`KEEP_BEHIND_S`) to wherever the demuxer has read. The decoder
//! reads the selected track through a cursor instead of taking packets out,
//! which is what leaves the history for a later switch back. A switch moves
//! the cursor to the new track at the playhead and hands the decoder its new
//! codec in the same step -- `AudioPop::Switch` comes out before any packet of
//! the new track, so no packet is ever fed to the wrong decoder.

use std::collections::{HashMap, VecDeque};
use std::time::Duration;

use ffmpeg_next::packet::{Mut as _, Ref as _};
use ffmpeg_next::{Packet, ffi};
use parking_lot::{Condvar, Mutex};

use crate::decode::AudioSwitch;

/// How much of every track is kept behind the playhead. The decoder runs up
/// to a second or so ahead of what is heard, and a switch has to find the new
/// track's packets from the playhead on.
pub(crate) const KEEP_BEHIND_S: f64 = 2.0;

/// How far before the switch point the new track's decoding starts, so a
/// codec that needs a frame to settle (AAC's priming) has it; the decoder
/// trims what comes before.
const DECODE_LEAD_S: f64 = 0.2;

/// What the audio decoder gets when it asks for more.
pub(crate) enum AudioPop {
    /// Decode with this from now on: the packets after it are the new
    /// track's.
    Switch(AudioSwitch),
    /// A packet of the selected track, and the serial it was read under.
    Packet(Packet, u64),
    /// The selected track has been handed out to its end, and the demuxer
    /// has reached the end of the file.
    Eof(u64),
    /// Shutting down; the decoder should return.
    Closed,
}

impl std::fmt::Debug for AudioPop {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Switch(_) => f.write_str("Switch"),
            Self::Packet(packet, serial) => {
                write!(f, "Packet({} bytes, serial {serial})", packet.size())
            }
            Self::Eof(serial) => write!(f, "Eof(serial {serial})"),
            Self::Closed => f.write_str("Closed"),
        }
    }
}

struct Queued {
    packet: Packet,
    /// Presentation time in seconds, when the packet has one.
    time: Option<f64>,
}

/// Every audio track's packets and the selected track's read position.
#[derive(Default)]
pub(crate) struct AudioPackets {
    inner: Mutex<Inner>,
    changed: Condvar,
}

impl std::fmt::Debug for AudioPackets {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let inner = self.inner.lock();
        f.debug_struct("AudioPackets")
            .field("tracks", &inner.tracks.len())
            .field("selected", &inner.selected)
            .field("bytes", &inner.bytes)
            .finish_non_exhaustive()
    }
}

#[derive(Default)]
struct Inner {
    /// By stream index.
    tracks: HashMap<usize, VecDeque<Queued>>,
    selected: Option<usize>,
    /// The next packet of the selected track to hand out.
    cursor: usize,
    pending: Option<AudioSwitch>,
    bytes: usize,
    serial: u64,
    eof: bool,
    /// The end of file has been reported for this serial.
    eof_reported: bool,
    closed: bool,
}

impl Inner {
    fn selected_track(&self) -> Option<&VecDeque<Queued>> {
        self.selected.and_then(|s| self.tracks.get(&s))
    }
}

impl AudioPackets {
    /// Keeps `packet`, read under `serial`, for the track at `stream`. A
    /// packet from before the last flush is dropped.
    pub(crate) fn push(&self, stream: usize, packet: Packet, serial: u64, time: Option<f64>) {
        let mut inner = self.inner.lock();
        if inner.closed || serial != inner.serial {
            return;
        }
        inner.bytes += packet.size();
        inner
            .tracks
            .entry(stream)
            .or_default()
            .push_back(Queued { packet, time });
        inner.eof = false;
        inner.eof_reported = false;
        drop(inner);
        self.changed.notify_all();
    }

    /// The next thing for the decoder, waiting up to `timeout`. `None` means
    /// the wait ran out.
    pub(crate) fn pop(&self, timeout: Duration) -> Option<AudioPop> {
        let mut inner = self.inner.lock();
        loop {
            if inner.closed {
                return Some(AudioPop::Closed);
            }
            if let Some(switch) = inner.pending.take() {
                return Some(AudioPop::Switch(switch));
            }
            let cursor = inner.cursor;
            if let Some(queued) = inner.selected_track().and_then(|t| t.get(cursor)) {
                let packet = share(&queued.packet);
                inner.cursor += 1;
                return Some(AudioPop::Packet(packet, inner.serial));
            }
            if inner.eof && !inner.eof_reported && inner.selected.is_some() {
                inner.eof_reported = true;
                return Some(AudioPop::Eof(inner.serial));
            }
            if self.changed.wait_for(&mut inner, timeout).timed_out() {
                return None;
            }
        }
    }

    /// Plays the track at `stream` from media time `from` on, decoded by
    /// `switch` (None: the decoder it has already fits, as on a first
    /// selection). `stream` None stops handing out audio.
    pub(crate) fn select(&self, stream: Option<usize>, from: f64, switch: Option<AudioSwitch>) {
        let mut inner = self.inner.lock();
        inner.selected = stream;
        inner.pending = switch;
        let start = from - DECODE_LEAD_S;
        // The last packet at or before the lead point, so decoding starts no
        // later than it; its samples before `from` are trimmed downstream.
        inner.cursor = inner.selected_track().map_or(0, |track| {
            track
                .iter()
                .rposition(|q| q.time.is_some_and(|t| t <= start))
                .unwrap_or(0)
        });
        inner.eof_reported = false;
        drop(inner);
        self.changed.notify_all();
    }

    /// Drops every track's packets from before `time`, except packets of
    /// the selected track the decoder has not had yet.
    pub(crate) fn forget_before(&self, time: f64) {
        let mut inner = self.inner.lock();
        let Inner {
            tracks,
            selected,
            cursor,
            bytes,
            ..
        } = &mut *inner;
        for (stream, track) in tracks.iter_mut() {
            let is_selected = *selected == Some(*stream);
            while let Some(front) = track.front() {
                let old = front.time.is_some_and(|t| t < time);
                let unread = is_selected && *cursor == 0;
                if !old || unread {
                    break;
                }
                *bytes -= front.packet.size();
                track.pop_front();
                if is_selected {
                    *cursor -= 1;
                }
            }
        }
    }

    /// Drops everything and starts accepting `serial` (a seek). The
    /// selection stays.
    pub(crate) fn flush(&self, serial: u64) {
        let mut inner = self.inner.lock();
        inner.tracks.clear();
        inner.cursor = 0;
        inner.bytes = 0;
        inner.serial = serial;
        inner.eof = false;
        inner.eof_reported = false;
        drop(inner);
        self.changed.notify_all();
    }

    /// The demuxer has read everything under `serial`.
    pub(crate) fn set_eof(&self, serial: u64) {
        let mut inner = self.inner.lock();
        if inner.serial == serial {
            inner.eof = true;
        }
        drop(inner);
        self.changed.notify_all();
    }

    /// Wakes every waiter for good.
    pub(crate) fn close(&self) {
        self.inner.lock().closed = true;
        self.changed.notify_all();
    }

    /// Bytes kept, every track together.
    pub(crate) fn bytes(&self) -> usize {
        self.inner.lock().bytes
    }

    /// Seconds of the selected track ahead of the decoder.
    pub(crate) fn span(&self) -> f64 {
        let inner = self.inner.lock();
        let Some(track) = inner.selected_track() else {
            return 0.0;
        };
        let first = track.iter().skip(inner.cursor).find_map(|q| q.time);
        let last = track.iter().rev().find_map(|q| q.time);
        match (first, last) {
            (Some(first), Some(last)) if last > first => last - first,
            _ => 0.0,
        }
    }

    /// The presentation time of the selected track's newest packet.
    pub(crate) fn newest(&self) -> Option<f64> {
        self.inner
            .lock()
            .selected_track()
            .and_then(|t| t.iter().rev().find_map(|q| q.time))
    }
}

/// A second reference to `packet`'s data: the decoder reads the packet
/// while the history keeps it.
fn share(packet: &Packet) -> Packet {
    let mut shared = Packet::empty();
    // SAFETY: both packets are valid; av_packet_ref takes a new reference to
    // a refcounted buffer, or copies one that is not.
    let error = unsafe { ffi::av_packet_ref(shared.as_mut_ptr(), packet.as_ptr()) };
    if error < 0 {
        // Out of memory: an empty packet decodes to nothing.
        return Packet::empty();
    }
    shared
}

#[cfg(test)]
mod tests {
    use super::*;

    fn packet(size: usize) -> Packet {
        Packet::new(size)
    }

    fn packet_size(pop: Option<AudioPop>) -> usize {
        match pop {
            Some(AudioPop::Packet(p, _)) => p.size(),
            other => panic!("expected a packet, got {other:?}"),
        }
    }

    /// Two tracks interleaved, a packet per 0.1 s each; track 1's packets are
    /// 10 bytes, track 2's 20.
    fn interleaved(queue: &AudioPackets, seconds: u32) {
        for tenth in 0..seconds * 10 {
            let time = f64::from(tenth) / 10.0;
            queue.push(1, packet(10), 0, Some(time));
            queue.push(2, packet(20), 0, Some(time));
        }
    }

    #[test]
    fn only_the_selected_track_is_handed_out() {
        let queue = AudioPackets::default();
        queue.select(Some(1), 0.0, None);
        interleaved(&queue, 1);
        for _ in 0..10 {
            assert_eq!(packet_size(queue.pop(Duration::ZERO)), 10);
        }
        assert!(queue.pop(Duration::ZERO).is_none());
        assert_eq!(queue.bytes(), 300, "every track is kept");
    }

    #[test]
    fn a_switch_comes_out_before_the_new_track_and_starts_at_the_playhead() {
        let queue = AudioPackets::default();
        queue.select(Some(1), 0.0, None);
        interleaved(&queue, 3);
        for _ in 0..25 {
            queue.pop(Duration::ZERO);
        }
        queue.select(Some(2), 1.05, None);
        // From the lead point (0.85 s): packets 0.8, 0.9, ... of track 2.
        assert_eq!(queue.inner.lock().cursor, 8);
        assert_eq!(packet_size(queue.pop(Duration::ZERO)), 20);
    }

    #[test]
    fn switching_back_finds_the_history_the_decoder_already_read() {
        let queue = AudioPackets::default();
        queue.select(Some(1), 0.0, None);
        interleaved(&queue, 3);
        for _ in 0..25 {
            queue.pop(Duration::ZERO);
        }
        queue.select(Some(2), 1.05, None);
        queue.select(Some(1), 1.55, None);
        assert_eq!(queue.inner.lock().cursor, 13);
        assert_eq!(packet_size(queue.pop(Duration::ZERO)), 10);
    }

    #[test]
    fn history_is_forgotten_but_unread_packets_are_not() {
        let queue = AudioPackets::default();
        queue.select(Some(1), 0.0, None);
        interleaved(&queue, 2);
        for _ in 0..5 {
            queue.pop(Duration::ZERO);
        }
        // The decoder is at 0.5 s: track 1 keeps 0.5 on, track 2 1.0 on.
        queue.forget_before(1.0);
        let inner = queue.inner.lock();
        assert_eq!(inner.tracks[&1].len(), 15);
        assert_eq!(inner.tracks[&2].len(), 10);
        assert_eq!(inner.cursor, 0);
        assert_eq!(inner.bytes, 15 * 10 + 10 * 20);
    }

    #[test]
    fn the_pending_decoder_goes_before_any_packet() {
        let queue = AudioPackets::default();
        queue.select(Some(1), 0.0, None);
        interleaved(&queue, 1);
        let codec = ffmpeg_next::codec::decoder::find(ffmpeg_next::codec::Id::MP2)
            .expect("FFmpeg has an MP2 decoder");
        let decoder = ffmpeg_next::codec::Context::new_with_codec(codec)
            .decoder()
            .audio()
            .expect("an audio decoder");
        queue.select(
            Some(2),
            0.0,
            Some(AudioSwitch {
                decoder,
                time_base: ffmpeg_next::Rational::new(1, 1000),
                start: Some(0.0),
                generation: 1,
            }),
        );
        assert!(matches!(
            queue.pop(Duration::ZERO),
            Some(AudioPop::Switch(_))
        ));
        assert_eq!(packet_size(queue.pop(Duration::ZERO)), 20);
    }

    #[test]
    fn end_of_file_comes_after_the_selected_track_s_last_packet() {
        let queue = AudioPackets::default();
        queue.select(Some(1), 0.0, None);
        queue.push(1, packet(1), 0, Some(0.0));
        queue.push(2, packet(1), 0, Some(0.0));
        queue.set_eof(0);
        assert!(matches!(
            queue.pop(Duration::ZERO),
            Some(AudioPop::Packet(_, 0))
        ));
        assert!(matches!(queue.pop(Duration::ZERO), Some(AudioPop::Eof(0))));
        assert!(queue.pop(Duration::ZERO).is_none());
    }

    #[test]
    fn a_flush_drops_every_track_and_stragglers() {
        let queue = AudioPackets::default();
        queue.select(Some(1), 0.0, None);
        interleaved(&queue, 1);
        queue.flush(1);
        queue.push(1, packet(10), 0, Some(0.0)); // read before the seek landed
        assert_eq!(queue.bytes(), 0);
        assert!(queue.pop(Duration::ZERO).is_none());
    }
}
