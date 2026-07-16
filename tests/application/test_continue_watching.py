from gravitas.application.continue_watching import ContinueWatching
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import PlaybackProgress


class FakeStore:
    def __init__(self, entries: list[PlaybackProgress]) -> None:
        self.entries = entries

    def load_all(self) -> list[PlaybackProgress]:
        return list(self.entries)

    def save(self, entry: PlaybackProgress) -> None: ...
    def delete(self, media_id: str, video_id: str | None = None) -> None: ...
    def clear(self) -> None: ...


def entry(media_id: str, video_id: str = "", **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": media_id.upper(),
        "poster": None,
        "label": "",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def build(entries: list[PlaybackProgress]) -> ContinueWatching:
    return ContinueWatching(WatchProgressRepository(FakeStore(entries)))


def test_returns_in_progress_newest_first() -> None:
    rows = build(
        [
            entry("tt1", updated_at=100),
            entry("tt2", updated_at=300),
            entry("tt3", updated_at=200),
        ]
    )()
    assert [e.media_id for e in rows] == ["tt2", "tt3", "tt1"]


def test_excludes_watched() -> None:
    rows = build([entry("tt1"), entry("tt2", watched=True, position=0.0)])()
    assert [e.media_id for e in rows] == ["tt1"]


def test_empty_when_nothing_in_progress() -> None:
    assert build([])() == []


def test_caps_at_the_default_limit() -> None:
    rows = build([entry(f"tt{i}", updated_at=i) for i in range(30)])()
    assert len(rows) == ContinueWatching.DEFAULT_LIMIT
    # The cap keeps the NEWEST, not an arbitrary 20.
    assert rows[0].media_id == "tt29"


def test_limit_is_overridable() -> None:
    rows = build([entry(f"tt{i}", updated_at=i) for i in range(30)])(limit=3)
    assert [e.media_id for e in rows] == ["tt29", "tt28", "tt27"]
