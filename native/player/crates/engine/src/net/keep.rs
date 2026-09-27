//! A file's opening bytes, kept across sessions.
//!
//! Opening a Matroska file over the network is a handful of ranged reads one
//! after another -- the header at the start, the index near the end, back to
//! the start -- and each costs a round trip to a debrid CDN: 2.7 s of the
//! first picture on a real TorBox stream, measured. Resuming the same file
//! later reads exactly the same bytes again, through a new signed URL that
//! says nothing about it being the same file. So the chunks the demuxer read
//! while opening are kept on disk, keyed by the file itself -- its size and
//! a hash of its first 64 KiB, which the reader has in hand one round trip
//! in -- and handed to the next open of that file before it asks for them.
//!
//! Bounded per file and in all, oldest dropped first. A disk that fails costs
//! the head start, never the stream.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::SystemTime;

/// How much of the file's start is hashed into its key.
pub(crate) const KEY_BYTES: usize = 64 * 1024;
/// At most this much is kept per file: a header, an index, and fonts a
/// Matroska file attaches for its subtitles; not a scene.
pub(crate) const MAX_FILE_BYTES: usize = 32 * 1024 * 1024;
/// At most this much in all, the least recently used files dropped first.
const MAX_TOTAL_BYTES: u64 = 512 * 1024 * 1024;

/// A file's key: its size and a 64-bit FNV-1a of its first bytes, as hex.
/// FNV rather than the standard hasher, whose output may change between Rust
/// releases and would orphan every kept file.
pub(crate) fn key(size: u64, head: &[u8]) -> String {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for &byte in size
        .to_le_bytes()
        .iter()
        .chain(&head[..head.len().min(KEY_BYTES)])
    {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(0x0100_0000_01b3);
    }
    format!("{size:x}-{hash:016x}")
}

/// Whether `head` is enough of the file to key it by.
pub(crate) fn keyable(head: &[u8], size: u64) -> bool {
    head.len() >= KEY_BYTES || head.len() as u64 == size
}

/// The chunks kept for `key`, by index; empty when none are.
pub(crate) fn load(root: &Path, key: &str) -> Vec<(u64, Arc<[u8]>)> {
    let directory = root.join(key);
    let Ok(entries) = fs::read_dir(&directory) else {
        return Vec::new();
    };
    let chunks: Vec<(u64, Arc<[u8]>)> = entries
        .flatten()
        .filter_map(|entry| {
            let index = entry.file_name().to_str()?.parse::<u64>().ok()?;
            let data = fs::read(entry.path()).ok()?;
            Some((index, Arc::from(data)))
        })
        .collect();
    // Used now: the pruning below keeps what is used.
    let _ = fs::File::open(&directory).and_then(|d| d.set_modified(SystemTime::now()));
    chunks
}

/// Keeps `chunks` for `key`, replacing nothing already kept, and prunes the
/// store to its budget.
pub(crate) fn save(root: &Path, key: &str, chunks: &[(u64, Arc<[u8]>)]) {
    let directory = root.join(key);
    if directory.exists() || chunks.is_empty() {
        return;
    }
    // Written aside and moved in whole: a half-written set must never be
    // found and handed to a demuxer.
    let staging = root.join(format!(".{key}.partial"));
    let _ = fs::remove_dir_all(&staging);
    let written = fs::create_dir_all(&staging).is_ok()
        && chunks
            .iter()
            .all(|(index, data)| fs::write(staging.join(index.to_string()), data).is_ok());
    if !written || fs::rename(&staging, &directory).is_err() {
        let _ = fs::remove_dir_all(&staging);
        return;
    }
    prune(root);
}

fn size_of(directory: &Path) -> u64 {
    fs::read_dir(directory).map_or(0, |entries| {
        entries
            .flatten()
            .filter_map(|e| e.metadata().ok())
            .map(|m| m.len())
            .sum()
    })
}

/// Drops the least recently used files until the store is within budget.
fn prune(root: &Path) {
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    let mut kept: Vec<(SystemTime, PathBuf, u64)> = entries
        .flatten()
        .filter(|e| !e.file_name().to_string_lossy().starts_with('.'))
        .filter_map(|e| {
            let used = e.metadata().ok()?.modified().ok()?;
            let path = e.path();
            let bytes = size_of(&path);
            Some((used, path, bytes))
        })
        .collect();
    let mut total: u64 = kept.iter().map(|(_, _, bytes)| bytes).sum();
    kept.sort_by_key(|(used, _, _)| *used);
    for (_, path, bytes) in kept {
        if total <= MAX_TOTAL_BYTES {
            break;
        }
        let _ = fs::remove_dir_all(&path);
        total = total.saturating_sub(bytes);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(byte: u8, len: usize) -> Arc<[u8]> {
        Arc::from(vec![byte; len])
    }

    #[test]
    fn a_file_is_known_by_its_size_and_start() {
        let head = vec![7u8; KEY_BYTES];
        assert_eq!(key(100, &head), key(100, &head));
        assert_ne!(key(100, &head), key(101, &head));
        let mut other = head.clone();
        other[10] = 8;
        assert_ne!(key(100, &head), key(100, &other));
        // Only the first 64 KiB count: the rest is not in hand yet.
        let mut longer = head.clone();
        longer.push(1);
        assert_eq!(key(100, &head), key(100, &longer));
    }

    #[test]
    fn kept_chunks_come_back_for_the_same_file_only() {
        let root = std::env::temp_dir().join(format!("gravitas-keep-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        save(&root, "a", &[(0, chunk(1, 10)), (42, chunk(2, 20))]);
        let mut back = load(&root, "a");
        back.sort_by_key(|(index, _)| *index);
        assert_eq!(back.len(), 2);
        assert_eq!((back[1].0, back[1].1.len()), (42, 20));
        assert!(load(&root, "b").is_empty());
        // A second save for the same file changes nothing.
        save(&root, "a", &[(7, chunk(3, 5))]);
        assert_eq!(load(&root, "a").len(), 2);
        let _ = fs::remove_dir_all(&root);
    }
}
