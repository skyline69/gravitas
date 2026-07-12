from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import AddonManifest, MetaDetail, Stream
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.models.stream_list_model import StreamListModel


def _manifest() -> AddonManifest:
    return AddonManifest(
        id="fake",
        name="F",
        version="1",
        resources=("meta", "stream"),
        types=("movie",),
        catalogs=(),
        base_url="https://a/",
    )


class FakeGetDetail:
    async def __call__(self, manifest, type, item_id):
        return MetaDetail(
            id=item_id,
            type="movie",
            name="Film",
            description="d",
            poster="p",
            background="b",
            videos=(),
            logo="l",
            year="2026",
            runtime="102 min",
            imdb_rating="7.5",
            genres=("Animation", "Comedy"),
            cast=("Tom Hanks",),
            directors=("Dir",),
        )


class FakeResolve:
    async def __call__(self, manifest, type, item_id):
        return [
            Stream(name="1080p", title="web", url="http://s/v.mkv", info_hash=None, file_idx=None)
        ]


class FailGetDetail:
    async def __call__(self, *a, **k):
        raise AddonUnreachable("boom")


async def test_load_populates_meta(qapp: object) -> None:
    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), FakeResolve(), model)  # type: ignore[arg-type]
    ctl.bind_manifest(_manifest())
    changes: list[int] = []
    ctl.metaChanged.connect(lambda: changes.append(1))

    await ctl.load("movie", "tt1")

    assert ctl.title == "Film"
    assert ctl.description == "d"
    assert ctl.logo == "l"
    assert ctl.background == "b"
    assert ctl.year == "2026"
    assert ctl.runtime == "102 min"
    assert ctl.imdbRating == "7.5"
    assert list(ctl.genres) == ["Animation", "Comedy"]
    assert list(ctl.cast) == ["Tom Hanks"]
    assert list(ctl.directors) == ["Dir"]
    assert ctl.hasMeta is True
    assert model.rowCount() == 1
    assert changes


async def test_load_without_manifest_errors(qapp: object) -> None:
    ctl = DetailController(FakeGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    errors: list[str] = []
    ctl.errorOccurred.connect(errors.append)
    await ctl.load("movie", "tt1")
    assert errors == ["no addon installed"]
    assert ctl.hasMeta is False


async def test_load_error_emits(qapp: object) -> None:
    ctl = DetailController(FailGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    ctl.bind_manifest(_manifest())
    errors: list[str] = []
    ctl.errorOccurred.connect(errors.append)
    await ctl.load("movie", "tt1")
    assert errors == ["boom"]


class FailResolve:
    async def __call__(self, *a, **k):
        raise AddonUnreachable("no streams")


async def test_stale_streams_cleared_when_resolve_fails(qapp: object) -> None:
    # first item resolves streams; second item's meta loads but its stream
    # fetch fails -> the previous item's streams must not linger
    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), FakeResolve(), model)  # type: ignore[arg-type]
    ctl.bind_manifest(_manifest())
    await ctl.load("movie", "tt1")
    assert model.rowCount() == 1

    ctl._resolve_stream = FailResolve()  # type: ignore[assignment]
    errors: list[str] = []
    ctl.errorOccurred.connect(errors.append)
    await ctl.load("movie", "tt2")

    assert errors == ["no streams"]
    assert model.rowCount() == 0
