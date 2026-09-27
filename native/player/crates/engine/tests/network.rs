//! The engine's own network reading, end to end: a real file served by a
//! local origin that behaves the ways debrid CDNs do -- ranges or not, a
//! limit on concurrent readers, latency -- played through the engine.
//!
//! One test binary of its own, with the tests run one at a time
//! (`SERIAL`), because network settings are process-wide.

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use gravitas_player_engine::{
    AudioBackend, Event, LoadOptions, NetworkSettings, Player, RenderTarget, configure_network,
    last_network_failure, network_bytes_per_s,
};
use parking_lot::Mutex;

static SERIAL: Mutex<()> = Mutex::new(());

/// A 30 s noisy test pattern: incompressible, so the file spans many chunks
/// and runs (~45 MB).
fn sample() -> Option<&'static Path> {
    static SAMPLE: OnceLock<Option<PathBuf>> = OnceLock::new();
    SAMPLE
        .get_or_init(|| {
            let dir =
                std::env::temp_dir().join(format!("gravitas-network-test-{}", std::process::id()));
            std::fs::create_dir_all(&dir).ok()?;
            let path = dir.join("sample.mkv");
            let status = Command::new("ffmpeg")
                .args(["-loglevel", "error", "-y", "-f", "lavfi", "-i"])
                .arg("testsrc2=size=640x360:rate=25:duration=30")
                .args(["-f", "lavfi", "-i", "sine=frequency=440:duration=30"])
                .args(["-vf", "noise=alls=30:allf=t+u"])
                .args(["-c:v", "mpeg4", "-q:v", "3", "-c:a", "mp2"])
                .arg(&path)
                .status()
                .ok()?;
            status.success().then_some(path)
        })
        .as_deref()
}

/// Five minutes of the same noisy pattern, smaller: far longer than the
/// demuxer's own 30 s read-ahead (~100 MB).
fn long_sample() -> Option<&'static Path> {
    static SAMPLE: OnceLock<Option<PathBuf>> = OnceLock::new();
    SAMPLE
        .get_or_init(|| {
            let dir =
                std::env::temp_dir().join(format!("gravitas-network-test-{}", std::process::id()));
            std::fs::create_dir_all(&dir).ok()?;
            let path = dir.join("long.mkv");
            let status = Command::new("ffmpeg")
                .args(["-loglevel", "error", "-y", "-f", "lavfi", "-i"])
                .arg("testsrc2=size=320x180:rate=25:duration=300")
                .args(["-f", "lavfi", "-i", "sine=frequency=440:duration=300"])
                .args(["-vf", "noise=alls=30:allf=t+u"])
                .args(["-c:v", "mpeg4", "-q:v", "3", "-c:a", "mp2"])
                .arg(&path)
                .status()
                .ok()?;
            status.success().then_some(path)
        })
        .as_deref()
}

/// How the origin behaves.
#[derive(Clone, Copy, Default)]
struct Behaviour {
    /// Answer ranges with 206; false sends the whole file with 200.
    no_ranges: bool,
    /// Answer 429 to requests past this many at once (0: no limit).
    concurrency: usize,
    /// Wait this long before answering.
    latency: Duration,
}

/// A minimal HTTP origin for one file.
struct Origin {
    port: u16,
    requests: Arc<AtomicUsize>,
    peak: Arc<AtomicUsize>,
    limited: Arc<AtomicUsize>,
    /// Where each ranged request began, in order.
    starts: Arc<Mutex<Vec<usize>>>,
}

impl Origin {
    fn serve(path: &Path, behaviour: Behaviour) -> Self {
        let body: Arc<Vec<u8>> = Arc::new(std::fs::read(path).expect("the sample reads"));
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port");
        let port = listener.local_addr().unwrap().port();
        let requests = Arc::new(AtomicUsize::new(0));
        let peak = Arc::new(AtomicUsize::new(0));
        let limited = Arc::new(AtomicUsize::new(0));
        let inflight = Arc::new(AtomicUsize::new(0));
        let starts = Arc::new(Mutex::new(Vec::new()));
        {
            let (requests, peak, limited, starts) = (
                requests.clone(),
                peak.clone(),
                limited.clone(),
                starts.clone(),
            );
            thread::spawn(move || {
                for stream in listener.incoming().flatten() {
                    let body = body.clone();
                    let (requests, peak, limited, inflight, starts) = (
                        requests.clone(),
                        peak.clone(),
                        limited.clone(),
                        inflight.clone(),
                        starts.clone(),
                    );
                    thread::spawn(move || {
                        let now = inflight.fetch_add(1, Ordering::SeqCst) + 1;
                        peak.fetch_max(now, Ordering::SeqCst);
                        let counters = Counters {
                            requests: &requests,
                            limited: &limited,
                            starts: &starts,
                        };
                        answer(stream, &body, behaviour, &counters, now);
                        inflight.fetch_sub(1, Ordering::SeqCst);
                    });
                }
            });
        }
        Self {
            port,
            requests,
            peak,
            limited,
            starts,
        }
    }

    fn url(&self) -> String {
        format!("http://127.0.0.1:{}/sample.mkv", self.port)
    }
}

/// What the origin counts, shared by its connections.
struct Counters<'a> {
    requests: &'a AtomicUsize,
    limited: &'a AtomicUsize,
    starts: &'a Mutex<Vec<usize>>,
}

fn answer(
    stream: TcpStream,
    body: &[u8],
    behaviour: Behaviour,
    counters: &Counters<'_>,
    concurrent: usize,
) {
    let mut reader = BufReader::new(stream.try_clone().unwrap());
    let mut range = None;
    let mut line = String::new();
    if reader.read_line(&mut line).unwrap_or(0) == 0 {
        return;
    }
    loop {
        line.clear();
        if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" {
            break;
        }
        if let Some(value) = line.to_ascii_lowercase().strip_prefix("range: bytes=") {
            let (first, last) = value.trim().split_once('-').unwrap_or(("0", ""));
            let first: usize = first.parse().unwrap_or(0);
            let last: usize = last.parse().unwrap_or(body.len() - 1);
            range = Some((first, last.min(body.len() - 1)));
        }
    }
    counters.requests.fetch_add(1, Ordering::SeqCst);
    if let Some((first, _)) = range {
        counters.starts.lock().push(first);
    }
    let mut stream = stream;
    if behaviour.concurrency > 0 && concurrent > behaviour.concurrency {
        counters.limited.fetch_add(1, Ordering::SeqCst);
        let _ = stream.write_all(
            b"HTTP/1.1 429 Too Many Requests\r\nRetry-After: 0\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        );
        return;
    }
    thread::sleep(behaviour.latency);
    let (status, first, last) = match range {
        Some((first, last)) if !behaviour.no_ranges => ("206 Partial Content", first, last),
        _ => ("200 OK", 0, body.len() - 1),
    };
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Length: {}\r\nContent-Range: bytes {first}-{last}/{}\r\nConnection: close\r\n\r\n",
        last - first + 1,
        body.len()
    );
    let head = if behaviour.no_ranges {
        head.replace(
            &format!("Content-Range: bytes {first}-{last}/{}\r\n", body.len()),
            "",
        )
    } else {
        head
    };
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(&body[first..=last]);
}

struct Harness {
    player: Player,
    events: Arc<Mutex<Vec<Event>>>,
    pixels: Vec<u8>,
}

impl Harness {
    fn new() -> Self {
        let events = Arc::new(Mutex::new(Vec::new()));
        let sink = {
            let events = events.clone();
            move |event: Event| events.lock().push(event)
        };
        Self {
            player: Player::new(AudioBackend::Null, sink).expect("the player starts"),
            events,
            pixels: vec![0; 320 * 180 * 4],
        }
    }

    fn load(&self, url: &str, start: f64) {
        self.player
            .load(LoadOptions {
                url: url.to_owned(),
                start,
                network_timeout: Some(Duration::from_secs(10)),
                ..LoadOptions::default()
            })
            .expect("load starts");
    }

    fn until(
        &mut self,
        timeout: Duration,
        what: &str,
        mut condition: impl FnMut(&mut Self) -> bool,
    ) {
        let deadline = Instant::now() + timeout;
        while !condition(self) {
            assert!(
                Instant::now() < deadline,
                "timed out waiting for {what}; events: {:?}",
                self.events.lock()
            );
            let mut target = RenderTarget {
                pixels: &mut self.pixels,
                width: 320,
                height: 180,
                stride: 320 * 4,
            };
            let _ = self.player.render(&mut target);
            thread::sleep(Duration::from_millis(5));
        }
    }

    fn playing_past(&mut self, seconds: f64) {
        self.until(Duration::from_secs(10), "playback", |h| {
            h.player.position() > seconds
        });
    }
}

fn configure(cache_dir: Option<PathBuf>) {
    configure_network(NetworkSettings {
        parallel: true,
        cache_dir,
        keep_dir: None,
    });
}

/// The chunk grid's size, as `net::source` has it.
const CHUNK_BYTES: usize = 1 << 20;

#[test]
fn a_ranged_host_is_read_over_several_connections() {
    let _serial = SERIAL.lock();
    let Some(sample) = sample() else {
        eprintln!("skipped: the ffmpeg CLI is not available to make a sample");
        return;
    };
    configure(None);
    // Latency, so a run is still arriving when the next is claimed: on bare
    // loopback each finishes before the read-ahead window allows another.
    let origin = Origin::serve(
        sample,
        Behaviour {
            latency: Duration::from_millis(150),
            ..Behaviour::default()
        },
    );
    let mut h = Harness::new();
    h.load(&origin.url(), 2.0);
    h.playing_past(6.0);
    assert!(
        origin.peak.load(Ordering::SeqCst) > 1,
        "one connection at a time"
    );
    assert!(network_bytes_per_s() > 0.0, "the line's rate is measured");
    h.player.seek(20.0);
    h.until(Duration::from_secs(10), "the seek", |h| {
        !h.player.is_loading() && h.player.position() > 20.0
    });
}

#[test]
fn a_seek_back_into_what_was_read_asks_the_host_nothing() {
    let _serial = SERIAL.lock();
    let Some(sample) = sample() else {
        return;
    };
    configure(None);
    let origin = Origin::serve(sample, Behaviour::default());
    let mut h = Harness::new();
    h.load(&origin.url(), 0.0);
    h.playing_past(4.0);
    // The first seconds were read long ago, and are cached behind.
    thread::sleep(Duration::from_millis(500));
    let before = origin.requests.load(Ordering::SeqCst);
    h.player.seek(1.0);
    h.until(Duration::from_secs(10), "the seek", |h| {
        !h.player.is_loading() && h.player.position() > 1.0
    });
    assert_eq!(origin.requests.load(Ordering::SeqCst), before);
}

#[test]
fn a_host_without_ranges_is_left_to_ffmpeg() {
    let _serial = SERIAL.lock();
    let Some(sample) = sample() else {
        return;
    };
    configure(None);
    let origin = Origin::serve(
        sample,
        Behaviour {
            no_ranges: true,
            ..Behaviour::default()
        },
    );
    let mut h = Harness::new();
    h.load(&origin.url(), 0.0);
    h.playing_past(1.0);
}

#[test]
fn a_rate_limiting_host_gets_fewer_readers_and_still_plays() {
    let _serial = SERIAL.lock();
    let Some(sample) = sample() else {
        return;
    };
    configure(None);
    let origin = Origin::serve(
        sample,
        Behaviour {
            concurrency: 1,
            latency: Duration::from_millis(50),
            ..Behaviour::default()
        },
    );
    let mut h = Harness::new();
    h.load(&origin.url(), 5.0);
    h.playing_past(6.0);
    assert!(
        origin.limited.load(Ordering::SeqCst) > 0,
        "the limit was hit"
    );
}

#[test]
fn a_host_that_refuses_fails_the_load_at_once_and_says_so() {
    let _serial = SERIAL.lock();
    configure(None);
    // Bound and closed: nothing listens there now.
    let port = TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port();
    let mut h = Harness::new();
    let started = Instant::now();
    h.load(&format!("http://127.0.0.1:{port}/sample.mkv"), 0.0);
    h.until(Duration::from_secs(5), "the load to fail", |h| {
        h.events
            .lock()
            .iter()
            .any(|e| matches!(e, Event::LoadFailed(_)))
    });
    // "At once" against FFmpeg's reconnect ladder (~10 s). Windows answers a
    // refused connect by retrying the SYN twice, 500 ms apart and doubling,
    // so a refusal costs ~2 s there before anything above the socket hears
    // of it (2.05 s measured, loopback).
    let bound = if cfg!(windows) { 3 } else { 2 };
    assert!(
        started.elapsed() < Duration::from_secs(bound),
        "it took {:?}",
        started.elapsed()
    );
    let reason = h
        .events
        .lock()
        .iter()
        .find_map(|e| match e {
            Event::LoadFailed(reason) => Some(reason.clone()),
            _ => None,
        })
        .unwrap();
    assert!(reason.contains("refused"), "{reason}");
    assert_eq!(last_network_failure().as_deref(), Some(reason.as_str()));
}

/// A host that takes the connection and never answers holds the session's
/// opening in a network call nothing interrupts -- until the timeout, 10 s
/// here. Stopping (the viewer going back) and loading the next source must
/// not wait for it: both run on the app's GUI thread.
#[test]
fn stopping_a_load_a_silent_host_holds_returns_at_once() {
    let _serial = SERIAL.lock();
    configure(None);
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let held = Arc::new(Mutex::new(Vec::<TcpStream>::new()));
    {
        let held = held.clone();
        thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                held.lock().push(stream);
            }
        });
    }
    let h = Harness::new();
    let url = format!("http://127.0.0.1:{port}/sample.mkv");
    h.load(&url, 0.0);
    let deadline = Instant::now() + Duration::from_secs(5);
    while held.lock().is_empty() {
        assert!(Instant::now() < deadline, "the load never reached the host");
        thread::sleep(Duration::from_millis(10));
    }
    let began = Instant::now();
    h.player.stop();
    assert!(
        began.elapsed() < Duration::from_millis(500),
        "stop took {:?}",
        began.elapsed()
    );
    let began = Instant::now();
    h.load(&url, 0.0);
    assert!(
        began.elapsed() < Duration::from_millis(500),
        "load took {:?}",
        began.elapsed()
    );
    // Nothing of the stopped load reaches the embedder.
    h.player.stop();
    thread::sleep(Duration::from_millis(100));
    assert!(
        !h.events
            .lock()
            .iter()
            .any(|e| matches!(e, Event::LoadFailed(_))),
        "a stopped load reported a failure: {:?}",
        h.events.lock()
    );
    // Dropping the player still joins every session thread: it has to let
    // the held connections go first, or it waits out the timeout.
    held.lock().clear();
}

#[test]
fn the_disk_cache_goes_with_the_stream() {
    let _serial = SERIAL.lock();
    let Some(sample) = sample() else {
        return;
    };
    let directory =
        std::env::temp_dir().join(format!("gravitas-network-cache-{}", std::process::id()));
    configure(Some(directory.clone()));
    let origin = Origin::serve(sample, Behaviour::default());
    let mut h = Harness::new();
    h.load(&origin.url(), 0.0);
    h.playing_past(1.0);
    let entries = || std::fs::read_dir(&directory).map_or(0, Iterator::count);
    assert_eq!(entries(), 1, "one directory for the stream");
    h.player.stop();
    // The stopped session winds down on a thread of its own (Player::stop),
    // and its cache goes as it does.
    let deadline = Instant::now() + Duration::from_secs(2);
    while entries() != 0 {
        assert!(Instant::now() < deadline, "the stream's cache outlived it");
        thread::sleep(Duration::from_millis(10));
    }
    configure(None);
    let _ = std::fs::remove_dir_all(&directory);
}

#[test]
fn a_file_opened_before_opens_without_its_index_reads() {
    let _serial = SERIAL.lock();
    let Some(sample) = sample() else {
        return;
    };
    let keep = std::env::temp_dir().join(format!("gravitas-network-keep-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&keep);
    configure_network(NetworkSettings {
        parallel: true,
        cache_dir: None,
        keep_dir: Some(keep.clone()),
    });
    let first = Origin::serve(sample, Behaviour::default());
    let mut h = Harness::new();
    h.load(&first.url(), 10.0);
    h.playing_past(10.2);
    h.player.stop();
    // Written on a thread of its own once the file was open.
    let deadline = Instant::now() + Duration::from_secs(5);
    let kept = || std::fs::read_dir(&keep).map_or(0, Iterator::count);
    while kept() == 0 && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(kept(), 1, "the opening reads are kept");

    // Another URL for the same file, the way a debrid link is re-signed.
    let second = Origin::serve(sample, Behaviour::default());
    h.load(&second.url(), 10.0);
    h.playing_past(10.2);
    let size = std::fs::metadata(sample).unwrap().len() as usize;
    let last_chunk = (size - 1) / CHUNK_BYTES;
    let asked: Vec<usize> = first.starts.lock().clone();
    assert!(
        asked.iter().any(|start| start / CHUNK_BYTES == last_chunk),
        "the first open reads the index at the end: {asked:?}"
    );
    let again: Vec<usize> = second.starts.lock().clone();
    assert!(
        again.iter().all(|start| start / CHUNK_BYTES != last_chunk),
        "the second open does not: {again:?}"
    );
    h.player.stop();
    configure(None);
    let _ = std::fs::remove_dir_all(&keep);
}

#[test]
fn a_pause_fills_the_cache_ahead() {
    let _serial = SERIAL.lock();
    let Some(sample) = long_sample() else {
        return;
    };
    let directory =
        std::env::temp_dir().join(format!("gravitas-network-pause-{}", std::process::id()));
    configure(Some(directory.clone()));
    let origin = Origin::serve(sample, Behaviour::default());
    let mut h = Harness::new();
    h.load(&origin.url(), 0.0);
    h.playing_past(0.2);
    h.player.set_paused(true);
    // The demuxer stops 30 s in, a sixth of the file; paused, the reader
    // goes on to the end of it.
    let size = std::fs::metadata(sample).unwrap().len() as usize;
    let last_chunk = (size - 1) / CHUNK_BYTES;
    h.until(Duration::from_secs(20), "the whole file", |_| {
        origin
            .starts
            .lock()
            .iter()
            .any(|start| (start / CHUNK_BYTES..start / CHUNK_BYTES + 8).contains(&last_chunk))
    });
    // Resumed, it plays from what the pause fetched.
    h.player.set_paused(false);
    h.playing_past(1.0);
    h.player.stop();
    configure(None);
    let _ = std::fs::remove_dir_all(&directory);
}

#[test]
fn what_lies_behind_the_resume_point_is_fetched_while_idle() {
    let _serial = SERIAL.lock();
    let Some(sample) = sample() else {
        return;
    };
    configure(None);
    let origin = Origin::serve(sample, Behaviour::default());
    let mut h = Harness::new();
    h.load(&origin.url(), 20.0);
    h.playing_past(20.5);
    // Paused, the reader wants nothing: the line is spare, and the backfill
    // pulls in what lies before 20 s.
    h.player.set_paused(true);
    let deadline = Instant::now() + Duration::from_secs(10);
    let mut quiet_since = (origin.requests.load(Ordering::SeqCst), Instant::now());
    while Instant::now() < deadline {
        let now = origin.requests.load(Ordering::SeqCst);
        if now != quiet_since.0 {
            quiet_since = (now, Instant::now());
        } else if quiet_since.1.elapsed() > Duration::from_millis(500) {
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }
    let before = origin.requests.load(Ordering::SeqCst);
    h.player.seek(17.0);
    h.player.set_paused(false);
    h.until(Duration::from_secs(10), "the seek back", |h| {
        !h.player.is_loading() && h.player.position() > 17.2
    });
    assert_eq!(
        origin.requests.load(Ordering::SeqCst),
        before,
        "the seek back was a cold fetch"
    );
}
