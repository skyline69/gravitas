"""Tab switching under Qt's own model checker.

The user-visible failure this guards against is a Movies tab that renders
completely empty and comes back only after switching away and returning: the
model held the rows all along, but the remove/insert ranges it announced did
not match what it actually did, so the view's idea of the list diverged from
the model's. set_filter morphs the visible list with difflib opcodes rather
than resetting (a reset re-incubates every strip, which is what made switching
flash), and that surgery is exactly the kind of code where an off-by-one
survives every count-based assertion.

QAbstractItemModelTester subscribes to every signal and fails on the spot when
an announced range doesn't match the model's own answers, so it catches the
divergence rather than the symptom.
"""

from __future__ import annotations

import random

from PySide6.QtTest import QAbstractItemModelTester

from gravitas.application.browse_catalog import CatalogRow
from gravitas.application.trakt_rows import TraktRow
from gravitas.domain.models import MediaItem, PlaybackProgress
from gravitas.presentation.models.catalog_rows_model import (
    CONTINUE_WATCHING_TITLE,
    CatalogRowsModel,
)

MODES = ("all", "movie", "series", "trending")


def _item(name: str, type_: str) -> MediaItem:
    return MediaItem(id=f"tt-{name}", type=type_, name=name, poster=None)  # type: ignore[arg-type]


def _catalog_row(title: str, type_: str, catalog_id: str) -> CatalogRow:
    return CatalogRow(
        title=title,
        addon_id="a",
        type=type_,  # type: ignore[arg-type]
        catalog_id=catalog_id,
        items=[_item(f"{title}-1", type_), _item(f"{title}-2", type_)],
    )


def _trakt_row(title: str, type_: str) -> TraktRow:
    return TraktRow(
        title=title,
        type=type_,  # type: ignore[arg-type]
        items=[_item(f"{title}-1", type_)],
    )


def _progress(name: str, type_: str) -> PlaybackProgress:
    return PlaybackProgress(
        media_id=f"tt-{name}",
        video_id="",
        type=type_,  # type: ignore[arg-type]
        name=name,
        poster=None,
        position=60.0,
        duration=6000.0,
        updated_at=1.0,
        label="",
        watched=False,
    )


def _rows() -> list[CatalogRow]:
    return [
        _catalog_row("Top Movies", "movie", "top"),
        _catalog_row("New Movies", "movie", "new"),
        _catalog_row("Top Series", "series", "top"),
        _catalog_row("New Series", "series", "new"),
    ]


def _titles(model: CatalogRowsModel) -> list[str]:
    return [
        model.data(model.index(i, 0), CatalogRowsModel.TitleRole) for i in range(model.rowCount())
    ]


def _expected_titles(mode: str, *, with_cw: bool, with_trakt: bool) -> list[str]:
    """What the tabs mean, restated independently of the model's own code:
    Movies/Series filter by row type, Trending picks rows whose title or
    catalog id reads top/trending/popular (and drops the personalised Trakt
    rows), All shows everything."""
    catalog = [
        ("Top Movies", "movie"),
        ("New Movies", "movie"),
        ("Top Series", "series"),
        ("New Series", "series"),
    ]
    trakt = [("Trakt Movies", "movie"), ("Trakt Series", "series")]
    titles = [CONTINUE_WATCHING_TITLE] if with_cw else []
    if with_trakt and mode != "trending":
        titles += [t for t, type_ in trakt if mode in ("all", type_)]
    if mode == "trending":
        titles += [t for t, _ in catalog if "top" in t.lower()]
    else:
        titles += [t for t, type_ in catalog if mode in ("all", type_)]
    return titles


def test_every_switch_announces_ranges_that_match_the_model(qapp: object) -> None:
    """Walk every ordered pair of tabs; the tester fails on any mismatch."""
    model = CatalogRowsModel()
    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model.set_rows(_rows())

    for first in MODES:
        for second in MODES:
            model.set_filter(first)
            model.set_filter(second)
            assert _titles(model) == _expected_titles(second, with_cw=False, with_trakt=False)


def test_switching_with_trakt_and_continue_watching_rows_present(qapp: object) -> None:
    """The visible list is CW + Trakt + catalog, three blocks with different
    lifetimes; the diff has to keep them in that order through every switch."""
    model = CatalogRowsModel()
    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model.set_rows(_rows())
    model.set_trakt_rows(
        [_trakt_row("Trakt Movies", "movie"), _trakt_row("Trakt Series", "series")]
    )
    model.set_continue_watching([_progress("Watching", "movie")])

    for mode in MODES * 3:
        model.set_filter(mode)
        titles = _titles(model)
        assert titles == sorted(set(titles), key=titles.index)  # no duplicated rows
        if mode == "all":
            assert titles[0] == CONTINUE_WATCHING_TITLE
            assert len(titles) == 7


def test_a_long_random_walk_never_diverges(qapp: object) -> None:
    """Users flick between tabs faster than any content lands. Interleave the
    switches with the updates that arrive on their own clocks — a Trakt answer,
    a playback tick, a catalog refresh — and check the model survives all of
    it. Seeded, so a failure is reproducible."""
    rng = random.Random(20260722)
    model = CatalogRowsModel()
    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model.set_rows(_rows())

    for _ in range(300):
        choice = rng.random()
        if choice < 0.6:
            model.set_filter(rng.choice(MODES))
        elif choice < 0.7:
            model.set_rows(_rows())
        elif choice < 0.8:
            model.set_trakt_rows(
                [_trakt_row("Trakt Movies", "movie")] if rng.random() < 0.5 else []
            )
        elif choice < 0.9:
            model.set_continue_watching(
                [_progress("Watching", rng.choice(("movie", "series")))]
                if rng.random() < 0.5
                else []
            )
        else:
            model.refresh_progress()
        # Whatever happened, the model's own answers stay self-consistent.
        assert model.rowCount() == len(_titles(model))


def test_the_visible_rows_are_never_empty_while_matching_rows_exist(qapp: object) -> None:
    """The reported bug: Movies renders empty though movie rows are loaded."""
    model = CatalogRowsModel()
    QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model.set_rows(_rows())

    for mode in ("movie", "series", "movie", "all", "movie", "series", "series", "movie"):
        model.set_filter(mode)
        if mode in ("movie", "series"):
            assert model.rowCount() == 2, f"{mode} tab went empty"
