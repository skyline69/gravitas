//! Engine logs into Python's logging, without any engine thread waiting on
//! the GIL.
//!
//! pyo3-log takes the GIL inside `log()`, on whatever thread logs. For the
//! engine that includes Qt's render thread (the zero-copy renderer, the
//! teardown callback the Qt side calls, and libplacebo's own warnings through
//! them), and a render thread waiting for the GIL deadlocks whenever the GUI
//! thread holds the GIL while it waits for the render thread -- a Python call
//! into Qt that synchronises with rendering does exactly that (measured with
//! `QQuickWindow::grabWindow`, which hangs any Python-implemented video item
//! for the same reason). The teardown in particular runs at a moment chosen
//! by Qt, not by us.
//!
//! So records are copied into owned values and sent over a channel, and one
//! thread of its own hands them to pyo3-log, which keeps Python's logger
//! names and levels. Logging never blocks and never touches Python on the
//! thread that logs. Once Python starts shutting down (`atexit`), records are
//! dropped rather than handed to an interpreter that is going away.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

use crossbeam_channel::{Receiver, Sender, unbounded};
use log::{Level, LevelFilter, Log, Metadata, Record};
use pyo3::prelude::*;

/// A log record that owns its text, so it can cross to another thread.
struct Owned {
    level: Level,
    target: String,
    message: String,
    module_path: Option<String>,
    file: Option<String>,
    line: Option<u32>,
}

struct Forwarding {
    sender: Sender<Owned>,
    closed: Arc<AtomicBool>,
}

/// Libraries whose own logging is chatter rather than news: the PulseAudio
/// client logs every connection at info and the close of cpal's
/// availability-check client as an error ("Client disconnected"). Real stream
/// failures reach the engine through cpal's error callback, which it logs
/// itself; these are passed on at debug level.
const CHATTY: [&str; 1] = ["pulseaudio"];

fn level_of(metadata: &Metadata<'_>) -> Level {
    if CHATTY.iter().any(|t| metadata.target().starts_with(t)) {
        Level::Debug
    } else {
        metadata.level()
    }
}

impl Log for Forwarding {
    fn enabled(&self, metadata: &Metadata<'_>) -> bool {
        level_of(metadata) <= log::max_level() && !self.closed.load(Ordering::Relaxed)
    }

    fn log(&self, record: &Record<'_>) {
        if !self.enabled(record.metadata()) {
            return;
        }
        let _ = self.sender.send(Owned {
            level: level_of(record.metadata()),
            target: record.target().to_owned(),
            message: record.args().to_string(),
            module_path: record.module_path().map(str::to_owned),
            file: record.file().map(str::to_owned),
            line: record.line(),
        });
    }

    fn flush(&self) {}
}

/// Installs the forwarding logger, at the level Python's root logger is at.
/// Must be called with the GIL held (module initialisation).
pub(crate) fn install(py: Python<'_>) -> PyResult<()> {
    let python = pyo3_log::Logger::new(py, pyo3_log::Caching::LoggersAndLevels)?;
    let level: i64 = py
        .import("logging")?
        .call_method0("getLogger")?
        .call_method0("getEffectiveLevel")?
        .extract()?;
    let (sender, receiver) = unbounded();
    let closed = Arc::new(AtomicBool::new(false));
    {
        let closed = closed.clone();
        // Python's own shutdown hook: after it, nothing is handed over.
        let stop = pyo3::types::PyCFunction::new_closure(py, None, None, move |_, _| {
            closed.store(true, Ordering::Relaxed);
        })?;
        py.import("atexit")?.call_method1("register", (stop,))?;
    }
    thread::Builder::new()
        .name("player-log".to_owned())
        .spawn({
            let closed = closed.clone();
            move || forward(&receiver, &python, &closed)
        })
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    if log::set_boxed_logger(Box::new(Forwarding { sender, closed })).is_ok() {
        log::set_max_level(level_filter(level));
    }
    Ok(())
}

fn forward(receiver: &Receiver<Owned>, python: &pyo3_log::Logger, closed: &AtomicBool) {
    for owned in receiver {
        if closed.load(Ordering::Relaxed) {
            continue;
        }
        python.log(
            &Record::builder()
                .level(owned.level)
                .target(&owned.target)
                .args(format_args!("{}", owned.message))
                .module_path(owned.module_path.as_deref())
                .file(owned.file.as_deref())
                .line(owned.line)
                .build(),
        );
    }
}

/// Python's numeric level as the `log` crate's filter.
fn level_filter(level: i64) -> LevelFilter {
    match level {
        ..=10 => LevelFilter::Debug,
        11..=20 => LevelFilter::Info,
        21..=30 => LevelFilter::Warn,
        _ => LevelFilter::Error,
    }
}
