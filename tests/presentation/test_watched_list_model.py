from gravitas.domain.models import PlaybackProgress
from gravitas.presentation.models.watched_list_model import WatchedListModel


def entry(**kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": "tt9",
        "video_id": "tt9:1:2",
        "type": "series",
        "name": "The Show",
        "poster": "http://p/9.jpg",
        "label": "S1E2 · Two",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def test_rows_and_roles(qapp: object) -> None:
    model = WatchedListModel()
    model.set_entries([entry()])
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, WatchedListModel.MediaIdRole) == "tt9"
    assert model.data(index, WatchedListModel.VideoIdRole) == "tt9:1:2"
    assert model.data(index, WatchedListModel.TypeRole) == "series"
    assert model.data(index, WatchedListModel.NameRole) == "The Show"
    assert model.data(index, WatchedListModel.PosterRole) == "http://p/9.jpg"
    assert model.data(index, WatchedListModel.LabelRole) == "S1E2 · Two"
    assert model.data(index, WatchedListModel.ProgressFractionRole) == 0.25


def test_role_names(qapp: object) -> None:
    names = WatchedListModel().roleNames()
    assert names[WatchedListModel.MediaIdRole] == b"mediaId"
    assert names[WatchedListModel.ProgressFractionRole] == b"progressFraction"


def test_missing_poster_is_empty_string(qapp: object) -> None:
    model = WatchedListModel()
    model.set_entries([entry(poster=None)])
    assert model.data(model.index(0, 0), WatchedListModel.PosterRole) == ""


def test_set_entries_replaces(qapp: object) -> None:
    model = WatchedListModel()
    model.set_entries([entry()])
    model.set_entries([])
    assert model.rowCount() == 0
