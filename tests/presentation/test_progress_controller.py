from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import PlaybackProgress
from gravitas.presentation.controllers.progress_controller import ProgressController
from gravitas.presentation.models.watched_list_model import WatchedListModel


class _Store:
    def __init__(self, entries: list[PlaybackProgress]) -> None:
        self.entries = entries
        self.deleted: list[tuple[str, str | None]] = []
        self.cleared = False

    def load_all(self) -> list[PlaybackProgress]:
        return list(self.entries)

    def save(self, entry: PlaybackProgress) -> None: ...

    def delete(self, media_id: str, video_id: str | None = None) -> None:
        self.deleted.append((media_id, video_id))

    def clear(self) -> None:
        self.cleared = True


def entry(media_id: str = "tt1", video_id: str = "", **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "M",
        "poster": None,
        "label": "",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def build(entries: list[PlaybackProgress]) -> tuple[ProgressController, _Store, WatchedListModel]:
    store = _Store(entries)
    model = WatchedListModel()
    return ProgressController(WatchProgressRepository(store), model), store, model


def test_has_progress(qapp: object) -> None:
    controller, _, _ = build([entry()])
    assert controller.hasProgress("tt1") is True
    assert controller.hasProgress("tt2") is False


def test_forget_one_video_delegates_and_signals(qapp: object) -> None:
    controller, store, _ = build([entry("tt9", "tt9:1:1", type="series")])
    fired: list[None] = []
    controller.progressChanged.connect(lambda: fired.append(None))
    controller.forget("tt9", "tt9:1:1")
    assert store.deleted == [("tt9", "tt9:1:1")]
    assert fired == [None]


def test_forget_media_delegates(qapp: object) -> None:
    controller, store, _ = build([entry("tt9", "tt9:1:1", type="series")])
    controller.forgetMedia("tt9")
    assert store.deleted == [("tt9", None)]


def test_reset_all_delegates(qapp: object) -> None:
    controller, store, _ = build([entry()])
    controller.resetAll()
    assert store.cleared is True
    assert controller.inProgressCount() == 0


def test_revision_bumps_on_every_mutation(qapp: object) -> None:
    controller, _, _ = build([entry()])
    before = controller.revision
    controller.forgetMedia("tt1")
    assert controller.revision == before + 1


def test_mutations_refresh_the_settings_model(qapp: object) -> None:
    controller, _, model = build([entry()])
    controller.refreshWatched()
    assert model.rowCount() == 1
    controller.forgetMedia("tt1")
    assert model.rowCount() == 0  # the list must not keep a forgotten row


def test_mark_watched_from_a_context_map(qapp: object) -> None:
    controller, _, _ = build([])
    controller.markWatched(
        {
            "mediaId": "tt9",
            "videoId": "tt9:1:1",
            "type": "series",
            "name": "Show",
            "poster": "",
            "label": "S1E1",
        }
    )
    assert controller.isWatched("tt9", "tt9:1:1") is True


def test_mark_watched_without_a_media_id_is_ignored(qapp: object) -> None:
    controller, _, _ = build([])
    controller.markWatched({"mediaId": "", "videoId": "", "type": "movie"})
    assert controller.inProgressCount() == 0
