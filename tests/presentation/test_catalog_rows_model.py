from gravitas.application.browse_catalog import CatalogRow
from gravitas.domain.models import MediaItem
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel


def _row(title: str, catalog_id: str, name: str) -> CatalogRow:
    return CatalogRow(
        title=title,
        addon_id="a",
        type="movie",
        catalog_id=catalog_id,
        items=[MediaItem(id="tt1", type="movie", name=name, poster="http://p/1.jpg")],
    )


def test_set_rows_exposes_roles(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows([_row("Top", "top", "A")])
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, CatalogRowsModel.TitleRole) == "Top"
    assert model.data(index, CatalogRowsModel.AddonIdRole) == "a"
    assert model.data(index, CatalogRowsModel.TypeRole) == "movie"
    assert model.data(index, CatalogRowsModel.CatalogIdRole) == "top"


def test_posters_role_returns_populated_poster_model(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows([_row("Top", "top", "A")])
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert isinstance(posters, PosterGridModel)
    assert posters.rowCount() == 1
    poster_index = posters.index(0, 0)
    assert posters.data(poster_index, PosterGridModel.NameRole) == "A"


def test_set_rows_resets(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows([_row("Top", "top", "A"), _row("New", "new", "B")])
    assert model.rowCount() == 2
    model.set_rows([_row("Only", "only", "C")])
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), CatalogRowsModel.TitleRole) == "Only"


def test_role_names_are_stringified(qapp: object) -> None:
    model = CatalogRowsModel()
    names = {bytes(v).decode() for v in model.roleNames().values()}
    assert {"title", "addonId", "type", "catalogId", "posters"} <= names


def _typed_row(title: str, type_: str, catalog_id: str) -> CatalogRow:
    return CatalogRow(
        title=title,
        addon_id="a",
        type=type_,  # type: ignore[arg-type]
        catalog_id=catalog_id,
        items=[MediaItem(id="tt1", type=type_, name="X", poster=None)],  # type: ignore[arg-type]
    )


_ROWS = [
    _typed_row("Popular Movies", "movie", "top"),
    _typed_row("New Series", "series", "year"),
    _typed_row("Trending Now", "movie", "trending"),
    _typed_row("Documentaries", "series", "docs"),
]


def _titles(model: CatalogRowsModel) -> list[str]:
    return [
        model.data(model.index(i, 0), CatalogRowsModel.TitleRole) for i in range(model.rowCount())
    ]


def test_filter_all_shows_every_row(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    assert model.rowCount() == 4


def test_filter_movie(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("movie")
    assert _titles(model) == ["Popular Movies", "Trending Now"]


def test_filter_series(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("series")
    assert _titles(model) == ["New Series", "Documentaries"]


def test_filter_trending_matches_title_or_catalog_id_case_insensitive(
    qapp: object,
) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("trending")
    # "Popular Movies" (title kw), "Trending Now" (title + catalog_id kw)
    assert _titles(model) == ["Popular Movies", "Trending Now"]


def test_filter_persists_across_set_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_filter("series")
    model.set_rows(_ROWS)
    assert _titles(model) == ["New Series", "Documentaries"]


def _trakt_row(title: str, type_: str) -> object:
    from gravitas.application.trakt_rows import TraktRow

    return TraktRow(
        title=title,
        type=type_,
        items=[MediaItem(id="tt9", type="movie", name="T", poster=None)],
    )


_TRAKT_ROWS = [
    _trakt_row("Recommended Movies", "movie"),
    _trakt_row("Recommended Series", "series"),
    _trakt_row("Recently Watched", ""),
]


def test_trakt_rows_sit_before_catalog_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    assert _titles(model)[:3] == [
        "Recommended Movies",
        "Recommended Series",
        "Recently Watched",
    ]
    assert model.rowCount() == 7
    # No addon or catalog behind them, so See All (keyed on catalogId) hides.
    index = model.index(0, 0)
    assert model.data(index, CatalogRowsModel.AddonIdRole) == ""
    assert model.data(index, CatalogRowsModel.CatalogIdRole) == ""
    assert model.data(index, CatalogRowsModel.ContinueWatchingRole) is False


def test_trakt_rows_follow_the_type_filter(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    model.set_filter("movie")
    # The mixed-type history row ("") shows only under All.
    assert _titles(model) == ["Recommended Movies", "Popular Movies", "Trending Now"]
    model.set_filter("series")
    assert _titles(model) == ["Recommended Series", "New Series", "Documentaries"]


def test_trakt_rows_hidden_under_trending(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    model.set_filter("trending")
    # Personalized rows are not what is popular.
    assert _titles(model) == ["Popular Movies", "Trending Now"]


def test_set_trakt_rows_empty_clears_them(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    model.set_trakt_rows([])
    assert model.rowCount() == 4


def test_trakt_rows_survive_set_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    model.set_rows(_ROWS)
    assert model.rowCount() == 7


def test_refresh_progress_reaches_rows_hidden_by_the_active_filter(qapp: object) -> None:
    """refresh_progress must fan out over _all_rows, not the filtered _rows —
    otherwise switching filters back to a row that was hidden during a
    playback session would show a stale bar until something else forced a
    reset. Movies is the active filter here, so the Series row's poster model
    must still receive dataChanged even though it is currently filtered out."""
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("movie")
    assert _titles(model) == ["Popular Movies", "Trending Now"]

    poster_models = [row[4] for row in model._all_rows]
    assert len(poster_models) == 4
    seen: list[list[tuple[int, list[int]]]] = [[] for _ in poster_models]
    for i, pm in enumerate(poster_models):
        pm.dataChanged.connect(lambda _tl, _br, roles, i=i: seen[i].append((0, list(roles))))

    model.refresh_progress()

    assert all(fired for fired in seen), "every row's poster model must fire dataChanged"
    # "New Series" (index 1) is filtered out under "movie" but must still refresh.
    assert seen[1], "a filtered-out row's poster model was not refreshed"


# --- Continue Watching -------------------------------------------------------
#
# Held apart from the catalog rows on purpose: progress changes every few
# seconds during playback, and rebuilding this row must never re-hit an
# addon's /catalog over the network.

from gravitas.domain.models import PlaybackProgress  # noqa: E402


def _progress(media_id: str, type_: str = "movie", **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": "",
        "type": type_,
        "name": media_id.upper(),
        "poster": "http://p/1.jpg",
        "label": "",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


_CATALOGS = [
    _typed_row("Popular Movies", "movie", "top"),
    _typed_row("New Series", "series", "year"),
]


def test_cw_no_row_when_nothing_is_in_progress(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([])
    assert _titles(model) == ["Popular Movies", "New Series"]


def test_cw_row_is_first(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([_progress("tt1")])
    assert _titles(model) == ["Continue Watching", "Popular Movies", "New Series"]


def test_cw_row_survives_set_rows_arriving_after_it(qapp: object) -> None:
    # Bootstrap order is not guaranteed: progress is local and instant, the
    # catalogs are a network round-trip.
    model = CatalogRowsModel()
    model.set_continue_watching([_progress("tt1")])
    model.set_rows(_CATALOGS)
    assert _titles(model)[0] == "Continue Watching"


def test_cw_row_filters_to_the_active_tab(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([_progress("tt1", "movie"), _progress("tt9", "series")])

    model.set_filter("movie")
    assert _titles(model) == ["Continue Watching", "Popular Movies"]
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert [posters.item_at(i).id for i in range(posters.rowCount())] == ["tt1"]

    model.set_filter("series")
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert [posters.item_at(i).id for i in range(posters.rowCount())] == ["tt9"]


def test_cw_row_absent_when_the_tab_has_no_matching_entries(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([_progress("tt9", "series")])
    model.set_filter("movie")
    # An empty Continue Watching row is worse than no row.
    assert _titles(model) == ["Popular Movies"]


def test_cw_row_absent_from_the_trending_tab(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([_progress("tt1")])
    model.set_filter("trending")
    # Trending is what is hot, not what you personally started.
    assert "Continue Watching" not in _titles(model)


def test_cw_row_renders_straight_from_the_stored_entry(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_continue_watching([_progress("tt9", "series", name="The Show")])
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    built = posters.item_at(0)
    # No network, no catalog lookup — every field is denormalized on the row.
    assert (built.id, built.type, built.name, built.poster) == (
        "tt9",
        "series",
        "The Show",
        "http://p/1.jpg",
    )


def test_cw_row_is_flagged_for_qml(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([_progress("tt1")])
    assert model.data(model.index(0, 0), CatalogRowsModel.ContinueWatchingRole) is True
    assert model.data(model.index(1, 0), CatalogRowsModel.ContinueWatchingRole) is False
    assert model.roleNames()[CatalogRowsModel.ContinueWatchingRole] == b"continueWatching"


def test_cw_row_has_no_addon_or_catalog_behind_it(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_continue_watching([_progress("tt1")])
    index = model.index(0, 0)
    # See All has nowhere to go for this row; QML hides it on the flag above.
    assert model.data(index, CatalogRowsModel.AddonIdRole) == ""
    assert model.data(index, CatalogRowsModel.CatalogIdRole) == ""


def test_cw_rebuild_does_not_disturb_catalog_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    before = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    model.set_continue_watching([_progress("tt1")])
    after = model.data(model.index(1, 0), CatalogRowsModel.PostersRole)
    # Same nested model object: rebuilding this row must never re-fetch a
    # catalog over the network.
    assert before is after


def test_cw_update_replaces_rather_than_appends(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([_progress("tt1")])
    model.set_continue_watching([_progress("tt1"), _progress("tt9", "series")])
    assert _titles(model).count("Continue Watching") == 1
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert posters.rowCount() == 2


def test_cw_update_never_resets_the_model(qapp: object) -> None:
    # The reset that this replaces fired on the 5s playback tick and tore down
    # every catalog delegate mid-incubation -- the source of the QSslSocket
    # "device not open" and "destroyed during incubation" warnings. Each CW
    # transition must instead be a granular insert / change / remove of row 0.
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)

    resets = []
    inserted: list[tuple[int, int]] = []
    removed: list[tuple[int, int]] = []
    changed: list[int] = []
    model.modelAboutToBeReset.connect(lambda: resets.append(1))
    model.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
    model.rowsRemoved.connect(lambda _p, first, last: removed.append((first, last)))
    model.dataChanged.connect(lambda tl, _br, _r=None: changed.append(tl.row()))

    model.set_continue_watching([_progress("tt1")])  # absent -> present
    model.set_continue_watching([_progress("tt9", "series")])  # present -> present
    model.set_continue_watching([])  # present -> absent

    assert resets == []
    assert inserted == [(0, 0)]
    assert removed == [(0, 0)]
    assert changed == [0]


def test_continue_watching_tick_with_same_titles_keeps_the_row_alive(qapp: object) -> None:
    """The 5s playback tick only moves resume positions; the row's poster
    model must survive it untouched (a rebuild re-incubates every card in
    the row, every tick, for no visible change)."""
    model = CatalogRowsModel()
    model.set_continue_watching([_progress("tt1", position=100.0)])
    posters_before = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    model.set_continue_watching([_progress("tt1", position=200.0, updated_at=999)])
    assert model.data(model.index(0, 0), CatalogRowsModel.PostersRole) is posters_before


def test_continue_watching_membership_change_still_rebuilds(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_continue_watching([_progress("tt1")])
    posters_before = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    model.set_continue_watching([_progress("tt1"), _progress("tt2")])
    posters_after = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert posters_after is not posters_before
    assert posters_after.rowCount() == 2
    model.set_continue_watching([])
    assert model.rowCount() == 0


def test_set_trakt_rows_never_resets_and_keeps_catalog_rows_alive(qapp: object) -> None:
    """Trakt rows land after the catalog painted; splicing them in must not
    tear down the catalog rows' delegates (a reset would re-incubate every
    card on the page — the boot spinner hitched on exactly that)."""
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    catalog_posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    resets: list[None] = []
    model.modelAboutToBeReset.connect(lambda: resets.append(None))
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    assert resets == []
    # Same poster model object, now shifted below the Trakt block.
    assert model.data(model.index(3, 0), CatalogRowsModel.PostersRole) is catalog_posters
    model.set_trakt_rows([])  # replace/clear paths are surgical too
    assert resets == []
    assert model.data(model.index(0, 0), CatalogRowsModel.PostersRole) is catalog_posters


def test_set_trakt_rows_respects_continue_watching_offset(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_continue_watching([_progress("tt1")])
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    assert _titles(model)[:4] == [
        "Continue Watching",
        "Recommended Movies",
        "Recommended Series",
        "Recently Watched",
    ]
    model.set_trakt_rows(_TRAKT_ROWS[:1])  # type: ignore[arg-type]
    assert _titles(model)[:2] == ["Continue Watching", "Recommended Movies"]
    assert _titles(model)[2] == "Popular Movies"


def test_set_rows_with_identical_content_skips_the_reset(qapp: object) -> None:
    """The boot's revalidation pass re-derives the same catalog most of the
    time; resetting then would rebuild every delegate to show identical
    content."""
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    posters_before = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    resets: list[None] = []
    model.modelAboutToBeReset.connect(lambda: resets.append(None))
    model.set_rows(list(_ROWS))  # equal content, fresh list object
    assert resets == []
    assert model.data(model.index(0, 0), CatalogRowsModel.PostersRole) is posters_before
    model.set_rows(_ROWS[:2])  # actual change still resets
    assert len(resets) == 1
    assert model.rowCount() == 2


def test_identical_trakt_refresh_is_a_complete_noop(qapp: object) -> None:
    """The revalidation pass usually confirms the boot snapshot; splicing the
    same rows back in re-trickled every poster — the visible 'it loaded
    again' two seconds after startup."""
    model = CatalogRowsModel()
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    ops: list[str] = []
    model.rowsAboutToBeRemoved.connect(lambda *_: ops.append("remove"))
    model.rowsAboutToBeInserted.connect(lambda *_: ops.append("insert"))
    model.set_trakt_rows(list(_TRAKT_ROWS))  # equal content, fresh list
    assert ops == []
    assert model.data(model.index(0, 0), CatalogRowsModel.PostersRole) is posters


def test_trakt_refresh_with_moved_content_updates_strips_in_place(qapp: object) -> None:
    from gravitas.application.trakt_rows import TraktRow

    model = CatalogRowsModel()
    model.set_trakt_rows(_TRAKT_ROWS)  # type: ignore[arg-type]
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    changed = [
        TraktRow(
            title="Recommended Movies",
            type="movie",
            items=[
                MediaItem(id="tt9", type="movie", name="T", poster=None),
                MediaItem(id="tt10", type="movie", name="U", poster=None),
            ],
        ),
        *_TRAKT_ROWS[1:],
    ]
    ops: list[str] = []
    model.rowsAboutToBeRemoved.connect(lambda *_: ops.append("remove"))
    model.set_trakt_rows(changed)  # type: ignore[arg-type]
    assert ops == []
    # Same poster model object, new contents.
    same = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert same is posters
    assert same.rowCount() == 2


def test_set_filter_is_surgical_not_a_reset(qapp: object) -> None:
    """A tab switch was a full model reset: every strip torn down and every
    poster re-incubated, so the whole page flashed skeletons. It must be
    granular removes/inserts instead — rows visible under both filters keep
    their delegates (same poster model objects)."""
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    movie_posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    resets: list[None] = []
    model.modelAboutToBeReset.connect(lambda: resets.append(None))

    model.set_filter("movie")
    assert resets == []
    assert _titles(model) == ["Popular Movies", "Trending Now"]
    assert model.data(model.index(0, 0), CatalogRowsModel.PostersRole) is movie_posters

    model.set_filter("all")
    assert resets == []
    assert _titles(model) == [r.title for r in _ROWS]
    assert model.data(model.index(0, 0), CatalogRowsModel.PostersRole) is movie_posters


def test_set_filter_emits_minimal_ranges(qapp: object) -> None:
    # all -> movie over [movie, series, movie, series]: two removes (the
    # series rows), zero inserts — nothing else may move.
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    inserted: list[tuple[int, int]] = []
    removed: list[tuple[int, int]] = []
    model.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
    model.rowsRemoved.connect(lambda _p, first, last: removed.append((first, last)))
    model.set_filter("movie")
    assert inserted == []
    assert removed == [(3, 3), (1, 1)]


def test_set_filter_same_mode_is_a_noop(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    ops: list[str] = []
    model.modelAboutToBeReset.connect(lambda: ops.append("reset"))
    model.rowsAboutToBeRemoved.connect(lambda *_: ops.append("remove"))
    model.rowsAboutToBeInserted.connect(lambda *_: ops.append("insert"))
    model.set_filter("all")
    assert ops == []


def test_set_filter_keeps_continue_watching_row_when_its_cards_are_unchanged(
    qapp: object,
) -> None:
    """all -> movie with only movie progress: the CW row shows the same cards
    under both filters, so its poster model (and delegate) must survive."""
    model = CatalogRowsModel()
    model.set_rows(_CATALOGS)
    model.set_continue_watching([_progress("tt1", "movie")])
    cw_posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    model.set_filter("movie")
    assert _titles(model) == ["Continue Watching", "Popular Movies"]
    assert model.data(model.index(0, 0), CatalogRowsModel.PostersRole) is cw_posters


def test_catalog_refresh_with_shuffled_items_updates_strips_in_place(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    resets: list[None] = []
    model.modelAboutToBeReset.connect(lambda: resets.append(None))
    shuffled = [
        CatalogRow(
            title="Popular Movies",
            addon_id="a",
            type="movie",
            catalog_id="top",
            items=[
                MediaItem(id="tt2", type="movie", name="Y", poster=None),
                MediaItem(id="tt1", type="movie", name="X", poster=None),
            ],
        ),
        *_ROWS[1:],
    ]
    model.set_rows(shuffled)
    assert resets == []
    same = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert same is posters
    assert same.rowCount() == 2
