from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import PlaybackProgress


class FakeStore:
    def __init__(self, entries: list[PlaybackProgress] | None = None) -> None:
        self.entries = list(entries or [])
        self.saved: list[PlaybackProgress] = []
        self.deleted: list[tuple[str, str | None]] = []
        self.cleared = False

    def load_all(self) -> list[PlaybackProgress]:
        return list(self.entries)

    def save(self, entry: PlaybackProgress) -> None:
        self.saved.append(entry)

    def delete(self, media_id: str, video_id: str | None = None) -> None:
        self.deleted.append((media_id, video_id))

    def clear(self) -> None:
        self.cleared = True


def entry(media_id: str = "tt1", video_id: str = "", **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "The Movie",
        "poster": None,
        "label": "",
        "position": 120.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def repo(store: FakeStore, now: int = 500) -> WatchProgressRepository:
    return WatchProgressRepository(store, clock=lambda: now)


def test_loads_index_from_store_at_construction() -> None:
    r = repo(FakeStore([entry()]))
    assert r.get("tt1") == entry()
    assert r.fraction_for("tt1") == 0.2


def test_fraction_for_unknown_is_zero() -> None:
    assert repo(FakeStore()).fraction_for("nope") == 0.0


def test_record_below_floor_is_ignored() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1",
        video_id="",
        type="movie",
        name="M",
        poster=None,
        label="",
        position=29.9,
        duration=600.0,
    )
    assert store.saved == []
    assert r.get("tt1") is None


def test_record_at_floor_is_kept() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1",
        video_id="",
        type="movie",
        name="M",
        poster=None,
        label="",
        position=30.0,
        duration=600.0,
    )
    assert len(store.saved) == 1
    assert r.get("tt1") is not None


def test_record_past_ninety_percent_marks_watched_and_zeroes_position() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1",
        video_id="",
        type="movie",
        name="M",
        poster=None,
        label="",
        position=540.0,
        duration=600.0,
    )
    saved = store.saved[0]
    assert saved.watched is True
    assert saved.position == 0.0
    assert r.resume_position("tt1") == 0.0  # a replay starts clean
    assert r.fraction_for("tt1") == 1.0


def test_record_with_unknown_duration_never_marks_watched() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1",
        video_id="",
        type="movie",
        name="M",
        poster=None,
        label="",
        position=100.0,
        duration=0.0,
    )
    assert store.saved[0].watched is False


def test_record_stamps_the_clock() -> None:
    store = FakeStore()
    repo(store, now=777).record(
        media_id="tt1",
        video_id="",
        type="movie",
        name="M",
        poster=None,
        label="",
        position=100.0,
        duration=600.0,
    )
    assert store.saved[0].updated_at == 777


def test_resume_position_returns_saved_position() -> None:
    assert repo(FakeStore([entry(position=300.0)])).resume_position("tt1") == 300.0


def test_latest_for_picks_highest_updated_at() -> None:
    store = FakeStore(
        [
            entry("tt9", "tt9:1:1", type="series", updated_at=100, label="S1E1"),
            entry("tt9", "tt9:1:3", type="series", updated_at=300, label="S1E3"),
            entry("tt9", "tt9:1:2", type="series", updated_at=200, label="S1E2"),
        ]
    )
    latest = repo(store).latest_for("tt9")
    assert latest is not None
    assert latest.label == "S1E3"


def test_latest_unwatched_for_skips_a_just_finished_episode() -> None:
    """latest_for would return the watched episode (fraction 1.0), making the
    whole series read as finished until a later episode is touched.
    latest_unwatched_for is what a series bar reads instead, so it must skip
    watched rows even when they are the most recently touched."""
    store = FakeStore(
        [
            entry("tt9", "tt9:1:1", type="series", updated_at=100, watched=True, position=0.0),
        ]
    )
    r = repo(store)
    assert r.latest_unwatched_for("tt9") is None
    assert r.latest_for("tt9") is not None  # unchanged: still reads the watched row


def test_latest_unwatched_for_returns_the_in_progress_episode() -> None:
    store = FakeStore(
        [
            entry("tt9", "tt9:1:1", type="series", updated_at=300, watched=True, position=0.0),
            entry("tt9", "tt9:1:2", type="series", updated_at=200, position=120.0),
        ]
    )
    r = repo(store)
    latest = r.latest_unwatched_for("tt9")
    assert latest is not None
    assert latest.video_id == "tt9:1:2"  # newest touched among the unwatched ones


def test_latest_unwatched_for_unknown_media_is_none() -> None:
    assert repo(FakeStore()).latest_unwatched_for("nope") is None


def test_record_past_watched_evicts_stale_latest_unwatched_pointer() -> None:
    """record() flipping an episode from unwatched to watched must evict its
    own entry from the _latest_unwatched index, not just leave a stale
    pointer at a row that is now watched."""
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt9",
        video_id="tt9:1:1",
        type="series",
        name="Show",
        poster=None,
        label="S1E1",
        position=60.0,
        duration=600.0,
    )
    assert r.latest_unwatched_for("tt9") is not None
    r.record(
        media_id="tt9",
        video_id="tt9:1:1",
        type="series",
        name="Show",
        poster=None,
        label="S1E1",
        position=540.0,
        duration=600.0,
    )
    assert r.latest_unwatched_for("tt9") is None
    assert r.latest_for("tt9") is not None  # unchanged: still reads the watched row


def test_record_past_watched_falls_back_to_other_unwatched_episode() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt9",
        video_id="tt9:1:1",
        type="series",
        name="Show",
        poster=None,
        label="S1E1",
        position=60.0,
        duration=600.0,
    )
    r.record(
        media_id="tt9",
        video_id="tt9:1:2",
        type="series",
        name="Show",
        poster=None,
        label="S1E2",
        position=60.0,
        duration=600.0,
    )
    # tt9:1:2 is now the latest-touched unwatched episode.
    r.record(
        media_id="tt9",
        video_id="tt9:1:2",
        type="series",
        name="Show",
        poster=None,
        label="S1E2",
        position=540.0,
        duration=600.0,
    )
    latest = r.latest_unwatched_for("tt9")
    assert latest is not None
    assert latest.video_id == "tt9:1:1"


def test_forget_one_episode_rebuilds_latest_unwatched() -> None:
    store = FakeStore(
        [
            entry("tt9", "tt9:1:1", type="series", updated_at=100, label="S1E1"),
            entry("tt9", "tt9:1:3", type="series", updated_at=300, label="S1E3"),
        ]
    )
    r = repo(store)
    r.forget("tt9", "tt9:1:3")
    latest = r.latest_unwatched_for("tt9")
    assert latest is not None
    assert latest.label == "S1E1"  # not a stale pointer at the deleted row


def test_reset_all_clears_latest_unwatched() -> None:
    store = FakeStore([entry("tt9", "tt9:1:1", type="series")])
    r = repo(store)
    r.reset_all()
    assert r.latest_unwatched_for("tt9") is None


def test_forget_one_episode_rebuilds_latest() -> None:
    store = FakeStore(
        [
            entry("tt9", "tt9:1:1", type="series", updated_at=100, label="S1E1"),
            entry("tt9", "tt9:1:3", type="series", updated_at=300, label="S1E3"),
        ]
    )
    r = repo(store)
    r.forget("tt9", "tt9:1:3")
    latest = r.latest_for("tt9")
    assert latest is not None
    assert latest.label == "S1E1"  # not a stale pointer at the deleted row
    assert store.deleted == [("tt9", "tt9:1:3")]


def test_forget_last_episode_drops_latest_entirely() -> None:
    store = FakeStore([entry("tt9", "tt9:1:1", type="series")])
    r = repo(store)
    r.forget("tt9", "tt9:1:1")
    assert r.latest_for("tt9") is None


def test_forget_whole_media_drops_every_episode() -> None:
    store = FakeStore(
        [
            entry("tt9", "tt9:1:1", type="series"),
            entry("tt9", "tt9:1:2", type="series"),
        ]
    )
    r = repo(store)
    r.forget("tt9")
    assert r.latest_for("tt9") is None
    assert r.get("tt9", "tt9:1:1") is None
    assert store.deleted == [("tt9", None)]


def test_in_progress_is_latest_per_media_newest_first_unwatched_only() -> None:
    store = FakeStore(
        [
            entry("tt1", "", updated_at=100),
            entry("tt9", "tt9:1:1", type="series", updated_at=400),
            entry("tt9", "tt9:1:2", type="series", updated_at=500),
            entry("tt5", "", updated_at=900, watched=True),
        ]
    )
    rows = repo(store).in_progress()
    assert [(e.media_id, e.video_id) for e in rows] == [("tt9", "tt9:1:2"), ("tt1", "")]


def test_mark_watched_without_prior_entry() -> None:
    store = FakeStore()
    r = repo(store)
    r.mark_watched(
        media_id="tt9",
        video_id="tt9:1:1",
        type="series",
        name="Show",
        poster=None,
        label="S1E1",
    )
    assert r.is_watched("tt9", "tt9:1:1") is True
    assert store.saved[0].position == 0.0


def test_total_count_includes_watched_rows_unlike_in_progress() -> None:
    store = FakeStore(
        [
            entry("tt1", "", updated_at=100),
            entry("tt2", "", updated_at=200, watched=True),
            entry("tt3", "", updated_at=300, watched=True),
        ]
    )
    r = repo(store)
    assert r.total_count() == 3
    assert len(r.in_progress()) == 1  # watched rows excluded here on purpose


def test_total_count_reflects_forget_and_reset() -> None:
    store = FakeStore([entry("tt1"), entry("tt2", watched=True)])
    r = repo(store)
    assert r.total_count() == 2
    r.forget("tt1")
    assert r.total_count() == 1
    r.reset_all()
    assert r.total_count() == 0


def test_reset_all_empties_index_and_store() -> None:
    store = FakeStore([entry(), entry("tt2")])
    r = repo(store)
    r.reset_all()
    assert r.in_progress() == []
    assert r.get("tt1") is None
    assert store.cleared is True


def test_in_progress_survives_an_older_episode_finishing() -> None:
    # Watch E2 halfway, then finish E1. _latest now points at E1 (watched),
    # but E2 is still half-watched, so the show IS still in progress — the
    # poster bar says so via _latest_unwatched, and this must agree with it.
    store = FakeStore([
        entry("tt9", "tt9:1:2", type="series", position=300.0, updated_at=100, label="S1E2"),
        entry("tt9", "tt9:1:1", type="series", watched=True, position=0.0, updated_at=200,
              label="S1E1"),
    ])
    r = repo(store)
    rows = r.in_progress()
    assert [e.label for e in rows] == ["S1E2"]


def test_in_progress_drops_a_media_once_every_entry_is_watched() -> None:
    store = FakeStore([
        entry("tt9", "tt9:1:1", type="series", watched=True, position=0.0, updated_at=200),
        entry("tt1", "", watched=True, position=0.0, updated_at=100),
    ])
    assert repo(store).in_progress() == []
