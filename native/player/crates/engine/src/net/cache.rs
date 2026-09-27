//! One stream's chunks: the aligned grid the engine reads and fetches on,
//! bounded, evicted by distance from the chunk being read.
//!
//! Aligned on purpose: a chunk fetched for one position has to be usable at
//! another, so a seek back into anything already played -- or pulled in by
//! the backfill -- costs nothing. Eviction is asymmetric: `ahead` bytes past
//! the chunk being read (`anchor`) are what the workers read ahead into,
//! `behind` bytes before it are what was played; whichever side is furthest
//! over its own share goes first. Not LRU: going back and going forward are
//! equally likely, and the bytes around the playhead are the ones worth
//! keeping.
//!
//! On disk (one file per chunk) when given a directory, which is what lets
//! it hold gigabytes; in memory otherwise. A disk that fails costs the cache,
//! never the stream: a chunk that cannot be written is simply not kept.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;

/// Where the chunks live.
#[derive(Debug)]
enum Store {
    Memory(HashMap<u64, Arc<[u8]>>),
    Disk(PathBuf),
}

#[derive(Debug)]
pub(crate) struct ChunkCache {
    store: Store,
    sizes: HashMap<u64, usize>,
    total: usize,
    limit: usize,
    ahead: usize,
    behind: usize,
    /// The chunk being read.
    pub(crate) anchor: u64,
    closed: bool,
}

impl ChunkCache {
    /// A cache of at most `limit` bytes, `behind` of them kept behind the
    /// anchor, in `directory` (created here) or in memory.
    pub(crate) fn new(limit: usize, behind: usize, directory: Option<PathBuf>) -> Self {
        let store = match directory {
            Some(directory) if fs::create_dir_all(&directory).is_ok() => Store::Disk(directory),
            Some(directory) => {
                log::warn!(
                    "cannot create the stream cache at {}; keeping it in memory",
                    directory.display()
                );
                Store::Memory(HashMap::new())
            }
            None => Store::Memory(HashMap::new()),
        };
        let behind = behind.clamp(1, limit.max(2) - 1);
        Self {
            store,
            sizes: HashMap::new(),
            total: 0,
            limit,
            ahead: (limit - behind).max(1),
            behind,
            anchor: 0,
            closed: false,
        }
    }

    pub(crate) fn contains(&self, index: u64) -> bool {
        self.sizes.contains_key(&index)
    }

    /// The bytes ahead of the anchor this cache is allowed to hold.
    pub(crate) fn ahead_bytes(&self) -> usize {
        self.ahead
    }

    #[cfg(test)]
    pub(crate) fn total_bytes(&self) -> usize {
        self.total
    }

    pub(crate) fn get(&mut self, index: u64) -> Option<Arc<[u8]>> {
        if !self.sizes.contains_key(&index) {
            return None;
        }
        match &self.store {
            Store::Memory(chunks) => chunks.get(&index).cloned(),
            Store::Disk(directory) => {
                if let Ok(bytes) = fs::read(chunk_path(directory, index)) {
                    Some(bytes.into())
                } else {
                    // Gone underneath us: not cached, so it is fetched again.
                    self.forget(index);
                    None
                }
            }
        }
    }

    pub(crate) fn put(&mut self, index: u64, data: Arc<[u8]>) {
        if self.closed || self.sizes.contains_key(&index) {
            return;
        }
        let size = data.len();
        match &mut self.store {
            Store::Memory(chunks) => {
                chunks.insert(index, data);
            }
            Store::Disk(directory) => {
                if fs::write(chunk_path(directory, index), &data).is_err() {
                    return;
                }
            }
        }
        self.sizes.insert(index, size);
        self.total += size;
        self.evict();
    }

    /// Drops everything, and the directory with it. Later puts are ignored:
    /// a worker still finishing may land one more chunk.
    pub(crate) fn close(&mut self) {
        self.closed = true;
        self.sizes.clear();
        self.total = 0;
        match &mut self.store {
            Store::Memory(chunks) => chunks.clear(),
            Store::Disk(directory) => {
                let _ = fs::remove_dir_all(directory);
            }
        }
    }

    /// How far past its side's share a chunk sits, as a fraction of that
    /// share, so the two sides compare fairly at unequal shares.
    fn overshoot(&self, index: u64, chunk: usize) -> f64 {
        if index >= self.anchor {
            ((index - self.anchor) as usize * chunk) as f64 / self.ahead as f64
        } else {
            ((self.anchor - index) as usize * chunk) as f64 / self.behind as f64
        }
    }

    fn forget(&mut self, index: u64) {
        if let Some(size) = self.sizes.remove(&index) {
            self.total -= size;
        }
        match &mut self.store {
            Store::Memory(chunks) => {
                chunks.remove(&index);
            }
            Store::Disk(directory) => {
                let _ = fs::remove_file(chunk_path(directory, index));
            }
        }
    }

    fn evict(&mut self) {
        while self.total > self.limit && self.sizes.len() > 1 {
            let chunk = self.sizes.values().copied().max().unwrap_or(1);
            let Some(victim) = self.sizes.keys().copied().max_by(|a, b| {
                self.overshoot(*a, chunk)
                    .total_cmp(&self.overshoot(*b, chunk))
            }) else {
                return;
            };
            // Never the chunk being read: that would be a refetch loop.
            if victim == self.anchor {
                return;
            }
            self.forget(victim);
        }
    }
}

impl Drop for ChunkCache {
    fn drop(&mut self) {
        self.close();
    }
}

fn chunk_path(directory: &std::path::Path, index: u64) -> PathBuf {
    directory.join(format!("{index}.chunk"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(byte: u8) -> Arc<[u8]> {
        vec![byte; 10].into()
    }

    #[test]
    fn chunks_come_back_as_they_went_in() {
        let mut cache = ChunkCache::new(100, 50, None);
        cache.put(3, chunk(7));
        assert!(cache.contains(3));
        assert_eq!(&*cache.get(3).unwrap(), &[7; 10]);
        assert!(cache.get(4).is_none());
    }

    #[test]
    fn the_furthest_side_over_its_share_goes_first() {
        // 60 bytes: 40 ahead, 20 behind.
        let mut cache = ChunkCache::new(60, 20, None);
        cache.anchor = 10;
        for index in [7, 8, 9, 10, 11, 12] {
            cache.put(index, chunk(0));
        }
        cache.put(13, chunk(0));
        // Behind: 7 is 30 bytes back on a 20-byte share -- the worst.
        assert!(!cache.contains(7));
        assert!(cache.contains(13));
        assert_eq!(cache.total_bytes(), 60);
    }

    #[test]
    fn the_chunk_being_read_is_never_evicted() {
        let mut cache = ChunkCache::new(10, 5, None);
        cache.anchor = 5;
        cache.put(5, chunk(1));
        cache.put(6, chunk(2));
        assert!(cache.contains(5));
    }

    #[test]
    fn a_disk_cache_removes_its_directory() {
        let directory =
            std::env::temp_dir().join(format!("gravitas-chunk-cache-test-{}", std::process::id()));
        let mut cache = ChunkCache::new(100, 50, Some(directory.clone()));
        cache.put(0, chunk(9));
        assert!(directory.join("0.chunk").is_file());
        assert_eq!(&*cache.get(0).unwrap(), &[9; 10]);
        drop(cache);
        assert!(!directory.exists());
    }
}
