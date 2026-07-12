# Detail Enrichment + Detail Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich `MetaDetail` with logo/year/runtime/rating/genres/cast/directors, parse them from Cinemeta, expose them through `DetailController` as QML properties, and redesign the Detail page (blurred background, title logo, meta row, chips, directors, sources).

**Architecture:** Additive optional fields on the frozen `MetaDetail` dataclass; a tolerant parser; a `DetailController` refactor from narrow title/description signals to a full notifying-property surface (one `metaChanged` signal); a reusable `AppChip` pill; a redesigned `Detail.qml` binding to those properties.

**Tech Stack:** Python 3.13, PySide6 (Qt6/QML, `Qt5Compat.GraphicalEffects` for blur), `qasync`, `pytest` (+ pytest-qt `qapp`), `ruff`, `mypy --strict`, `uv`.

## Global Constraints

- Clean Architecture dependency rule: `presentation → application → domain ← infrastructure`. `models.py` (domain) imports no framework code; `parsing.py` (infrastructure) imports domain + stdlib; `detail_controller.py` (presentation) imports application + domain + presentation siblings, never infrastructure.
- Gates each commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` (strict, `src` only), `uv run pytest -q`. Test output pristine.
- New `MetaDetail` fields MUST be defaulted (existing 7-arg constructions in `parsing.py`, `tests/application/test_use_cases.py`, `tests/domain/test_models.py` must stay valid).
- Async controller slots use `@qasync.asyncSlot`, never plain `@Slot`.
- Untyped-third-party typing: `Property`/`asyncSlot` decorators use precise `# type: ignore[<code>]` matching what mypy reports (QVariantList `Property` currently reports `[arg-type]`; `asyncSlot` reports `[untyped-decorator]`; use whatever mypy prints if different). For a QVariantList `Property` whose value is also read inside another method, use a private helper method to avoid the intra-class `Property` typing quirk (as `DiscoverController` does).
- QML context properties are null-guarded in bindings (`detailController ? detailController.x : …`), matching `Discover.qml`.
- No Claude attribution in commit messages. All commands via `uv`.

---

### Task 1: Extend `MetaDetail` with enriched fields

**Files:**
- Modify: `src/gravitas/domain/models.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Produces: `MetaDetail(..., logo: str|None=None, year: str|None=None, runtime: str|None=None, imdb_rating: str|None=None, genres: tuple[str,...]=(), cast: tuple[str,...]=(), directors: tuple[str,...]=())`.

- [ ] **Step 1: Write the failing test**

Add to `tests/domain/test_models.py`:

```python
def test_meta_detail_enriched_defaults() -> None:
    meta = MetaDetail(
        id="tt1", type="movie", name="A", description="d",
        poster=None, background=None, videos=(),
    )
    assert meta.logo is None
    assert meta.year is None
    assert meta.runtime is None
    assert meta.imdb_rating is None
    assert meta.genres == ()
    assert meta.cast == ()
    assert meta.directors == ()


def test_meta_detail_enriched_values() -> None:
    meta = MetaDetail(
        id="tt1", type="movie", name="A", description="d",
        poster=None, background=None, videos=(),
        logo="l", year="2026", runtime="102 min", imdb_rating="7.5",
        genres=("Animation", "Comedy"), cast=("Tom Hanks",), directors=("Dir",),
    )
    assert meta.year == "2026"
    assert meta.genres == ("Animation", "Comedy")
    assert meta.directors == ("Dir",)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/domain/test_models.py -q`
Expected: FAIL — `MetaDetail.__init__() got an unexpected keyword argument 'logo'`.

- [ ] **Step 3: Extend the dataclass**

In `src/gravitas/domain/models.py`, replace the `MetaDetail` dataclass with:

```python
@dataclass(frozen=True, slots=True)
class MetaDetail:
    id: str
    type: MediaType
    name: str
    description: str | None
    poster: str | None
    background: str | None
    videos: tuple[Video, ...]
    logo: str | None = None
    year: str | None = None
    runtime: str | None = None
    imdb_rating: str | None = None
    genres: tuple[str, ...] = ()
    cast: tuple[str, ...] = ()
    directors: tuple[str, ...] = ()
```

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/domain/test_models.py -q` → PASS. Then `uv run pytest -q` → green (existing 7-arg constructions still valid).
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` → pass.

```bash
git add src/gravitas/domain/models.py tests/domain/test_models.py
git commit -m "feat(detail): enrich MetaDetail with logo/year/runtime/rating/genres/cast/directors"
```

---

### Task 2: Parse the enriched fields in `parse_meta`

**Files:**
- Modify: `src/gravitas/infrastructure/addons/parsing.py`
- Test: `tests/infrastructure/addons/test_parsing.py`

**Interfaces:**
- Consumes: enriched `MetaDetail` (Task 1).
- Produces: `parse_meta` populating the new fields; helpers `_str_or_none`, `_str_tuple`.

- [ ] **Step 1: Write the failing test**

Add to `tests/infrastructure/addons/test_parsing.py`:

```python
def test_parse_meta_reads_enriched_fields() -> None:
    data = {
        "meta": {
            "id": "tt1",
            "type": "movie",
            "name": "Toy Story 5",
            "description": "desc",
            "logo": "http://l/logo.png",
            "background": "http://b/bg.jpg",
            "releaseInfo": "2026",
            "runtime": "102 min",
            "imdbRating": "7.5",
            "genres": ["Animation", "Comedy", 3],
            "cast": ["Tom Hanks", "Tim Allen"],
            "director": ["Andrew Stanton"],
        }
    }
    m = parse_meta(data)
    assert m.logo == "http://l/logo.png"
    assert m.year == "2026"
    assert m.runtime == "102 min"
    assert m.imdb_rating == "7.5"
    assert m.genres == ("Animation", "Comedy")  # non-string 3 skipped
    assert m.cast == ("Tom Hanks", "Tim Allen")
    assert m.directors == ("Andrew Stanton",)


def test_parse_meta_year_falls_back_to_year_field() -> None:
    data = {"meta": {"id": "tt1", "type": "movie", "name": "A", "year": "1999"}}
    assert parse_meta(data).year == "1999"


def test_parse_meta_missing_enriched_fields_default() -> None:
    data = {"meta": {"id": "tt1", "type": "movie", "name": "A"}}
    m = parse_meta(data)
    assert m.logo is None and m.year is None and m.runtime is None
    assert m.imdb_rating is None and m.genres == () and m.cast == () and m.directors == ()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py -k parse_meta -q`
Expected: FAIL — parsed `MetaDetail` has default `None`/`()` for the new fields (assertions on `logo`/`year`/etc. fail).

- [ ] **Step 3: Add helpers + populate fields**

In `src/gravitas/infrastructure/addons/parsing.py`, add two helpers near the top (after `_require`):

```python
def _str_or_none(v: Any) -> str | None:
    if isinstance(v, (str, int, float)):
        s = str(v)
        return s if s else None
    return None


def _str_tuple(v: Any) -> tuple[str, ...]:
    if isinstance(v, list):
        return tuple(x for x in v if isinstance(x, str))
    return ()
```

Then extend the `return MetaDetail(...)` in `parse_meta` to include the new fields:

```python
    return MetaDetail(
        id=str(meta.get("id", "")),
        type=m_type,
        name=str(meta.get("name", "")),
        description=meta.get("description"),
        poster=meta.get("poster"),
        background=meta.get("background"),
        videos=videos,
        logo=_str_or_none(meta.get("logo")),
        year=_str_or_none(meta.get("releaseInfo")) or _str_or_none(meta.get("year")),
        runtime=_str_or_none(meta.get("runtime")),
        imdb_rating=_str_or_none(meta.get("imdbRating")),
        genres=_str_tuple(meta.get("genres")),
        cast=_str_tuple(meta.get("cast")),
        directors=_str_tuple(meta.get("director")),
    )
```

- [ ] **Step 4: Run tests, gates, commit**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py -q` → PASS. Then `uv run pytest -q` → green.
Run gates → pass.

```bash
git add src/gravitas/infrastructure/addons/parsing.py tests/infrastructure/addons/test_parsing.py
git commit -m "feat(detail): parse enriched meta fields from Cinemeta"
```

---

### Task 3: `DetailController` meta properties + minimal Detail.qml rebind

Refactor the controller from `titleChanged`/`descriptionChanged` signals to a full notifying-property surface, add its first unit test, keep `Detail.qml` working against the new properties, and add a headless `Detail.qml` load test.

**Files:**
- Modify: `src/gravitas/presentation/controllers/detail_controller.py`
- Modify: `src/gravitas/presentation/qml/Detail.qml`
- Test: `tests/presentation/test_detail_controller.py` (new), `tests/test_composition.py` (add `test_detail_qml_loads`)

**Interfaces:**
- Produces: `DetailController` with signal `metaChanged()`; read-only `Property`s `hasMeta` (bool), `title`/`description`/`poster`/`background`/`logo`/`year`/`runtime`/`imdbRating` (str), `genres`/`cast`/`directors` (QVariantList); unchanged `errorOccurred`, `bind_manifest`, `@asyncSlot load(type, item_id)`.

- [ ] **Step 1: Write the failing controller test**

Create `tests/presentation/test_detail_controller.py`:

```python
from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import AddonManifest, MetaDetail, Stream
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.models.stream_list_model import StreamListModel


def _manifest() -> AddonManifest:
    return AddonManifest(
        id="fake", name="F", version="1", resources=("meta", "stream"),
        types=("movie",), catalogs=(), base_url="https://a/",
    )


class FakeGetDetail:
    async def __call__(self, manifest, type, item_id):  # noqa: ANN001, ANN002
        return MetaDetail(
            id=item_id, type="movie", name="Film", description="d",
            poster="p", background="b", videos=(),
            logo="l", year="2026", runtime="102 min", imdb_rating="7.5",
            genres=("Animation", "Comedy"), cast=("Tom Hanks",), directors=("Dir",),
        )


class FakeResolve:
    async def __call__(self, manifest, type, item_id):  # noqa: ANN001
        return [Stream(name="1080p", title="web", url="http://s/v.mkv", info_hash=None, file_idx=None)]


class FailGetDetail:
    async def __call__(self, *a, **k):  # noqa: ANN002, ANN003
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/presentation/test_detail_controller.py -q`
Expected: FAIL — current `DetailController` has no `title`/`metaChanged`/`hasMeta` etc.

- [ ] **Step 3: Rewrite the controller**

Replace `src/gravitas/presentation/controllers/detail_controller.py` with:

```python
"""QObject bridge: load meta + streams for a selected item; expose meta to QML."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.get_detail import GetDetail
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import AddonManifest, MediaType, MetaDetail
from gravitas.presentation.models.stream_list_model import StreamListModel


class DetailController(QObject):
    errorOccurred = Signal(str)
    metaChanged = Signal()

    def __init__(
        self,
        get_detail: GetDetail,
        resolve_stream: ResolveStream,
        stream_model: StreamListModel,
    ) -> None:
        super().__init__()
        self._get_detail = get_detail
        self._resolve_stream = resolve_stream
        self._stream_model = stream_model
        self._manifest: AddonManifest | None = None
        self._meta: MetaDetail | None = None

    def bind_manifest(self, manifest: AddonManifest) -> None:
        self._manifest = manifest

    @Property(bool, notify=metaChanged)
    def hasMeta(self) -> bool:
        return self._meta is not None

    @Property(str, notify=metaChanged)
    def title(self) -> str:
        return self._meta.name if self._meta else ""

    @Property(str, notify=metaChanged)
    def description(self) -> str:
        return (self._meta.description or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def poster(self) -> str:
        return (self._meta.poster or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def background(self) -> str:
        return (self._meta.background or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def logo(self) -> str:
        return (self._meta.logo or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def year(self) -> str:
        return (self._meta.year or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def runtime(self) -> str:
        return (self._meta.runtime or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def imdbRating(self) -> str:
        return (self._meta.imdb_rating or "") if self._meta else ""

    def _genres(self) -> list[str]:
        return list(self._meta.genres) if self._meta else []

    def _cast(self) -> list[str]:
        return list(self._meta.cast) if self._meta else []

    def _directors(self) -> list[str]:
        return list(self._meta.directors) if self._meta else []

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def genres(self) -> list[str]:
        return self._genres()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def cast(self) -> list[str]:
        return self._cast()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def directors(self) -> list[str]:
        return self._directors()

    @asyncSlot(str, str)  # type: ignore[untyped-decorator]
    async def load(self, type: str, item_id: str) -> None:
        if self._manifest is None:
            self.errorOccurred.emit("no addon installed")
            return
        media_type: MediaType = "series" if type == "series" else "movie"
        try:
            self._meta = await self._get_detail(self._manifest, media_type, item_id)
            self.metaChanged.emit()
            streams = await self._resolve_stream(self._manifest, media_type, item_id)
            self._stream_model.set_streams(streams)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
```

- [ ] **Step 4: Rebind `Detail.qml` to the new properties (minimal)**

Replace `src/gravitas/presentation/qml/Detail.qml` with a minimal property-bound version (full redesign is Task 5) so nothing references the removed signals:

```qml
import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: detail
    property string mediaType
    property string mediaId
    signal playUrl(string url)

    onMediaIdChanged: if (mediaId.length) detailController.load(mediaType, mediaId)

    Column {
        anchors.fill: parent; anchors.margins: 24; spacing: 12
        Text {
            text: detailController ? detailController.title : ""
            color: Theme.text; font.pixelSize: 28; font.bold: true
        }
        Text {
            text: detailController ? detailController.description : ""
            color: Theme.textDim; width: parent.width; wrapMode: Text.WordWrap
        }
        Text { text: "Sources"; color: Theme.text; font.pixelSize: 20 }
        ListView {
            width: parent.width; height: 300; spacing: 8
            model: streamModel
            delegate: StreamRow {
                width: ListView.view.width
                name: model.name
                subtitle: model.title
                onClicked: if (model.url) detail.playUrl(model.url)
            }
        }
    }
}
```

- [ ] **Step 5: Add a headless Detail.qml load test**

`Detail.qml` is only instantiated at runtime (pushed). Append to `tests/test_composition.py`:

```python
def test_detail_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()
    stub = QObject()
    engine.rootContext().setContextProperty("detailController", stub)
    engine.rootContext().setContextProperty("streamModel", stub)
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Detail.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Detail.qml failed to load: {component.errorString()}"
```

- [ ] **Step 6: Run tests, gates, commit**

Run: `uv run pytest tests/presentation/test_detail_controller.py tests/test_composition.py -q` → PASS. Then `uv run pytest -q` → green.
Run gates → pass. (If mypy reports a different ignore code than `[arg-type]`/`[untyped-decorator]`, use what it prints.)

```bash
git add src/gravitas/presentation/controllers/detail_controller.py \
        src/gravitas/presentation/qml/Detail.qml \
        tests/presentation/test_detail_controller.py \
        tests/test_composition.py
git commit -m "feat(detail): DetailController exposes enriched meta as QML properties"
```

---

### Task 4: `AppChip` pill component

**Files:**
- Create: `src/gravitas/presentation/qml/components/AppChip.qml`
- Modify: `src/gravitas/presentation/qml/components/qmldir`

**Interfaces:**
- Produces: `AppChip` with `property string text`.

- [ ] **Step 1: Create `AppChip.qml`**

```qml
import QtQuick
import "."

Rectangle {
    id: chip
    property alias text: label.text
    implicitWidth: label.implicitWidth + 28
    implicitHeight: 34
    radius: height / 2
    color: hover.hovered ? Theme.surfaceHover : Theme.surface
    border.width: 1
    border.color: Theme.border
    Behavior on color { ColorAnimation { duration: Theme.durFast } }

    Text {
        id: label
        anchors.centerIn: parent
        color: Theme.text
        font.pixelSize: Theme.fontSmall
    }

    HoverHandler { id: hover }
}
```

- [ ] **Step 2: Add to `qmldir`**

Append:

```
AppChip 1.0 AppChip.qml
```

- [ ] **Step 3: Verify + gates**

Run: `uv run pytest tests/test_composition.py -q` → PASS (qmldir still resolves for every loaded screen).
Run gates → pass.

- [ ] **Step 4: Commit**

```bash
git add src/gravitas/presentation/qml/components/AppChip.qml \
        src/gravitas/presentation/qml/components/qmldir
git commit -m "feat(ui): AppChip pill component"
```

---

### Task 5: Redesign `Detail.qml`

**Files:**
- Modify: `src/gravitas/presentation/qml/Detail.qml`

**Interfaces:**
- Consumes: `detailController` meta properties (Task 3), `AppChip` (Task 4), `streamModel`, existing `StreamRow`; `Qt5Compat.GraphicalEffects` `FastBlur` (already used elsewhere via `OpacityMask`).

- [ ] **Step 1: Rewrite `Detail.qml`**

Replace `src/gravitas/presentation/qml/Detail.qml` with:

```qml
import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects
import "components"

Item {
    id: detail
    property string mediaType
    property string mediaId
    signal playUrl(string url)

    onMediaIdChanged: if (mediaId.length) detailController.load(mediaType, mediaId)

    // blurred background art + dark scrim for readability
    Image {
        id: bgSrc
        anchors.fill: parent
        source: detailController && detailController.background ? detailController.background : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        visible: false
    }
    FastBlur { anchors.fill: parent; source: bgSrc; radius: 64 }
    Rectangle { anchors.fill: parent; color: Qt.rgba(0.078, 0.078, 0.078, 0.86) }

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight + 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: content
            x: 24
            y: 24
            width: parent.width - 48
            spacing: 16

            // title logo art, with a bold-text fallback
            Item {
                width: parent.width
                height: 120
                Image {
                    id: logo
                    anchors.centerIn: parent
                    height: 110
                    fillMode: Image.PreserveAspectFit
                    source: detailController && detailController.logo ? detailController.logo : ""
                    asynchronous: true
                    visible: status === Image.Ready
                }
                Text {
                    anchors.centerIn: parent
                    visible: !logo.visible
                    text: detailController ? detailController.title : ""
                    color: Theme.text
                    font.pixelSize: 32
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            // meta row: runtime · year · rating + IMDb badge
            Row {
                spacing: 24
                Text {
                    visible: text.length > 0
                    text: detailController ? detailController.runtime : ""
                    color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                }
                Text {
                    visible: text.length > 0
                    text: detailController ? detailController.year : ""
                    color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                }
                Row {
                    spacing: 8
                    visible: ratingText.text.length > 0
                    Text {
                        id: ratingText
                        anchors.verticalCenter: parent.verticalCenter
                        text: detailController ? detailController.imdbRating : ""
                        color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: badge.implicitWidth + 12
                        height: 22
                        radius: 4
                        color: "#f5c518"
                        Text {
                            id: badge
                            anchors.centerIn: parent
                            text: "IMDb"
                            color: "#000000"
                            font.pixelSize: Theme.fontSmall
                            font.bold: true
                        }
                    }
                }
            }

            // description
            Text {
                width: parent.width
                text: detailController ? detailController.description : ""
                color: Theme.text
                opacity: 0.9
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontBody
            }

            // genres
            Column {
                width: parent.width
                spacing: 8
                visible: genresRep.count > 0
                Text { text: "GENRES"; color: Theme.textDim; font.pixelSize: Theme.fontSmall; font.bold: true }
                Flow {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        id: genresRep
                        model: detailController ? detailController.genres : []
                        AppChip { text: modelData }
                    }
                }
            }

            // cast
            Column {
                width: parent.width
                spacing: 8
                visible: castRep.count > 0
                Text { text: "CAST"; color: Theme.textDim; font.pixelSize: Theme.fontSmall; font.bold: true }
                Flow {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        id: castRep
                        model: detailController ? detailController.cast : []
                        AppChip { text: modelData }
                    }
                }
            }

            // directors
            Text {
                width: parent.width
                visible: detailController && detailController.directors && detailController.directors.length > 0
                text: (detailController && detailController.directors)
                    ? "Directed by " + detailController.directors.join(", ")
                    : ""
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }

            // sources
            Text { text: "Sources"; color: Theme.text; font.pixelSize: 20 }
            Column {
                width: parent.width
                spacing: 8
                Repeater {
                    model: streamModel
                    StreamRow {
                        width: content.width
                        name: model.name
                        subtitle: model.title
                        onClicked: if (model.url) detail.playUrl(model.url)
                    }
                }
            }
        }
    }
}
```

- [ ] **Step 2: Run the full suite (headless load coverage)**

Run: `uv run pytest -q`
Expected: all green — `test_detail_qml_loads` now loads the redesigned `Detail.qml` (with `AppChip`, `FastBlur`, Repeaters) against stub context props under the offscreen platform, catching any QML syntax/type error.

- [ ] **Step 3: Gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` → pass.

- [ ] **Step 4: Launch and eyeball**

Run: `uv run gravitas`
Expected (needs a display): click a movie poster → the Detail page shows a blurred background, the title logo art (or bold title text if none), a meta row (`102 min   2026   7.5 [IMDb]`), the description, GENRES chips, CAST chips, a "Directed by …" line, and the Sources stream list; clicking a direct stream still plays. A title without a logo falls back to text. Close the app.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/qml/Detail.qml
git commit -m "feat(detail): redesigned Detail page with logo, meta, chips, directors"
```

---

## Self-Review

**Spec coverage:**
- `MetaDetail` enrichment (defaulted fields) → Task 1. ✓
- `parse_meta` tolerant parsing (`releaseInfo`→year with `year` fallback, list fields skip non-strings, missing→defaults) → Task 2. ✓
- `DetailController` full property surface + `metaChanged` + first unit test → Task 3. ✓
- `AppChip` reusable pill → Task 4. ✓
- Detail page redesign (blur bg, logo w/ text fallback, meta row + IMDb badge, description, genres/cast chips, directors, sources) → Task 5. ✓
- Headless coverage for the runtime-only `Detail.qml` (`test_detail_qml_loads`) → Task 3, exercised again in Task 5. ✓
- Null-guarded bindings; `@asyncSlot`; defaulted fields preserve existing constructions → Tasks 1/3. ✓
- Out of scope (Discover sidebar 3B, series UI, TMDb override, extra action icons) — no task introduces them. ✓

**Placeholder scan:** No TBD/TODO. Every code step is complete. The "use whatever mypy prints" notes are explicit fallbacks for untyped-third-party ignore-code variance, not missing content.

**Type/wiring consistency:** `MetaDetail(...)` new field names (`logo`/`year`/`runtime`/`imdb_rating`/`genres`/`cast`/`directors`) consistent Tasks 1/2/3. Controller property names (`title`/`description`/`poster`/`background`/`logo`/`year`/`runtime`/`imdbRating`/`genres`/`cast`/`directors`/`hasMeta`) consistent between Task 3 (definitions + test) and Task 5 (QML bindings). `metaChanged` is the single notify signal across all properties. `AppChip.text` (Task 4) matches the `AppChip { text: modelData }` usage (Task 5). Parser field mapping (`imdbRating`→`imdb_rating`, `director`→`directors`, `releaseInfo`→`year`) consistent between Task 2 code and its test.
