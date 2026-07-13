from gravitas.domain.models import AddonManifest, MediaItem, Stream
from gravitas.presentation.models.addon_list_model import AddonListModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.stream_list_model import StreamListModel


def test_poster_model_exposes_rows_and_roles(qapp: object) -> None:
    model = PosterGridModel()
    model.set_items([MediaItem(id="tt1", type="movie", name="Film", poster="http://p/1.jpg")])
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, PosterGridModel.NameRole) == "Film"
    assert model.data(index, PosterGridModel.PosterRole) == "http://p/1.jpg"
    assert model.item_at(0).id == "tt1"


def test_poster_model_role_names_are_stringified(qapp: object) -> None:
    model = PosterGridModel()
    names = {bytes(v).decode() for v in model.roleNames().values()}
    assert {"id", "type", "name", "poster"} <= names


def test_poster_model_append_items(qapp: object) -> None:
    model = PosterGridModel()
    model.set_items([MediaItem(id="tt1", type="movie", name="A", poster=None)])
    model.append_items([MediaItem(id="tt2", type="movie", name="B", poster=None)])
    assert model.rowCount() == 2
    assert model.item_at(1).id == "tt2"
    model.append_items([])  # no-op
    assert model.rowCount() == 2


def test_stream_model(qapp: object) -> None:
    model = StreamListModel()
    model.set_streams(
        [
            Stream(name="1080p", title="web", url="http://s/v.mkv", info_hash=None, file_idx=None),
        ]
    )
    index = model.index(0, 0)
    assert model.data(index, StreamListModel.NameRole) == "1080p"
    assert model.stream_at(0).url == "http://s/v.mkv"


def _manifest(id_: str, name: str) -> AddonManifest:
    return AddonManifest(
        id=id_,
        name=name,
        version="1",
        resources=("catalog",),
        types=("movie",),
        catalogs=(),
        base_url="https://x/",
    )


def test_addon_list_model_exposes_rows_and_removable(qapp: object) -> None:
    model = AddonListModel()
    model.set_addons(
        [_manifest("cinemeta", "Cinemeta"), _manifest("other", "Other")],
        {"cinemeta"},
    )
    assert model.rowCount() == 2
    i0 = model.index(0, 0)
    assert model.data(i0, AddonListModel.NameRole) == "Cinemeta"
    assert model.data(i0, AddonListModel.IdRole) == "cinemeta"
    assert model.data(i0, AddonListModel.RemovableRole) is False
    i1 = model.index(1, 0)
    assert model.data(i1, AddonListModel.RemovableRole) is True
