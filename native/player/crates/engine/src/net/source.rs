//! One stream, read over several connections at once and cached on an
//! aligned chunk grid -- what the app's Python stream proxy does for mpv,
//! inside the engine, where the reader's position is known exactly and a seek
//! is a new read position rather than a new connection.
//!
//! **Why.** FFmpeg's HTTP client opens one connection and reads it front to
//! back, and a host that caps what one connection may do (how most debrid
//! services shape traffic) caps playback whatever the line can carry. Here
//! `CONNECTIONS` workers fetch runs of consecutive chunks in parallel, each
//! run one ranged request, and the demuxer reads the chunks in order. A read
//! does not wait for a whole chunk: it takes the bytes of a chunk still
//! arriving, so the header of a file costs one round trip, not a mebibyte.
//!
//! **What it refuses to do.** It never guesses: a host that does not answer a
//! range with 206 and a total gets FFmpeg's own client (`Source::open` returns
//! None), which is what the engine did before. A probe that failed is a fact
//! about one moment, never recorded as "no ranges". A refused connect is the
//! one failure that ends the load at once (see `http::Failure::Unreachable`).
//!
//! **Rate limits.** A 429 (or a 503 with a `Retry-After`) is the host asking
//! for fewer readers, not a failed chunk. At the moment it refuses, the host
//! is serving exactly the requests of ours that are receiving a body, so that
//! is the reader count it accepts, and the stream drops to it at once (the
//! Python proxy stepped down one reader per half second, which cost seconds
//! of refusals at every start on a throttled account). The refused worker
//! hands its run back -- a chunk must never wait out a backoff in the hands
//! of a worker that cannot fetch it -- and backs off, at least a step
//! whatever `Retry-After` says. What a host accepted is remembered for new
//! streams from it (`HOST_LIMIT_TTL`).
//!
//! **Read-ahead earns its size.** Opening a file is a few scattered reads --
//! the header, the index at the end, the resume point -- and a window that
//! filled up at the first byte spent connections on stretches the demuxer
//! was about to jump away from. The window starts at one run and grows as the
//! reader goes on in sequence; a jump starts it again.
//!
//! **The backfill.** Resuming an episode mid-way means nothing before the
//! resume point was ever fetched, so stepping back is a cold fetch. One worker
//! walks backwards from it, only while there is nothing better to do: the
//! read-ahead window full and the reader not waiting.

use std::collections::{BTreeSet, HashMap, VecDeque};
use std::io::{self, Read};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use parking_lot::{Condvar, Mutex};

use super::cache::ChunkCache;
use super::http::{self, Client, Failure};
use super::keep;
use super::{Settings, global};

/// The grid everything is cut to. About a third of a second of a 25 Mbit/s
/// stream: small next to a request's round trip, big enough that its request
/// overhead is nothing next to its body.
pub(crate) const CHUNK_BYTES: u64 = 1024 * 1024;
/// Readers per stream. Four, not six: a `TorBox` CDN answered 2 of 6 concurrent
/// requests on one file with 429 while one connection alone ran at 192 Mbit/s.
pub(crate) const CONNECTIONS: usize = 4;
/// Consecutive chunks per request. One request per chunk was ~20 requests a
/// second at start-up on a fast line, which a CDN answered with 429.
const RUN_CHUNKS: u64 = 8;
/// How far ahead of the reader the workers may run with the cache in memory.
const READAHEAD_CHUNKS: u64 = 24;
/// The cache in memory, when there is no directory for it.
const MEMORY_CACHE_BYTES: usize = 192 * 1024 * 1024;
/// The cache on disk: the stream's main cache, 2:1 ahead of the reader and
/// behind it -- ahead carries playback through a network drop, behind makes
/// a seek back a local read.
const DISK_CACHE_BYTES: usize = 3 * 1024 * 1024 * 1024;
const DISK_CACHE_BEHIND_BYTES: usize = 1024 * 1024 * 1024;
/// How far behind the resume point the backfill fetches.
const BACKFILL_BYTES: u64 = 96 * 1024 * 1024;
/// How much of a response body is read at a time.
const PIECE_BYTES: usize = 64 * 1024;

/// Tries a run gets when it fails outright (a failure that delivered
/// something resets the count).
const RUN_ATTEMPTS: u32 = 3;
const RETRY_DELAY: Duration = Duration::from_millis(500);
/// Refusals in a row, with nothing delivered in between, after which the
/// host is taken at its word: the stream fails, and the player's stall
/// recovery takes it from there, as it does a dropped connection.
const REFUSALS_BEFORE_GIVING_UP: u32 = 40;
const RATE_LIMIT_DELAYS: [Duration; 5] = [
    Duration::from_millis(250),
    Duration::from_millis(500),
    Duration::from_secs(1),
    Duration::from_secs(2),
    Duration::from_secs(4),
];
/// Tries the probe gets against a rate limit: ~12 s on the steps above.
const PROBE_RATE_LIMIT_ATTEMPTS: u32 = 6;
/// How long a host's accepted reader count is remembered.
const HOST_LIMIT_TTL: Duration = Duration::from_secs(15 * 60);
/// How often a waiting reader or an idle worker looks up.
const WAIT_STEP: Duration = Duration::from_millis(50);
const IDLE_STEP: Duration = Duration::from_millis(500);

/// Why a stream could not be opened here at all.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub(crate) enum OpenError {
    /// No connection could be made; the message says why, for the viewer.
    #[error("{0}")]
    Unreachable(String),
    /// The load was stopped while probing.
    #[error("stopped")]
    Stopped,
}

/// A stream being read, and the workers fetching it. Dropping it stops them.
#[derive(Debug)]
pub(crate) struct Source {
    shared: Arc<Shared>,
}

#[derive(Debug)]
struct Shared {
    client: Client,
    /// What the addon handed over, and where its redirects end: the resolved
    /// URL first, the original as the retry (a CDN URL can expire).
    url: String,
    resolved: String,
    host: String,
    size: u64,
    last_chunk: u64,
    readahead: u64,
    /// Where files' opening bytes are kept across sessions.
    keep_dir: Option<PathBuf>,
    state: Mutex<State>,
    changed: Condvar,
    stop: AtomicBool,
}

#[derive(Debug)]
struct State {
    cache: ChunkCache,
    /// Chunks a worker is fetching, with the bytes that have arrived.
    inflight: HashMap<u64, Vec<u8>>,
    /// The next chunk forward workers claim.
    cursor: u64,
    /// The chunk the reader is at.
    reading: u64,
    /// The reader is waiting for bytes that have not arrived.
    waiting: bool,
    /// Chunks read in sequence since the last jump (see `window`).
    sequential: u64,
    /// Playback is paused: the read-ahead window is the whole cache.
    paused: bool,
    /// Forward workers allowed now; only shrinks, on the host's say-so.
    parallel: usize,
    /// Requests receiving a body right now.
    streaming: usize,
    /// Refusals since a byte last arrived.
    refusals: u32,
    backfill: Option<Backfill>,
    /// Why the stream cannot go on, once a run has failed for good.
    failure: Option<String>,
    /// The chunks the reader has touched while the file is being opened
    /// (see `mark_opened`); None once it is open.
    opening: Option<BTreeSet<u64>>,
    /// The file's key (see `keep.rs`), once its first chunk is in.
    key: Option<String>,
    /// Its opening bytes came from an earlier session.
    restored: bool,
}

#[derive(Debug, Clone, Copy)]
struct Backfill {
    /// The next chunk down, and where it stops.
    next: u64,
    floor: u64,
    done: bool,
}

/// What a run is for: the reader's way forward, or the backfill behind.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Forward,
    Backfill,
}

impl Source {
    /// Probes `url` and starts reading it, or returns None when FFmpeg's own
    /// client should read it instead: not http(s), turned off, a host that
    /// does not serve ranges, a probe that failed for a moment, or a URL this
    /// has failed on before.
    ///
    /// # Errors
    /// When the host refused the connection, or the load was stopped.
    pub(crate) fn open(
        url: &str,
        headers: &[(String, String)],
        user_agent: Option<&str>,
        stopping: &dyn Fn() -> bool,
    ) -> Result<Option<Self>, OpenError> {
        let settings = global().settings.lock().clone();
        if !settings.parallel || !(url.starts_with("http://") || url.starts_with("https://")) {
            return Ok(None);
        }
        if global().is_broken(url) {
            log::info!(
                "reading {} with FFmpeg: this engine failed on it before",
                redact(url)
            );
            return Ok(None);
        }
        let host = http::host_of(url).unwrap_or_default();
        let parallel = global().host_limit(&host).unwrap_or(CONNECTIONS);
        let client = Client::new(headers, user_agent, CONNECTIONS);
        let probing = Instant::now();
        let probe = match probe(&client, url, stopping) {
            Ok(probe) => probe,
            Err(Failure::Unreachable { host }) => {
                let why = http::describe_unreachable(&host);
                global().set_failure(Some(why.clone()));
                return Err(OpenError::Unreachable(why));
            }
            Err(Failure::Other(reason)) if reason == STOPPED => return Err(OpenError::Stopped),
            Err(failure) => {
                // A fact about one moment, not about the host: this load goes
                // through FFmpeg, the next one probes again.
                log::warn!(
                    "probing {} failed ({failure}); FFmpeg reads it this time",
                    redact(url)
                );
                return Ok(None);
            }
        };
        global().set_failure(None);
        let (Some(size), true) = (probe.size, probe.ranged) else {
            log::info!(
                "{} does not serve ranges; FFmpeg reads it on one connection",
                redact(url)
            );
            return Ok(None);
        };
        log::info!(
            "reading {} over {parallel} connection{}: {size} bytes (probed in {} ms)",
            redact(url),
            if parallel == 1 { "" } else { "s" },
            probing.elapsed().as_millis()
        );
        Ok(Some(Self::start(
            client,
            url,
            &probe.resolved,
            &host,
            size,
            parallel,
            &settings,
        )))
    }

    fn start(
        client: Client,
        url: &str,
        resolved: &str,
        host: &str,
        size: u64,
        parallel: usize,
        settings: &Settings,
    ) -> Self {
        let cache = match &settings.cache_dir {
            Some(directory) => ChunkCache::new(
                DISK_CACHE_BYTES,
                DISK_CACHE_BEHIND_BYTES,
                Some(stream_directory(directory)),
            ),
            None => ChunkCache::new(MEMORY_CACHE_BYTES, MEMORY_CACHE_BYTES / 2, None),
        };
        let chunks_held = MEMORY_CACHE_BYTES.max(cache.ahead_bytes()) as u64 / CHUNK_BYTES;
        let window = if settings.cache_dir.is_some() {
            cache.ahead_bytes() as u64 / CHUNK_BYTES
        } else {
            READAHEAD_CHUNKS
        };
        // Never further ahead than the cache holds: a run landing faster than
        // the reader takes it would push out what it is about to read.
        let readahead = window.min(chunks_held.saturating_sub(1)).max(1);
        let shared = Arc::new(Shared {
            client,
            url: url.to_owned(),
            resolved: resolved.to_owned(),
            host: host.to_owned(),
            size,
            last_chunk: (size.max(1) - 1) / CHUNK_BYTES,
            readahead,
            keep_dir: settings.keep_dir.clone(),
            state: Mutex::new(State {
                cache,
                inflight: HashMap::new(),
                cursor: 0,
                reading: 0,
                waiting: false,
                sequential: 0,
                paused: false,
                parallel,
                streaming: 0,
                refusals: 0,
                backfill: None,
                failure: None,
                opening: Some(BTreeSet::new()),
                key: None,
                restored: false,
            }),
            changed: Condvar::new(),
            stop: AtomicBool::new(false),
        });
        for worker in 0..CONNECTIONS {
            let shared = shared.clone();
            let _ = thread::Builder::new()
                .name("player-net".to_owned())
                .spawn(move || forward(&shared, worker));
        }
        {
            let shared = shared.clone();
            let _ = thread::Builder::new()
                .name("player-net-backfill".to_owned())
                .spawn(move || backfill(&shared));
        }
        Self { shared }
    }

    /// The stream's length in bytes.
    pub(crate) fn size(&self) -> u64 {
        self.shared.size
    }

    /// Reads at `position` into `buffer`, waiting for bytes that have not
    /// arrived; 0 at the end of the stream. `hot` holds the last whole chunk
    /// read, so a chunk on disk is read from it once, not once per call.
    ///
    /// # Errors
    /// When the stream has failed (a run that would not come), or
    /// `interrupted` says the load is over (`ErrorKind::Interrupted`).
    pub(crate) fn read(
        &self,
        position: u64,
        buffer: &mut [u8],
        hot: &mut Option<(u64, Arc<[u8]>)>,
        interrupted: &dyn Fn() -> bool,
    ) -> io::Result<usize> {
        let shared = &*self.shared;
        if position >= shared.size || buffer.is_empty() {
            return Ok(0);
        }
        let index = position / CHUNK_BYTES;
        let offset = (position % CHUNK_BYTES) as usize;
        if let Some((_, chunk)) = hot.as_ref().filter(|(i, _)| *i == index) {
            return Ok(copy_from(chunk, offset, buffer));
        }
        let mut state = shared.state.lock();
        if state.reading != index {
            state.sequential = if index == state.reading + 1 {
                state.sequential + 1
            } else {
                0
            };
            state.reading = index;
            state.cache.anchor = index;
            shared.changed.notify_all();
        }
        if let Some(opening) = &mut state.opening {
            opening.insert(index);
        }
        loop {
            if let Some(chunk) = state.cache.get(index) {
                state.waiting = false;
                let copied = copy_from(&chunk, offset, buffer);
                *hot = Some((index, chunk));
                return Ok(copied);
            }
            if let Some(partial) = state.inflight.get(&index)
                && partial.len() > offset
            {
                let copied = copy_from(partial, offset, buffer);
                state.waiting = false;
                return Ok(copied);
            }
            if let Some(failure) = &state.failure {
                return Err(io::Error::other(failure.clone()));
            }
            if interrupted() || shared.stop.load(Ordering::Acquire) {
                return Err(io::ErrorKind::Interrupted.into());
            }
            if !state.inflight.contains_key(&index) && state.cursor != index {
                // Nobody is fetching it: a seek, or a chunk evicted before it
                // was read. The workers start again from here.
                state.cursor = index;
                shared.changed.notify_all();
            }
            state.waiting = true;
            shared.changed.wait_for(&mut state, WAIT_STEP);
        }
    }

    /// The file is open (and at its start position): keeps the chunks read
    /// on the way for the next session that opens it, unless they came from
    /// one. Written on a thread of its own; playback does not wait for it.
    pub(crate) fn mark_opened(&self) {
        let shared = &*self.shared;
        let Some(root) = shared.keep_dir.clone() else {
            return;
        };
        let (key, chunks) = {
            let mut state = shared.state.lock();
            let Some(opening) = state.opening.take() else {
                return;
            };
            let Some(key) = state.key.clone().filter(|_| !state.restored) else {
                return;
            };
            let chunks: Vec<(u64, Arc<[u8]>)> = opening
                .into_iter()
                .filter_map(|index| state.cache.get(index).map(|chunk| (index, chunk)))
                .collect();
            (key, chunks)
        };
        let bytes: usize = chunks.iter().map(|(_, c)| c.len()).sum();
        if bytes > keep::MAX_FILE_BYTES {
            log::debug!("not keeping {} MiB of opening reads", bytes >> 20);
            return;
        }
        let _ = thread::Builder::new()
            .name("player-net-keep".to_owned())
            .spawn(move || keep::save(&root, &key, &chunks));
    }

    /// Playback paused or resumed. Paused, the workers read ahead as far as
    /// the cache holds; resumed, the window goes back to following the
    /// reader, and what the pause fetched is read from the cache.
    pub(crate) fn set_paused(&self, paused: bool) {
        let mut state = self.shared.state.lock();
        if state.paused != paused {
            state.paused = paused;
            self.shared.changed.notify_all();
        }
    }

    /// Starts the backfill behind `position`, the resume point: once per
    /// stream, and only when there is something behind it.
    pub(crate) fn backfill_from(&self, position: u64) {
        let index = position / CHUNK_BYTES;
        let mut state = self.shared.state.lock();
        if state.backfill.is_some() || index == 0 {
            return;
        }
        state.backfill = Some(Backfill {
            next: index - 1,
            floor: index.saturating_sub(BACKFILL_BYTES / CHUNK_BYTES),
            done: false,
        });
        self.shared.changed.notify_all();
    }
}

impl Drop for Source {
    fn drop(&mut self) {
        // The workers are not joined: one blocked in a read returns when its
        // piece arrives or its request times out, and the load that dropped
        // this must not wait for that. Each finishes the piece in hand and
        // returns; the cache goes now.
        self.shared.stop.store(true, Ordering::Release);
        self.shared.state.lock().cache.close();
        self.shared.changed.notify_all();
    }
}

/// The marker a probe interrupted by a stop returns.
const STOPPED: &str = "stopped";

/// The probe, waiting out a rate limit on the run's own schedule: a 429 is
/// not "no ranges", and recording it as that sent every later connection of
/// a throttled account through the fallback.
fn probe(client: &Client, url: &str, stopping: &dyn Fn() -> bool) -> Result<http::Probe, Failure> {
    let mut attempt = 0;
    loop {
        match client.probe(url) {
            Err(Failure::RateLimited { after }) if attempt < PROBE_RATE_LIMIT_ATTEMPTS => {
                let step = RATE_LIMIT_DELAYS[(attempt as usize).min(RATE_LIMIT_DELAYS.len() - 1)];
                attempt += 1;
                if !sleep_unless(after.unwrap_or_default().max(step), stopping) {
                    return Err(Failure::Other(STOPPED.to_owned()));
                }
            }
            other => return other,
        }
    }
}

/// A forward worker: claims the next runs ahead of the reader.
fn forward(shared: &Arc<Shared>, worker: usize) {
    let mut refused = 0;
    while !shared.stop.load(Ordering::Acquire) {
        let run = {
            let mut state = shared.state.lock();
            if worker >= state.parallel {
                // The host takes fewer readers; the ones below the limit
                // carry the stream.
                return;
            }
            let run = if state.failure.is_some() {
                None
            } else {
                claim_forward(&mut state, shared)
            };
            if run.is_none() {
                shared.changed.wait_for(&mut state, IDLE_STEP);
            }
            run
        };
        if let Some(run) = run {
            match fetch_run(shared, run, Kind::Forward) {
                Outcome::Refused { after } => {
                    back_off(shared, after, refused);
                    refused += 1;
                }
                Outcome::Fetched => refused = 0,
            }
        }
    }
}

/// How far ahead of the reader the workers may claim now: one run at first,
/// growing as the reader goes on in sequence, never past `readahead` -- and
/// all of it while playback is paused, when the demuxer has stopped reading
/// (its own read-ahead is full) and the line is the viewer's to spare.
fn window(shared: &Shared, state: &State) -> u64 {
    if state.paused {
        return shared.readahead;
    }
    shared
        .readahead
        .min(RUN_CHUNKS.saturating_add(state.sequential.saturating_mul(2)))
}

/// The next stretch nobody holds, from the cursor, within the read-ahead
/// window. Waits for room for a whole run while plenty is buffered: taking
/// each freed slot as it comes turns steady play back into a request per
/// chunk.
fn claim_forward(state: &mut State, shared: &Shared) -> Option<Vec<u64>> {
    if !forward_has_room(shared, state) {
        return None;
    }
    let ahead = state.cursor.saturating_sub(state.reading);
    let room = window(shared, state).saturating_sub(ahead);
    let mut run = Vec::new();
    while state.cursor <= shared.last_chunk && (run.len() as u64) < RUN_CHUNKS.min(room) {
        let index = state.cursor;
        if state.cache.contains(index) || state.inflight.contains_key(&index) {
            if !run.is_empty() {
                break;
            }
            state.cursor += 1;
            continue;
        }
        state.inflight.insert(index, Vec::new());
        run.push(index);
        state.cursor += 1;
    }
    (!run.is_empty()).then_some(run)
}

/// Whether the forward workers may claim a run now.
fn forward_has_room(shared: &Shared, state: &State) -> bool {
    if state.cursor > shared.last_chunk {
        return false;
    }
    let window = window(shared, state);
    let ahead = state.cursor.saturating_sub(state.reading);
    let room = window.saturating_sub(ahead);
    let whole_run = RUN_CHUNKS.min(window);
    room > 0 && (room >= whole_run || ahead <= whole_run)
}

/// Whether the line is spare: the reader has what it needs and the way
/// forward has nothing to claim.
fn spare(shared: &Shared, state: &State) -> bool {
    !state.waiting && state.failure.is_none() && !forward_has_room(shared, state)
}

/// The backfill worker: fetches downwards from the resume point, only while
/// the forward workers have nothing to do and the reader is not waiting.
fn backfill(shared: &Arc<Shared>) {
    let mut refused = 0;
    while !shared.stop.load(Ordering::Acquire) {
        let run = {
            let mut state = shared.state.lock();
            let run = claim_backfill(shared, &mut state);
            if run.is_none() {
                shared.changed.wait_for(&mut state, IDLE_STEP);
            }
            run
        };
        if let Some(run) = run {
            match fetch_run(shared, run, Kind::Backfill) {
                Outcome::Refused { after } => {
                    back_off(shared, after, refused);
                    refused += 1;
                }
                Outcome::Fetched => refused = 0,
            }
        }
    }
}

/// Waits out a refusal: `Retry-After` when the host gave one, but never less
/// than the backoff step -- a host answering 0 means "not now", and taking it
/// literally burns every retry inside a millisecond.
fn back_off(shared: &Shared, after: Option<Duration>, refused: usize) {
    let step = RATE_LIMIT_DELAYS[refused.min(RATE_LIMIT_DELAYS.len() - 1)];
    let stopping = || shared.stop.load(Ordering::Acquire);
    sleep_unless(after.unwrap_or_default().max(step), &stopping);
}

fn claim_backfill(shared: &Shared, state: &mut State) -> Option<Vec<u64>> {
    let spare = spare(shared, state);
    let backfill = state.backfill.as_mut().filter(|b| !b.done)?;
    if !spare {
        return None;
    }
    let mut run = Vec::new();
    while (run.len() as u64) < RUN_CHUNKS && !backfill.done {
        let index = backfill.next;
        if index <= backfill.floor {
            backfill.done = true;
        } else {
            backfill.next -= 1;
        }
        if state.cache.contains(index) || state.inflight.contains_key(&index) {
            if !run.is_empty() {
                break;
            }
            continue;
        }
        state.inflight.insert(index, Vec::new());
        run.push(index);
    }
    run.sort_unstable();
    (!run.is_empty()).then_some(run)
}

/// How a run went, for the worker that fetched it.
enum Outcome {
    /// Fetched, or given up for a reason of its own (the reader moved on, a
    /// failure recorded on the stream).
    Fetched,
    /// The host refused it; the run was handed back.
    Refused { after: Option<Duration> },
}

/// Fetches consecutive chunks `run` in one ranged request, handing each to
/// the cache the moment it is complete, retried from the first chunk not
/// delivered. A refusal hands the run back instead (see the module docs).
fn fetch_run(shared: &Shared, run: Vec<u64>, kind: Kind) -> Outcome {
    let mut pending: VecDeque<u64> = run.into();
    let mut failures = 0;
    let mut last_error = String::new();
    let mut abandoned = false;
    let mut refused = false;
    let mut retry_after = None;
    let mut refused_by = String::new();
    while !pending.is_empty() && failures < RUN_ATTEMPTS && !shared.stop.load(Ordering::Acquire) {
        let first = pending[0];
        let last = pending[pending.len() - 1];
        let start = first * CHUNK_BYTES;
        let end = ((last + 1) * CHUNK_BYTES - 1).min(shared.size - 1);
        let url = if failures == 0 {
            &shared.resolved
        } else {
            &shared.url
        };
        shared.state.lock().inflight.insert(first, Vec::new());
        let asked = Instant::now();
        let asked_host = http::host_of(url).unwrap_or_default();
        match shared.client.range(url, start, end) {
            Err(Failure::RateLimited { after }) => {
                log::debug!(
                    "{asked_host}: chunks {first}-{last} refused after {} ms",
                    asked.elapsed().as_millis()
                );
                refused = true;
                retry_after = after;
                refused_by = asked_host;
                break;
            }
            Err(failure) => {
                last_error = failure.to_string();
                failures += 1;
                if failures < RUN_ATTEMPTS {
                    thread::sleep(RETRY_DELAY * failures);
                }
            }
            Ok(body) => {
                log::debug!(
                    "{asked_host}: chunks {first}-{last} answered in {} ms",
                    asked.elapsed().as_millis()
                );
                shared.state.lock().streaming += 1;
                let streamed = stream_body(shared, body, &mut pending, kind);
                shared.state.lock().streaming -= 1;
                log::debug!(
                    "{asked_host}: chunks {first}-{last} {} after {} ms",
                    match &streamed {
                        Streamed::Done => "done",
                        Streamed::Abandoned => "abandoned",
                        Streamed::Broke { .. } => "broken off",
                    },
                    asked.elapsed().as_millis()
                );
                match streamed {
                    Streamed::Done => {}
                    Streamed::Abandoned => {
                        abandoned = true;
                        break;
                    }
                    Streamed::Broke { progressed, error } => {
                        last_error = error;
                        failures = if progressed { 0 } else { failures + 1 };
                        if failures < RUN_ATTEMPTS {
                            thread::sleep(RETRY_DELAY * failures.max(1));
                        }
                    }
                }
            }
        }
    }
    settle_run(
        shared,
        kind,
        &pending,
        &Ended {
            abandoned,
            refused,
            retry_after,
            refused_by,
            last_error,
        },
    )
}

/// How a run's requests ended, for `settle_run`.
struct Ended {
    abandoned: bool,
    /// The host refused the run, naming this wait or none.
    refused: bool,
    retry_after: Option<Duration>,
    /// The host that refused: the CDN the redirects led to, or the addon's
    /// signing endpoint the retry goes back to.
    refused_by: String,
    last_error: String,
}

/// Puts what a run did not deliver back where it belongs -- handed back,
/// resumed later, or recorded as the stream's failure.
fn settle_run(shared: &Shared, kind: Kind, pending: &VecDeque<u64>, ended: &Ended) -> Outcome {
    let Ended {
        abandoned,
        refused,
        retry_after,
        refused_by,
        last_error,
    } = ended;
    let abandoned = *abandoned;
    let mut state = shared.state.lock();
    for index in pending {
        state.inflight.remove(index);
    }
    if abandoned
        && kind == Kind::Backfill
        && let (Some(&highest), Some(backfill)) = (pending.back(), state.backfill.as_mut())
    {
        // Yielded to the way forward: what it did not fetch is still to do.
        backfill.next = backfill.next.max(highest);
        backfill.done = false;
    }
    if *refused {
        if kind == Kind::Forward {
            // Handed back: whichever worker the host is serving takes it.
            state.cursor = state.cursor.min(pending[0]);
        } else if let Some(backfill) = state.backfill.as_mut() {
            backfill.next = backfill.next.max(pending[pending.len() - 1]);
            backfill.done = false;
        }
        state.refusals += 1;
        if state.refusals >= REFUSALS_BEFORE_GIVING_UP && state.failure.is_none() {
            let why = format!("{} refused every request for a while (429)", shared.host);
            log::warn!("{why}");
            state.failure = Some(why);
        }
        drop(state);
        slow_down(shared, refused_by);
        shared.changed.notify_all();
        return Outcome::Refused {
            after: *retry_after,
        };
    }
    let stopped = shared.stop.load(Ordering::Acquire);
    if !pending.is_empty() && !abandoned && !stopped {
        let why = format!(
            "chunks {}-{} of {} would not come: {last_error}",
            pending[0],
            pending[pending.len() - 1],
            redact(&shared.url)
        );
        match kind {
            Kind::Forward => {
                log::warn!("{why}");
                state.failure = Some(why);
                drop(state);
                global().note_broken(&shared.url);
            }
            Kind::Backfill => {
                log::info!("the backfill stops: {why}");
                if let Some(backfill) = state.backfill.as_mut() {
                    backfill.done = true;
                }
            }
        }
    }
    shared.changed.notify_all();
    Outcome::Fetched
}

/// How streaming a body ended.
enum Streamed {
    /// Every pending chunk arrived.
    Done,
    /// The reader moved away from this run; what arrived is kept.
    Abandoned,
    /// The body ended or failed early.
    Broke { progressed: bool, error: String },
}

/// Cuts `body` into the pending chunks as it arrives.
fn stream_body(
    shared: &Shared,
    mut body: http::Body,
    pending: &mut VecDeque<u64>,
    kind: Kind,
) -> Streamed {
    let mut piece = vec![0u8; PIECE_BYTES];
    let mut progressed = false;
    // The file's start, gathered here while it arrives to key the file by
    // (see `restore`); only for a run that begins at the file's start.
    let mut head = (pending.front() == Some(&0)
        && shared.keep_dir.is_some()
        && shared.state.lock().key.is_none())
    .then(Vec::new);
    loop {
        let read = match body.read(&mut piece) {
            Ok(0) => {
                return Streamed::Broke {
                    progressed,
                    error: "the host ended the range early".to_owned(),
                };
            }
            Ok(read) => read,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => {
                return Streamed::Broke {
                    progressed,
                    error: error.to_string(),
                };
            }
        };
        global().meter.add(read);
        // The file's start is held back until it keys the file, and the
        // kept chunks are restored before any of it is published: the
        // demuxer reads a Matroska header off the first few KiB and jumps
        // straight to the index, and the kept index must be in the cache by
        // then or it is fetched again (measured: publishing as it arrived,
        // one warm open in two or three still asked for the index). At
        // 64 KiB the wait is a few milliseconds of a round trip's worth.
        let released: Vec<u8>;
        let mut bytes: &[u8] = &piece[..read];
        if let Some(start) = &mut head {
            let take = (keep::KEY_BYTES - start.len()).min(read);
            start.extend_from_slice(&piece[..take]);
            if !keep::keyable(start, shared.size) {
                continue;
            }
            restore(shared, start);
            let mut whole = head.take().unwrap_or_default();
            whole.extend_from_slice(&piece[take..read]);
            released = whole;
            bytes = &released;
        }
        let mut state = shared.state.lock();
        state.refusals = 0;
        while let (Some(&index), false) = (pending.front(), bytes.is_empty()) {
            let length = chunk_length(shared, index);
            let partial = state.inflight.entry(index).or_default();
            if partial.capacity() == 0 {
                partial.reserve_exact(length);
            }
            let take = (length - partial.len()).min(bytes.len());
            partial.extend_from_slice(&bytes[..take]);
            bytes = &bytes[take..];
            if partial.len() == length {
                let chunk: Arc<[u8]> = state.inflight.remove(&index).unwrap_or_default().into();
                state.cache.put(index, chunk);
                pending.pop_front();
                progressed = true;
                if let Some(&next) = pending.front() {
                    state.inflight.insert(next, Vec::new());
                }
            }
        }
        shared.changed.notify_all();
        if pending.is_empty() {
            return Streamed::Done;
        }
        if shared.stop.load(Ordering::Acquire) {
            return Streamed::Abandoned;
        }
        if kind == Kind::Forward {
            // The reader went somewhere else (a seek): this run's remaining
            // chunks are not what anyone is waiting for any more.
            let first = pending[0];
            let last = pending[pending.len() - 1];
            if last < state.reading || first > state.reading + window(shared, &state) {
                return Streamed::Abandoned;
            }
        } else if !spare(shared, &state) {
            // The backfill yields the moment the way forward needs the line.
            return Streamed::Abandoned;
        }
    }
}

/// Keys the file by its first chunk, and hands the cache what an earlier
/// session kept of its opening reads (see `keep.rs`), before the demuxer
/// asks for them.
fn restore(shared: &Shared, head: &[u8]) {
    if !keep::keyable(head, shared.size) {
        return;
    }
    let key = keep::key(shared.size, head);
    let kept = shared
        .keep_dir
        .as_deref()
        .map(|root| keep::load(root, &key))
        .unwrap_or_default();
    let mut state = shared.state.lock();
    if state.key.is_some() {
        return;
    }
    state.key = Some(key);
    let mut restored = 0;
    for (index, chunk) in kept {
        if index <= shared.last_chunk
            && !state.cache.contains(index)
            && !state.inflight.contains_key(&index)
        {
            state.cache.put(index, chunk);
            restored += 1;
        }
    }
    if restored > 0 {
        state.restored = true;
        log::info!("{restored} chunks of this file's opening reads kept from an earlier session");
        shared.changed.notify_all();
    }
}

/// Takes the host at its word after a refusal: it accepts as many readers
/// as it is serving right now (floored at one -- exactly what FFmpeg's own
/// client would do), remembered for the host.
fn slow_down(shared: &Shared, refused_by: &str) {
    let mut state = shared.state.lock();
    let accepted = state.streaming.max(1);
    if accepted >= state.parallel {
        return;
    }
    state.parallel = accepted;
    drop(state);
    shared.changed.notify_all();
    global().set_host_limit(&shared.host, accepted);
    log::warn!(
        "{refused_by} is rate limiting; dropping to {accepted} connection{} for this stream",
        if accepted == 1 { "" } else { "s" }
    );
}

fn chunk_length(shared: &Shared, index: u64) -> usize {
    (shared.size - index * CHUNK_BYTES).min(CHUNK_BYTES) as usize
}

fn copy_from(chunk: &[u8], offset: usize, buffer: &mut [u8]) -> usize {
    let available = &chunk[offset.min(chunk.len())..];
    let count = available.len().min(buffer.len());
    buffer[..count].copy_from_slice(&available[..count]);
    count
}

/// Sleeps `duration` in short steps; false when `stop` said to stop.
fn sleep_unless(duration: Duration, stop: &dyn Fn() -> bool) -> bool {
    let deadline = Instant::now() + duration;
    while Instant::now() < deadline {
        if stop() {
            return false;
        }
        thread::sleep(WAIT_STEP.min(deadline - Instant::now()));
    }
    !stop()
}

/// A directory of its own for one stream's chunks.
fn stream_directory(root: &std::path::Path) -> PathBuf {
    static NEXT: OnceLock<std::sync::atomic::AtomicU64> = OnceLock::new();
    let next = NEXT
        .get_or_init(|| std::sync::atomic::AtomicU64::new(0))
        .fetch_add(1, Ordering::Relaxed);
    root.join(format!("{}-{next}", std::process::id()))
}

/// A URL for the log: scheme, host and the start of the path, no tokens.
pub(crate) fn redact(url: &str) -> String {
    let host = http::host_of(url).unwrap_or_default();
    format!("{host}/…")
}

/// When a host's remembered limit expires.
pub(super) fn host_limit_fresh(at: Instant) -> bool {
    at.elapsed() < HOST_LIMIT_TTL
}
