# Home Category Rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Home page's single flat poster grid with Stremio-style horizontal category rows — each with a section title, a "See All" button, and a horizontal strip of posters.

**Architecture:** The application layer already returns per-catalog rows (`BrowseCatalog → list[CatalogRow]`). This plan stops the presentation layer from flattening them: a new `CatalogRowsModel` holds one element per row, each exposing its own reused `PosterGridModel`. Home renders a vertical `ListView` of rows, each a new `CatalogRowStrip.qml` (title + See All + horizontal `ListView` of the existing `PosterCard`).

**Tech Stack:** Python 3.13, PySide6 (Qt6/QML), `qasync`, `pytest` (+ pytest-qt `qapp` fixture, async tests), `ruff`, `mypy --strict`, `uv`.

## Global Constraints

- Clean Architecture dependency rule holds: `presentation → application → domain ← infrastructure`. `CatalogRowsModel` (presentation) may import `application` (`CatalogRow`) and `presentation` siblings; it must not import `infrastructure`.
- Three gates pass on every commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, plus `uv run pytest -q`.
- `mypy --strict` covers `src` only. Untyped third-party (`qasync`) uses precise `# type: ignore[<code>]`, never blanket ignores.
- No new network calls — same data `BrowseCatalog` already fetches.
- Never add Claude attribution to commit messages.
- All commands run through `uv`.

---

### Task 1: Extend `CatalogRow` with catalog identity

Carry `type` + `catalog_id` on each row so step 2's "See All" can target a specific catalog. Fill them from the `CatalogRef` `BrowseCatalog` already iterates.

**Files:**
- Modify: `src/gravitas/application/browse_catalog.py`
- Test: `tests/application/test_use_cases.py` (update `test_browse_catalog_builds_rows`)

**Interfaces:**
- Consumes: `MediaType` from `gravitas.domain.models`; `CatalogRef` fields `type`, `id`, `name` (already used).
- Produces: `CatalogRow(title: str, type: MediaType, catalog_id: str, items: list[MediaItem])`.

- [ ] **Step 1: Update the failing test**

In `tests/application/test_use_cases.py`, replace the body of `test_browse_catalog_builds_rows` with:

```python
async def test_browse_catalog_builds_rows() -> None:
    repo = AddonRepository(FakeSource())
    await repo.install("https://a/manifest.json")
    rows = await BrowseCatalog(repo)()
    assert rows == [
        CatalogRow(
            title="Top",
            type="movie",
            catalog_id="top",
            items=[MediaItem(id="tt1", type="movie", name="A", poster=None)],
        )
    ]
```

(Keep the existing `install` call the test already performs; only the `assert rows == [...]` expectation changes to include `type` and `catalog_id`. If the existing test installs the addon differently, preserve that setup and change only the expected `CatalogRow`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_use_cases.py::test_browse_catalog_builds_rows -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'type'` (dataclass has no such field yet).

- [ ] **Step 3: Extend the dataclass and populate the fields**

In `src/gravitas/application/browse_catalog.py`, add the `MediaType` import and the two fields, and fill them in `__call__`:

```python
"""Use case: build catalog rows across all installed addons."""

from __future__ import annotations

from dataclasses import dataclass

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import MediaItem, MediaType


@dataclass(frozen=True, slots=True)
class CatalogRow:
    title: str
    type: MediaType
    catalog_id: str
    items: list[MediaItem]


class BrowseCatalog:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self) -> list[CatalogRow]:
        rows: list[CatalogRow] = []
        for manifest, ref in self._repo.catalog_refs():
            items = await self._repo.aggregate_catalog(manifest, ref)
            rows.append(
                CatalogRow(
                    title=ref.name,
                    type=ref.type,
                    catalog_id=ref.id,
                    items=items,
                )
            )
        return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/application/test_use_cases.py::test_browse_catalog_builds_rows -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

```bash
git add src/gravitas/application/browse_catalog.py tests/application/test_use_cases.py
git commit -m "feat(catalog): carry type + catalog_id on CatalogRow"
```

---

### Task 2: `CatalogRowsModel` — one element per row, each with its own poster model

**Files:**
- Create: `src/gravitas/presentation/models/catalog_rows_model.py`
- Test: `tests/presentation/test_catalog_rows_model.py`

**Interfaces:**
- Consumes: `CatalogRow` (Task 1); `PosterGridModel` from `gravitas.presentation.models.poster_grid_model` (unchanged), whose `set_items(items)`, `rowCount()`, `NameRole`/`PosterRole` this reuses.
- Produces: `CatalogRowsModel` with `set_rows(rows: list[CatalogRow]) -> None`; role constants `TitleRole`, `TypeRole`, `CatalogIdRole`, `PostersRole`; QML role names `title`, `type`, `catalogId`, `posters`. `PostersRole` returns a `PosterGridModel` instance.

- [ ] **Step 1: Write the failing test**

Create `tests/presentation/test_catalog_rows_model.py`:

```python
from gravitas.application.browse_catalog import CatalogRow
from gravitas.domain.models import MediaItem
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel


def _row(title: str, catalog_id: str, name: str) -> CatalogRow:
    return CatalogRow(
        title=title,
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
    assert {"title", "type", "catalogId", "posters"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/presentation/test_catalog_rows_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gravitas.presentation.models.catalog_rows_model'`.

- [ ] **Step 3: Write the model**

Create `src/gravitas/presentation/models/catalog_rows_model.py`:

```python
"""Qt list model exposing catalog rows, each with its own poster model, to QML."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.application.browse_catalog import CatalogRow
from gravitas.presentation.models.poster_grid_model import PosterGridModel

_ROOT_INDEX = QModelIndex()


class CatalogRowsModel(QAbstractListModel):
    TitleRole = Qt.ItemDataRole.UserRole + 1
    TypeRole = Qt.ItemDataRole.UserRole + 2
    CatalogIdRole = Qt.ItemDataRole.UserRole + 3
    PostersRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self) -> None:
        super().__init__()
        # (title, type, catalog_id, poster_model). Holding the PosterGridModel
        # here keeps a Python reference alive so QML can bind it as an inner
        # ListView model without it being garbage-collected.
        self._rows: list[tuple[str, str, str, PosterGridModel]] = []

    def set_rows(self, rows: list[CatalogRow]) -> None:
        self.beginResetModel()
        built: list[tuple[str, str, str, PosterGridModel]] = []
        for row in rows:
            poster_model = PosterGridModel()
            poster_model.set_items(row.items)
            built.append((row.title, row.type, row.catalog_id, poster_model))
        self._rows = built
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        title, type_, catalog_id, posters = self._rows[index.row()]
        match role:
            case CatalogRowsModel.TitleRole:
                return title
            case CatalogRowsModel.TypeRole:
                return type_
            case CatalogRowsModel.CatalogIdRole:
                return catalog_id
            case CatalogRowsModel.PostersRole:
                return posters
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            CatalogRowsModel.TitleRole: QByteArray(b"title"),
            CatalogRowsModel.TypeRole: QByteArray(b"type"),
            CatalogRowsModel.CatalogIdRole: QByteArray(b"catalogId"),
            CatalogRowsModel.PostersRole: QByteArray(b"posters"),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/presentation/test_catalog_rows_model.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

```bash
git add src/gravitas/presentation/models/catalog_rows_model.py tests/presentation/test_catalog_rows_model.py
git commit -m "feat(ui): CatalogRowsModel exposing per-row poster models"
```

---

### Task 3: Point `CatalogController` at the rows model

**Files:**
- Modify: `src/gravitas/presentation/controllers/catalog_controller.py`
- Test: `tests/presentation/test_catalog_controller.py` (new)

**Interfaces:**
- Consumes: `BrowseCatalog` (awaitable returning `list[CatalogRow]`); `CatalogRowsModel.set_rows` (Task 2).
- Produces: `CatalogController(browse: BrowseCatalog, model: CatalogRowsModel)`; unchanged `load_catalog()`, `refresh` slot, `errorOccurred`/`loadingChanged` signals — so `AddonController` (which calls `catalog.load_catalog()`) needs no change.

- [ ] **Step 1: Write the failing test**

Create `tests/presentation/test_catalog_controller.py`:

```python
from gravitas.application.browse_catalog import CatalogRow
from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import MediaItem
from gravitas.presentation.controllers.catalog_controller import CatalogController
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel


class FakeBrowse:
    async def __call__(self) -> list[CatalogRow]:
        return [
            CatalogRow(
                title="Top",
                type="movie",
                catalog_id="top",
                items=[MediaItem(id="tt1", type="movie", name="A", poster=None)],
            )
        ]


class FailingBrowse:
    async def __call__(self) -> list[CatalogRow]:
        raise AddonUnreachable("boom")


async def test_load_catalog_populates_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    controller = CatalogController(FakeBrowse(), model)  # type: ignore[arg-type]
    loading: list[bool] = []
    controller.loadingChanged.connect(loading.append)

    await controller.load_catalog()

    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, CatalogRowsModel.TitleRole) == "Top"
    assert model.data(index, CatalogRowsModel.CatalogIdRole) == "top"
    assert loading == [True, False]


async def test_load_catalog_emits_error_and_still_clears_loading(qapp: object) -> None:
    model = CatalogRowsModel()
    controller = CatalogController(FailingBrowse(), model)  # type: ignore[arg-type]
    errors: list[str] = []
    loading: list[bool] = []
    controller.errorOccurred.connect(errors.append)
    controller.loadingChanged.connect(loading.append)

    await controller.load_catalog()

    assert errors == ["boom"]
    assert loading == [True, False]
    assert model.rowCount() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/presentation/test_catalog_controller.py -v`
Expected: FAIL — `AttributeError`/`TypeError` because `CatalogController.load_catalog` still calls `set_items` on a `CatalogRowsModel` (which has no such method), or the flatten comprehension runs.

- [ ] **Step 3: Update the controller**

Replace `src/gravitas/presentation/controllers/catalog_controller.py` with:

```python
"""QObject bridge: run BrowseCatalog and populate the catalog rows model."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.domain.errors import GravitasError
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel


class CatalogController(QObject):
    errorOccurred = Signal(str)
    loadingChanged = Signal(bool)

    def __init__(self, browse: BrowseCatalog, model: CatalogRowsModel) -> None:
        super().__init__()
        self._browse = browse
        self._model = model

    async def load_catalog(self) -> None:
        self.loadingChanged.emit(True)
        try:
            rows = await self._browse()
            self._model.set_rows(rows)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
        finally:
            self.loadingChanged.emit(False)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def refresh(self) -> None:
        await self.load_catalog()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/presentation/test_catalog_controller.py tests/presentation/test_addon_controller.py -v`
Expected: all PASS (addon-controller tests still pass — `load_catalog` signature is unchanged for its `FakeCatalogController`).

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

```bash
git add src/gravitas/presentation/controllers/catalog_controller.py tests/presentation/test_catalog_controller.py
git commit -m "feat(ui): CatalogController drives CatalogRowsModel"
```

---

### Task 4: Wire the rows model into the composition root

**Files:**
- Modify: `src/gravitas/main.py`
- Test: `tests/test_composition.py` (update `posterModel` → `catalogRowsModel`)

**Interfaces:**
- Consumes: `CatalogRowsModel` (Task 2); `CatalogController(browse, model)` (Task 3).
- Produces: QML context property `catalogRowsModel`; the `posterModel` context property is removed (only `Home.qml` referenced it — replaced in Task 5).

- [ ] **Step 1: Update the failing composition test**

In `tests/test_composition.py`, change the poster-model assertion line:

```python
        assert ctx.contextProperty("catalogRowsModel") is not None
```

(Replace the existing `assert ctx.contextProperty("posterModel") is not None`. Leave the other five assertions unchanged.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition.py -v`
Expected: FAIL — `catalogRowsModel` context property is `None` (still registered as `posterModel`).

- [ ] **Step 3: Update `main.py`**

Apply these edits in `src/gravitas/main.py`:

1. Swap the model import:

```python
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.stream_list_model import StreamListModel
```

(Remove the `poster_grid_model` import line — `main.py` no longer references `PosterGridModel` directly.)

2. Replace the model construction (was `poster_model = PosterGridModel()`):

```python
    rows_model = CatalogRowsModel()
    stream_model = StreamListModel()

    catalog_controller = CatalogController(BrowseCatalog(repo), rows_model)
```

3. Replace the context-property registration line (was `ctx.setContextProperty("posterModel", poster_model)`):

```python
    ctx.setContextProperty("catalogRowsModel", rows_model)
```

4. Replace `poster_model` in the `engine._gravitas_refs` keep-alive tuple with `rows_model`:

```python
    engine._gravitas_refs = (  # type: ignore[attr-defined]
        catalog_controller,
        detail_controller,
        player_controller,
        addon_controller,
        rows_model,
        stream_model,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_composition.py -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass. (`main.py` importing `CatalogRowsModel` but not `PosterGridModel` must have no unused-import warnings.)

```bash
git add src/gravitas/main.py tests/test_composition.py
git commit -m "feat(ui): register catalogRowsModel in composition root"
```

---

### Task 5: Render category rows in QML

Replace Home's flat `GridView` with a vertical `ListView` of `CatalogRowStrip`s; add the new strip component; add a no-op `onSeeAll` hook in `Main.qml`. Add a `rootObjects` assertion to the composition test so a QML parse error in the new components fails the suite headlessly.

**Files:**
- Create: `src/gravitas/presentation/qml/components/CatalogRowStrip.qml`
- Modify: `src/gravitas/presentation/qml/Home.qml`
- Modify: `src/gravitas/presentation/qml/Main.qml`
- Modify: `tests/test_composition.py` (assert `engine.rootObjects()` non-empty)

**Interfaces:**
- Consumes: context property `catalogRowsModel` (Task 4) with role names `title`, `type`, `catalogId`, `posters`; existing `PosterCard.qml` (`title`, `posterUrl`, `clicked()`); `posters` role names `name`, `poster`, `type`, `id`.
- Produces: `Home` signal `seeAll(string type, string catalogId)` (in addition to existing `openDetail`).

- [ ] **Step 1: Add the QML-load assertion to the composition test**

In `tests/test_composition.py`, immediately after the `_app, engine = build_app(...)` call, add:

```python
        assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
```

- [ ] **Step 2: Run it to confirm current tree still loads**

Run: `uv run pytest tests/test_composition.py -v`
Expected: PASS (Home still references the old `posterModel` at this point only if Task 4 is not yet applied; if Task 4 is applied, `Home.qml` still says `model: posterModel` and QML will warn about an unknown context property but still load — `rootObjects()` stays non-empty). Proceed regardless of warnings; the next steps fix the binding.

- [ ] **Step 3: Create `CatalogRowStrip.qml`**

Create `src/gravitas/presentation/qml/components/CatalogRowStrip.qml`:

```qml
import QtQuick
import QtQuick.Controls

Item {
    id: root
    property string title
    property string type
    property string catalogId
    property var posters
    signal openDetail(string type, string id)
    signal seeAll(string type, string catalogId)

    implicitHeight: header.height + strip.anchors.topMargin + strip.height

    Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 28

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: root.title
            color: "white"
            font.pixelSize: 18
            font.bold: true
        }

        Text {
            id: seeAllLabel
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: "See All"
            color: "#9aa0a6"
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.seeAll(root.type, root.catalogId)
            }
        }
    }

    ListView {
        id: strip
        anchors.top: header.bottom
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.right: parent.right
        height: 280
        orientation: ListView.Horizontal
        spacing: 16
        clip: true
        model: root.posters
        delegate: PosterCard {
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            onClicked: root.openDetail(model.type, model.id)
        }

        // Horizontal ListViews don't scroll on a vertical mouse wheel by
        // default; map wheel delta onto contentX so a trackpad/wheel scrolls
        // the strip sideways.
        WheelHandler {
            acceptedModifiers: Qt.NoModifier
            onWheel: (event) => {
                strip.contentX = Math.max(
                    0,
                    Math.min(
                        strip.contentWidth - strip.width,
                        strip.contentX - event.angleDelta.y
                    )
                )
            }
        }
    }
}
```

- [ ] **Step 4: Rewrite the grid in `Home.qml`**

In `src/gravitas/presentation/qml/Home.qml`: add the `seeAll` signal next to the existing `openDetail`, and replace the whole `GridView { ... }` block with a vertical `ListView`. The `addonBar`, `BusyIndicator`, and imports stay. Result:

```qml
import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    signal openDetail(string type, string id)
    signal seeAll(string type, string catalogId)

    Rectangle {
        id: addonBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 56
        color: "#1c1c1c"

        Row {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            TextField {
                id: urlField
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - addButton.width - parent.spacing
                placeholderText: "Addon manifest URL…"
            }

            Button {
                id: addButton
                anchors.verticalCenter: parent.verticalCenter
                text: "Add"
                onClicked: {
                    addonController.addAddon(urlField.text)
                    urlField.text = ""
                }
            }
        }
    }

    ListView {
        id: rowsView
        anchors.top: addonBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        spacing: 28
        clip: true
        model: catalogRowsModel
        delegate: CatalogRowStrip {
            width: rowsView.width
            title: model.title
            type: model.type
            catalogId: model.catalogId
            posters: model.posters
            onOpenDetail: (t, id) => home.openDetail(t, id)
            onSeeAll: (t, cid) => home.seeAll(t, cid)
        }
    }

    BusyIndicator {
        id: busy
        anchors.centerIn: parent
        running: false
        Connections {
            target: catalogController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
}
```

- [ ] **Step 5: Add the no-op `onSeeAll` hook in `Main.qml`**

In `src/gravitas/presentation/qml/Main.qml`, update the `homePage` component's `Home` so it handles the new signal (step 2 will replace the body with navigation to the Discover board):

```qml
    Component {
        id: homePage
        Home {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onSeeAll: (type, catalogId) => { /* step 2: open Discover board pre-filtered by (type, catalogId) */ }
        }
    }
```

- [ ] **Step 6: Run the full suite (headless QML-load coverage)**

Run: `uv run pytest -q`
Expected: all pass — including `tests/test_composition.py`, whose new `assert engine.rootObjects()` now exercises loading `Main.qml → Home → CatalogRowStrip → PosterCard` under the offscreen platform, catching any QML syntax/type error in the new components.

- [ ] **Step 7: Gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 8: Launch and eyeball the rows**

Run: `uv run gravitas`
Expected (needs a display; system libmpv optional for browsing): the Home page shows stacked category rows — each a bold section title with a "See All" label on the right, above a horizontally scrollable strip of posters. Clicking a poster opens its detail page; clicking "See All" does nothing yet (step 2). Confirm scroll works via drag and wheel/trackpad. Close the app.

- [ ] **Step 9: Commit**

```bash
git add src/gravitas/presentation/qml/components/CatalogRowStrip.qml \
        src/gravitas/presentation/qml/Home.qml \
        src/gravitas/presentation/qml/Main.qml \
        tests/test_composition.py
git commit -m "feat(ui): Stremio-style Home category rows with See All"
```

---

## Self-Review

**Spec coverage:**
- Data layer (`CatalogRow` + `BrowseCatalog` type/catalog_id) → Task 1. ✓
- `CatalogRowsModel` with the four roles + nested `PosterGridModel` → Task 2. ✓
- `CatalogController` swap → Task 3. ✓
- Composition root `catalogRowsModel` + keep-alive → Task 4. ✓
- QML: `Home.qml` vertical ListView, new `CatalogRowStrip.qml`, `PosterCard` reuse, `Main.qml` no-op `onSeeAll`, addon bar + BusyIndicator retained → Task 5. ✓
- See All emits `seeAll(type, catalogId)`, ignored for now → Task 5 (Home signal + Main no-op). ✓
- Error handling unchanged; empty repo → empty list → empty ListView → Task 3 error test + no special-casing. ✓
- Testing: model unit tests (Task 2), controller tests (Task 3), BrowseCatalog test update (Task 1), QML-at-launch + headless load assertion (Task 5), all gates each task. ✓
- Out of scope (filters, genre/pagination, board, sidebar, chrome) — no tasks introduce them. ✓

**Placeholder scan:** No TBD/TODO in executable steps. The single `/* step 2: ... */` is an intentional QML comment marking the deferred hook, not missing plan content. All code steps show complete code.

**Type consistency:** `CatalogRow(title, type, catalog_id, items)` consistent across Tasks 1–3. Role names `title`/`type`/`catalogId`/`posters` consistent between `CatalogRowsModel.roleNames` (Task 2) and QML bindings (Task 5). `set_rows` used identically in Tasks 2–3. `CatalogController(browse, model)` signature consistent Tasks 3–4. `seeAll(string type, string catalogId)` consistent between `Home.qml` and `Main.qml` (Task 5).
