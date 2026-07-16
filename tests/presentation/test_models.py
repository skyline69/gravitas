from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import AddonManifest, MediaItem, PlaybackProgress, Stream, Video
from gravitas.presentation.models.addon_list_model import AddonListModel
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.search_results_model import SearchResultsModel
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


def test_stream_display_parses_structured_tokens(qapp: object) -> None:
    from gravitas.presentation.models.stream_list_model import parse_stream_display

    display = parse_stream_display("4K ⚡ ⟨Web-dl⟩ ★★ MediaFusion", "4K ⚡ ⟨Web-dl⟩ ★★ MediaFusion")
    assert display.resolution == "4K"
    assert display.instant is True
    assert display.tags == ["Web-dl"]
    assert display.stars == 2
    assert display.detail == "MediaFusion"
    assert display.subtitle == ""  # identical title adds nothing

    display = parse_stream_display("Torrentio 1080p", "Movie.2026.WEB · 👤 92 · 2.1 GB")
    assert display.resolution == "1080P"
    assert display.instant is False
    assert display.tags == []
    assert display.subtitle == "Movie.2026.WEB · 👤 92 · 2.1 GB"

    plain = parse_stream_display("My Addon", "")
    assert plain.resolution == ""
    assert plain.detail == "My Addon"


def test_stream_model_display_roles(qapp: object) -> None:
    model = StreamListModel()
    model.set_streams(
        [
            Stream(
                name="4K ⚡ ⟨Bluray⟩ ★",
                title="4K ⚡ ⟨Bluray⟩ ★",
                url="http://s/v.mkv",
                info_hash=None,
                file_idx=None,
            )
        ]
    )
    index = model.index(0, 0)
    assert model.data(index, StreamListModel.ResolutionRole) == "4K"
    assert model.data(index, StreamListModel.InstantRole) is True
    assert model.data(index, StreamListModel.TagsRole) == ["Bluray"]
    assert model.data(index, StreamListModel.StarsRole) == 1
    assert model.data(index, StreamListModel.SubtitleRole) == ""
    names = {bytes(v).decode() for v in model.roleNames().values()}
    assert {"resolution", "instant", "tags", "stars", "extra", "subtitle"} <= names


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
    assert model.data(i0, AddonListModel.VersionRole) == "1"
    i1 = model.index(1, 0)
    assert model.data(i1, AddonListModel.RemovableRole) is True


def test_search_results_model_roles(qapp: object) -> None:
    model = SearchResultsModel()
    model.set_items([MediaItem(id="tt1", type="movie", name="A", poster="p", year="1999")])
    i = model.index(0, 0)
    assert model.data(i, SearchResultsModel.IdRole) == "tt1"
    assert model.data(i, SearchResultsModel.NameRole) == "A"
    assert model.data(i, SearchResultsModel.YearRole) == "1999"
    assert model.data(i, SearchResultsModel.PosterRole) == "p"


class _Store:
    def __init__(self, entries: list[PlaybackProgress]) -> None:
        self.entries = entries

    def load_all(self) -> list[PlaybackProgress]:
        return list(self.entries)

    def save(self, entry: PlaybackProgress) -> None: ...
    def delete(self, media_id: str, video_id: str | None = None) -> None: ...
    def clear(self) -> None: ...


def _entry(media_id: str, video_id: str, **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "N",
        "poster": None,
        "label": "",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def test_episode_model_exposes_progress_roles(qapp: object) -> None:
    repo = WatchProgressRepository(
        _Store(
            [
                _entry("tt9", "tt9:1:1", type="series", position=150.0),
                _entry("tt9", "tt9:1:2", type="series", watched=True, position=0.0),
            ]
        )
    )
    model = EpisodeListModel(repo)
    model.set_media_id("tt9")
    model.set_videos(
        [
            Video(id="tt9:1:1", title="One", season=1, episode=1),
            Video(id="tt9:1:2", title="Two", season=1, episode=2),
            Video(id="tt9:1:3", title="Three", season=1, episode=3),
        ]
    )
    frac = EpisodeListModel.ProgressFractionRole
    watched = EpisodeListModel.WatchedRole
    assert model.data(model.index(0, 0), frac) == 0.25
    assert model.data(model.index(1, 0), watched) is True
    assert model.data(model.index(2, 0), frac) == 0.0  # never started
    names = model.roleNames()
    assert names[frac] == b"progressFraction"
    assert names[watched] == b"watched"


def test_episode_model_without_a_repo_reports_zero(qapp: object) -> None:
    model = EpisodeListModel()
    model.set_videos([Video(id="v1", title="One", season=1, episode=1)])
    assert model.data(model.index(0, 0), EpisodeListModel.ProgressFractionRole) == 0.0


def test_poster_model_movie_reads_its_own_entry(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([_entry("tt1", "")]))
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt1", type="movie", name="M", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressFractionRole) == 0.25


def test_poster_model_series_reads_the_latest_episode(qapp: object) -> None:
    repo = WatchProgressRepository(
        _Store(
            [
                _entry("tt9", "tt9:1:1", type="series", position=60.0, updated_at=100),
                _entry("tt9", "tt9:1:2", type="series", position=300.0, updated_at=200),
            ]
        )
    )
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt9", type="series", name="S", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressFractionRole) == 0.5


def test_poster_model_series_shows_no_bar_once_the_latest_episode_is_watched(
    qapp: object,
) -> None:
    """Finishing S1E1 must not read as the whole show being 100% done — that
    disagreed with Settings, which excludes watched rows from in_progress()
    and would say the same show is NOT in progress. The bar goes to 0 (no
    bar) instead, matching in_progress()'s rule, until a later episode is
    started."""
    repo = WatchProgressRepository(
        _Store(
            [
                _entry("tt9", "tt9:1:1", type="series", watched=True, position=0.0),
            ]
        )
    )
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt9", type="series", name="S", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressFractionRole) == 0.0


def test_poster_model_series_reads_the_in_progress_episode_over_a_watched_one(
    qapp: object,
) -> None:
    repo = WatchProgressRepository(
        _Store(
            [
                _entry(
                    "tt9",
                    "tt9:1:1",
                    type="series",
                    watched=True,
                    position=0.0,
                    updated_at=300,
                ),
                _entry(
                    "tt9",
                    "tt9:1:2",
                    type="series",
                    position=150.0,
                    duration=600.0,
                    updated_at=200,
                ),
            ]
        )
    )
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt9", type="series", name="S", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressFractionRole) == 0.25


def test_poster_model_series_never_reports_watched(qapp: object) -> None:
    repo = WatchProgressRepository(
        _Store(
            [
                _entry("tt9", "tt9:1:1", type="series", watched=True, position=0.0),
            ]
        )
    )
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt9", type="series", name="S", poster=None)])
    # One finished episode does not finish the show.
    assert model.data(model.index(0, 0), PosterGridModel.WatchedRole) is False


def test_refresh_progress_emits_datachanged_for_progress_roles(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([]))
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt1", type="movie", name="M", poster=None)])
    seen: list[list[int]] = []
    model.dataChanged.connect(lambda tl, br, roles: seen.append(list(roles)))
    model.refresh_progress()
    # The label must refresh with the bar — a stale label would name an
    # episode the bar no longer describes.
    assert seen == [
        [
            PosterGridModel.ProgressFractionRole,
            PosterGridModel.WatchedRole,
            PosterGridModel.ProgressLabelRole,
        ]
    ]


def test_refresh_progress_on_empty_model_is_a_noop(qapp: object) -> None:
    model = PosterGridModel(WatchProgressRepository(_Store([])))
    seen: list[object] = []
    model.dataChanged.connect(lambda *a: seen.append(a))
    model.refresh_progress()
    assert seen == []  # index(-1, 0) would be invalid


def test_search_results_model_exposes_progress_roles(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([_entry("tt1", "")]))
    model = SearchResultsModel(repo)
    model.set_items([MediaItem(id="tt1", type="movie", name="M", poster=None)])
    assert model.data(model.index(0, 0), SearchResultsModel.ProgressFractionRole) == 0.25


def test_poster_model_exposes_the_episode_label_for_series(qapp: object) -> None:
    repo = WatchProgressRepository(
        _Store(
            [
                _entry("tt9", "tt9:1:1", type="series", updated_at=100, label="S1E1 · Pilot"),
                _entry("tt9", "tt9:1:2", type="series", updated_at=200, label="S1E2 · Two"),
            ]
        )
    )
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt9", type="series", name="Show", poster=None)])
    index = model.index(0, 0)
    # The label names the episode you would resume — the newest unwatched one.
    assert model.data(index, PosterGridModel.ProgressLabelRole) == "S1E2 · Two"
    assert model.roleNames()[PosterGridModel.ProgressLabelRole] == b"progressLabel"


def test_poster_model_label_is_empty_for_a_movie(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([_entry("tt1", "", label="")]))
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt1", type="movie", name="M", poster=None)])
    # A movie has no episode to name; the card title already says everything.
    assert model.data(model.index(0, 0), PosterGridModel.ProgressLabelRole) == ""


def test_poster_model_label_is_empty_when_nothing_in_progress(qapp: object) -> None:
    model = PosterGridModel(WatchProgressRepository(_Store([])))
    model.set_items([MediaItem(id="tt9", type="series", name="Show", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressLabelRole) == ""


def test_poster_model_label_without_a_repo(qapp: object) -> None:
    model = PosterGridModel()
    model.set_items([MediaItem(id="tt9", type="series", name="Show", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressLabelRole) == ""
