//! Packets between the demuxer and a decoder.

use std::collections::VecDeque;
use std::time::Duration;

use ffmpeg_next::Packet;
use parking_lot::{Condvar, Mutex};

/// What a decoder gets when it asks for the next packet.
pub(crate) enum Pop {
    /// A packet, and the serial it was read under.
    Packet(Packet, u64),
    /// Everything read under this serial has been handed out and the
    /// demuxer has reached the end of the file.
    Eof(u64),
    /// The queue is shutting down; the decoder should return.
    Closed,
}

impl std::fmt::Debug for Pop {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Pop::Packet(packet, serial) => {
                write!(f, "Packet({} bytes, serial {serial})", packet.size())
            }
            Pop::Eof(serial) => write!(f, "Eof(serial {serial})"),
            Pop::Closed => f.write_str("Closed"),
        }
    }
}

/// A FIFO of packets for one decoder, tagged with the serial they were read
/// under so a seek can invalidate them without draining anything.
#[derive(Debug, Default)]
pub(crate) struct PacketQueue {
    inner: Mutex<Inner>,
    changed: Condvar,
}

#[derive(Debug, Default)]
struct Inner {
    packets: VecDeque<Queued>,
    bytes: usize,
    serial: u64,
    eof: bool,
    closed: bool,
}

struct Queued {
    packet: Packet,
    serial: u64,
    /// Presentation time in seconds, when the packet has one.
    time: Option<f64>,
}

impl std::fmt::Debug for Queued {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Queued")
            .field("bytes", &self.packet.size())
            .field("serial", &self.serial)
            .field("time", &self.time)
            .finish()
    }
}

impl PacketQueue {
    /// Appends a packet read under `serial`. A packet from before the last
    /// flush is dropped: its seek has been superseded.
    pub(crate) fn push(&self, packet: Packet, serial: u64, time: Option<f64>) {
        let mut inner = self.inner.lock();
        if inner.closed || serial != inner.serial {
            return;
        }
        inner.bytes += packet.size();
        inner.packets.push_back(Queued {
            packet,
            serial,
            time,
        });
        inner.eof = false;
        drop(inner);
        self.changed.notify_all();
    }

    /// The next packet, waiting up to `timeout` for one. `None` means the
    /// wait ran out, which lets the caller look at its other flags.
    pub(crate) fn pop(&self, timeout: Duration) -> Option<Pop> {
        let mut inner = self.inner.lock();
        loop {
            if inner.closed {
                return Some(Pop::Closed);
            }
            if let Some(queued) = inner.packets.pop_front() {
                inner.bytes -= queued.packet.size();
                drop(inner);
                self.changed.notify_all();
                return Some(Pop::Packet(queued.packet, queued.serial));
            }
            if inner.eof {
                // Handed out once per end of file; a seek clears it.
                inner.eof = false;
                return Some(Pop::Eof(inner.serial));
            }
            if self.changed.wait_for(&mut inner, timeout).timed_out() {
                return None;
            }
        }
    }

    /// Drops every queued packet and starts accepting `serial`.
    pub(crate) fn flush(&self, serial: u64) {
        let mut inner = self.inner.lock();
        inner.packets.clear();
        inner.bytes = 0;
        inner.serial = serial;
        inner.eof = false;
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

    /// Bytes queued.
    pub(crate) fn bytes(&self) -> usize {
        self.inner.lock().bytes
    }

    /// Seconds of media queued, first packet to last.
    pub(crate) fn span(&self) -> f64 {
        let inner = self.inner.lock();
        let first = inner.packets.iter().find_map(|q| q.time);
        let last = inner.packets.iter().rev().find_map(|q| q.time);
        match (first, last) {
            (Some(first), Some(last)) if last > first => last - first,
            _ => 0.0,
        }
    }

    /// The presentation time of the newest queued packet.
    pub(crate) fn newest(&self) -> Option<f64> {
        self.inner.lock().packets.iter().rev().find_map(|q| q.time)
    }

    /// Nothing queued and no end of file waiting to be reported.
    pub(crate) fn is_empty(&self) -> bool {
        let inner = self.inner.lock();
        inner.packets.is_empty() && !inner.eof
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn packet(size: usize) -> Packet {
        Packet::new(size)
    }

    #[test]
    fn packets_come_out_in_order_with_their_serial() {
        let queue = PacketQueue::default();
        queue.flush(3);
        queue.push(packet(10), 3, Some(1.0));
        queue.push(packet(20), 3, Some(2.0));
        assert_eq!(queue.bytes(), 30);
        assert!((queue.span() - 1.0).abs() < f64::EPSILON);
        match queue.pop(Duration::ZERO) {
            Some(Pop::Packet(p, 3)) => assert_eq!(p.size(), 10),
            other => panic!("unexpected {other:?}"),
        }
        assert_eq!(queue.bytes(), 20);
    }

    #[test]
    fn a_flush_drops_the_old_serial_and_its_stragglers() {
        let queue = PacketQueue::default();
        queue.push(packet(10), 0, None);
        queue.flush(1);
        queue.push(packet(10), 0, None); // read before the seek landed
        assert!(queue.pop(Duration::ZERO).is_none());
        assert!(queue.is_empty());
    }

    #[test]
    fn end_of_file_is_reported_once_after_the_last_packet() {
        let queue = PacketQueue::default();
        queue.push(packet(1), 0, None);
        queue.set_eof(0);
        assert!(matches!(queue.pop(Duration::ZERO), Some(Pop::Packet(_, 0))));
        assert!(matches!(queue.pop(Duration::ZERO), Some(Pop::Eof(0))));
        assert!(queue.pop(Duration::ZERO).is_none());
    }

    #[test]
    fn closing_wakes_a_waiting_decoder() {
        let queue = std::sync::Arc::new(PacketQueue::default());
        let waiter = {
            let queue = queue.clone();
            std::thread::spawn(move || queue.pop(Duration::from_secs(10)))
        };
        std::thread::sleep(Duration::from_millis(20));
        queue.close();
        assert!(matches!(waiter.join().unwrap(), Some(Pop::Closed)));
    }
}
