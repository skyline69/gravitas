//! What the engine tells its embedder, and the thread it tells it on.

use std::sync::Arc;
use std::thread;

use crossbeam_channel::{Receiver, Sender, unbounded};

use crate::error::{Error, Result};

/// Something the embedder may want to react to. Delivered in order, on the
/// engine's event thread -- never on a thread that holds engine state, so a
/// sink may call back into the [`crate::Player`] freely.
#[derive(Clone, Debug, PartialEq)]
pub enum Event {
    /// The file opened: its tracks, duration and chapters are known.
    FileLoaded,
    /// The file could not be opened. Carries FFmpeg's reason.
    LoadFailed(String),
    /// The track list or the selection changed.
    TracksChanged,
    /// Paused, loading or the volume changed.
    StateChanged,
    /// The first frame of a load or seek is on screen, `ms` after it began.
    FirstFrame { ms: u64 },
    /// Playback reached the end of what the file delivered. Like mpv's
    /// `eof-reached`, it says nothing about why: a connection that dropped
    /// ends the file the same way the last frame does.
    EndOfFile,
}

/// Receives events. Implemented for any `Fn(Event)` that may be sent across
/// threads.
pub trait EventSink: Send + Sync + 'static {
    fn deliver(&self, event: Event);
}

impl<F: Fn(Event) + Send + Sync + 'static> EventSink for F {
    fn deliver(&self, event: Event) {
        self(event);
    }
}

/// The sending half, cloned into every engine thread. Sending never blocks.
#[derive(Clone, Debug)]
pub(crate) struct Events {
    sender: Sender<Event>,
}

impl Events {
    pub(crate) fn send(&self, event: Event) {
        // The receiver only goes away when the player is dropped, and then
        // nobody is listening anyway.
        let _ = self.sender.send(event);
    }
}

/// Starts the thread that hands events to `sink`. It ends when every
/// [`Events`] handle is gone.
pub(crate) fn spawn(sink: Arc<dyn EventSink>) -> Result<(Events, thread::JoinHandle<()>)> {
    let (sender, receiver) = unbounded();
    let handle = thread::Builder::new()
        .name("player-events".to_owned())
        .spawn(move || deliver_all(&receiver, sink.as_ref()))
        .map_err(|source| Error::Thread {
            what: "the event thread",
            source,
        })?;
    Ok((Events { sender }, handle))
}

fn deliver_all(receiver: &Receiver<Event>, sink: &dyn EventSink) {
    for event in receiver {
        sink.deliver(event);
    }
}
