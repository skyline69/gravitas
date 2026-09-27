//! The engine's own network reading (milestone 5 in `native/player/README.md`):
//! parallel ranged reads and a chunk cache, fed to FFmpeg through custom I/O.
//! `source.rs` has the reasoning; this module holds what outlives one stream
//! -- settings, the upstream rate, what hosts have taught -- which the
//! embedder reads the way it reads the Python stream proxy's.

mod avio;
mod cache;
mod http;
mod keep;
mod source;

use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use parking_lot::Mutex;

pub(crate) use avio::CustomIo;
pub(crate) use source::{OpenError, Source};

/// How the engine reads network streams, process-wide.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NetworkSettings {
    /// Read http(s) streams over several connections with a chunk cache;
    /// false leaves them to FFmpeg's own client, one connection each.
    pub parallel: bool,
    /// Where the chunk cache lives (one directory per stream, removed with
    /// it); None keeps a smaller cache in memory.
    pub cache_dir: Option<PathBuf>,
    /// Where files' opening bytes are kept across sessions (see
    /// `keep.rs`); None keeps nothing.
    pub keep_dir: Option<PathBuf>,
}

impl Default for NetworkSettings {
    fn default() -> Self {
        Self {
            parallel: true,
            cache_dir: None,
            keep_dir: None,
        }
    }
}

pub(crate) type Settings = NetworkSettings;

/// Rates are over this window, so a number sampled during a cache fill
/// describes the fill and not the whole episode.
const RATE_WINDOW: Duration = Duration::from_secs(10);
/// Below this, elapsed time says more about scheduling than throughput.
const MIN_RATE_SPAN: Duration = Duration::from_millis(50);
/// URLs remembered as ones this engine failed on; one playback at a time,
/// so this only has to outlive a reconnect or two.
const BROKEN_LIMIT: usize = 8;

/// What outlives one stream.
pub(crate) struct Global {
    pub(crate) settings: Mutex<Settings>,
    pub(crate) meter: RateMeter,
    host_limits: Mutex<HashMap<String, (usize, Instant)>>,
    broken: Mutex<VecDeque<String>>,
    failure: Mutex<Option<String>>,
}

pub(crate) fn global() -> &'static Global {
    static GLOBAL: OnceLock<Global> = OnceLock::new();
    GLOBAL.get_or_init(|| Global {
        settings: Mutex::new(Settings::default()),
        meter: RateMeter::default(),
        host_limits: Mutex::new(HashMap::new()),
        broken: Mutex::new(VecDeque::new()),
        failure: Mutex::new(None),
    })
}

impl Global {
    /// The reader count `host` last accepted, while that is recent.
    pub(crate) fn host_limit(&self, host: &str) -> Option<usize> {
        let limits = self.host_limits.lock();
        let (count, at) = limits.get(host)?;
        source::host_limit_fresh(*at).then_some(*count)
    }

    pub(crate) fn set_host_limit(&self, host: &str, count: usize) {
        self.host_limits
            .lock()
            .insert(host.to_owned(), (count, Instant::now()));
    }

    pub(crate) fn is_broken(&self, url: &str) -> bool {
        self.broken.lock().iter().any(|u| u == url)
    }

    /// Remembers `url` as one to leave to FFmpeg: "no worse than FFmpeg on
    /// its own" has to hold when this module is what is broken.
    pub(crate) fn note_broken(&self, url: &str) {
        let mut broken = self.broken.lock();
        if !broken.iter().any(|u| u == url) {
            broken.push_back(url.to_owned());
            while broken.len() > BROKEN_LIMIT {
                broken.pop_front();
            }
        }
    }

    pub(crate) fn set_failure(&self, failure: Option<String>) {
        *self.failure.lock() = failure;
    }
}

/// Bytes per second off the network, over a short trailing window.
#[derive(Debug, Default)]
pub(crate) struct RateMeter {
    samples: Mutex<VecDeque<(Instant, usize)>>,
}

impl RateMeter {
    pub(crate) fn add(&self, count: usize) {
        let now = Instant::now();
        let mut samples = self.samples.lock();
        samples.push_back((now, count));
        trim(&mut samples, now);
    }

    /// 0.0 until there is a span to divide by: a single piece that landed a
    /// moment ago is not a rate.
    pub(crate) fn bytes_per_s(&self) -> f64 {
        let now = Instant::now();
        let mut samples = self.samples.lock();
        trim(&mut samples, now);
        let Some((first, _)) = samples.front() else {
            return 0.0;
        };
        let span = now.duration_since(*first);
        if span < MIN_RATE_SPAN {
            return 0.0;
        }
        samples.iter().map(|(_, c)| *c).sum::<usize>() as f64 / span.as_secs_f64()
    }
}

fn trim(samples: &mut VecDeque<(Instant, usize)>, now: Instant) {
    while samples
        .front()
        .is_some_and(|(at, _)| now.duration_since(*at) > RATE_WINDOW)
    {
        samples.pop_front();
    }
}

/// Sets how the engine reads network streams from the next load on. A new
/// cache directory is emptied first: whatever is there is a crashed
/// session's stream, and debrid bytes should not outlive their playback.
/// What hosts taught about their limits starts over too: the settings
/// change once at start-up and then only when the viewer changes them, and
/// a limit learned before that is not a reason to read with fewer
/// connections after it (the tests, which configure per test, rely on it).
pub fn configure_network(settings: NetworkSettings) {
    let mut current = global().settings.lock();
    if let Some(directory) = &settings.cache_dir
        && current.cache_dir.as_ref() != Some(directory)
    {
        let _ = std::fs::remove_dir_all(directory);
    }
    *current = settings;
    drop(current);
    global().host_limits.lock().clear();
}

/// Bytes per second the engine has pulled off the network lately -- the
/// line's rate while it reads, where the demuxer's own rate would measure
/// the local cache. 0.0 when nothing was read in the last ten seconds.
#[must_use]
pub fn network_bytes_per_s() -> f64 {
    global().meter.bytes_per_s()
}

/// Why the last stream the engine probed could not be reached, ready to
/// show; None once a host has answered since.
#[must_use]
pub fn last_network_failure() -> Option<String> {
    global().failure.lock().clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_rate_needs_a_span() {
        let meter = RateMeter::default();
        meter.add(1_000_000);
        assert!(meter.bytes_per_s().abs() < f64::EPSILON);
        std::thread::sleep(Duration::from_millis(60));
        meter.add(1_000_000);
        let rate = meter.bytes_per_s();
        assert!(rate > 1_000_000.0, "rate {rate}");
    }

    #[test]
    fn broken_urls_are_remembered_a_few_at_a_time() {
        let global = global();
        for n in 0..=BROKEN_LIMIT {
            global.note_broken(&format!("https://example.com/{n}"));
        }
        assert!(!global.is_broken("https://example.com/0"));
        assert!(global.is_broken(&format!("https://example.com/{BROKEN_LIMIT}")));
    }
}
