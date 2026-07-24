# Top Navigation Bar + Settings Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Home addon-URL strip with a persistent app nav bar (All/Movies/Series/Trending tabs + Settings gear) and add a Settings page for addon management and About.

**Architecture:** Tab filtering runs in the Python `CatalogRowsModel` (unit-tested), driven by a plain `@Slot` on `CatalogController`. Nav chrome (`TopBar.qml`) wraps the `StackView` in `Main.qml`. Settings gains a use case (`UninstallAddon`), a repo capability (`uninstall` + protected-addon tracking), a list model (`AddonListModel`), and a `SettingsController`.

**Tech Stack:** Python 3, PySide6 (Qt6/QML), qasync, pytest. Clean Architecture (`presentation → application → domain ← infrastructure`).

## Global Constraints

- Quality gates must pass on every commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.
- Tests run with `uv run pytest -q`. Qt tests get `QT_QPA_PLATFORM=offscreen` from `tests/conftest.py`; async tests take a `qapp: object` fixture and are `async def`.
- Dependency rule: `application` must not import `infrastructure` or `presentation`; `domain` imports nothing from other layers.
- Async controller slots that do I/O use `@qasync.asyncSlot`, never `@Slot`. Pure/no-I/O slots use `@Slot`.
- `mypy --strict` on `src`; `qasync`/`python-mpv` untyped — use precise `# type: ignore[code]`, never blanket.
- Every domain failure subclasses `GravitasError`.
- Tab modes are exactly the strings `"all"`, `"movie"`, `"series"`, `"trending"`.
- Trending keywords (case-insensitive): `top`, `trending`, `popular`.
- Phosphor glyph codepoints (this font uses standard Phosphor web values): gear `0xe270`, trash `0xe4a6`. Existing `x` `0xe4f6`.

---

### Task 1: Repository uninstall + protected tracking

**Files:**
- Modify: `src/gravitas/domain/errors.py`
- Modify: `src/gravitas/application/addon_repository.py`
- Test: `tests/application/test_addon_repository.py`

**Interfaces:**
- Consumes: existing `AddonRepository(source)`, `AddonManifest`.
- Produces:
  - `class AddonRemovalError(GravitasError)` in `errors.py`.
  - `AddonRepository.install(url: str, *, protected: bool = False) -> AddonManifest`
  - `AddonRepository.uninstall(addon_id: str) -> None` (raises `AddonRemovalError` if protected or absent)
  - `AddonRepository.is_protected(addon_id: str) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/application/test_addon_repository.py`:

```python
import pytest

from gravitas.domain.errors import AddonRemovalError


async def test_uninstall_removes_manifest() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    repo.uninstall(manifest.id)
    assert repo.installed() == []


async def test_uninstall_protected_raises() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/", protected=True)
    assert repo.is_protected(manifest.id) is True
    with pytest.raises(AddonRemovalError):
        repo.uninstall(manifest.id)
    assert repo.installed() != []


async def test_uninstall_absent_raises() -> None:
    repo = AddonRepository(FakeSource())
    with pytest.raises(AddonRemovalError):
        repo.uninstall("nope")


async def test_install_default_not_protected() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    assert repo.is_protected(manifest.id) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/application/test_addon_repository.py -q`
Expected: FAIL — `ImportError: cannot import name 'AddonRemovalError'`.

- [ ] **Step 3: Add the error class**

In `src/gravitas/domain/errors.py`, after `NoStreams`:

```python
class AddonRemovalError(GravitasError):
    """An addon could not be removed (protected default, or not installed)."""
```

- [ ] **Step 4: Implement repo changes**

In `src/gravitas/application/addon_repository.py`, add the import:

```python
from gravitas.domain.errors import AddonRemovalError, AddonUnreachable, GravitasError
```

In `__init__`, add after `self._manifests`:

```python
        self._protected: set[str] = set()
```

Replace the existing `install` method with:

```python
    async def install(self, url: str, *, protected: bool = False) -> AddonManifest:
        manifest = await self._source.fetch_manifest(url)
        self._manifests = [m for m in self._manifests if m.id != manifest.id]
        self._manifests.append(manifest)
        if protected:
            self._protected.add(manifest.id)
        return manifest
```

Add after `installed`:

```python
def uninstall(self, addon_id: str) -> None:
    if addon_id in self._protected:
        raise AddonRemovalError(f"{addon_id} is protected and cannot be removed")
    remaining = [m for m in self._manifests if m.id != addon_id]
    if len(remaining) == len(self._manifests):
        raise AddonRemovalError(f"no installed addon with id {addon_id}")
    self._manifests = remaining


def is_protected(self, addon_id: str) -> bool:
    return addon_id in self._protected
```

- [ ] **Step 5: Run tests + gates to verify pass**

Run: `uv run pytest tests/application/test_addon_repository.py -q && uv run mypy src && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/domain/errors.py src/gravitas/application/addon_repository.py tests/application/test_addon_repository.py
git commit -m "feat(addons): repository uninstall + protected-addon tracking"
```

---

### Task 2: UninstallAddon use case

**Files:**
- Create: `src/gravitas/application/uninstall_addon.py`
- Test: `tests/application/test_use_cases.py`

**Interfaces:**
- Consumes: `AddonRepository.uninstall` (Task 1).
- Produces: `class UninstallAddon` with `async def __call__(self, addon_id: str) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/application/test_use_cases.py`:

```python
import pytest

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain.errors import AddonRemovalError


async def test_uninstall_addon_removes(repo_source_factory: object) -> None:
    # Build a repo with one installed addon, then uninstall it.
    from tests.application.test_addon_repository import FakeSource

    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    await UninstallAddon(repo)(manifest.id)
    assert repo.installed() == []


async def test_uninstall_addon_propagates_error() -> None:
    from tests.application.test_addon_repository import FakeSource

    repo = AddonRepository(FakeSource())
    with pytest.raises(AddonRemovalError):
        await UninstallAddon(repo)("nope")
```

Note: if `test_use_cases.py` has no `repo_source_factory` fixture, drop that parameter from the first test signature — it is unused here (kept only to avoid an accidental name clash). Use `async def test_uninstall_addon_removes() -> None:` instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_use_cases.py -k uninstall -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gravitas.application.uninstall_addon'`.

- [ ] **Step 3: Implement the use case**

Create `src/gravitas/application/uninstall_addon.py`:

```python
"""Use case: uninstall an addon by id."""

from __future__ import annotations

from gravitas.application.addon_repository import AddonRepository


class UninstallAddon:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, addon_id: str) -> None:
        self._repo.uninstall(addon_id)
```

- [ ] **Step 4: Run test + gates to verify pass**

Run: `uv run pytest tests/application/test_use_cases.py -k uninstall -q && uv run mypy src`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/application/uninstall_addon.py tests/application/test_use_cases.py
git commit -m "feat(addons): UninstallAddon use case"
```

---

### Task 3: CatalogRowsModel filtering + CatalogController.setFilter

**Files:**
- Modify: `src/gravitas/presentation/models/catalog_rows_model.py`
- Modify: `src/gravitas/presentation/controllers/catalog_controller.py`
- Test: `tests/presentation/test_catalog_rows_model.py`, `tests/presentation/test_catalog_controller.py`

**Interfaces:**
- Consumes: existing `CatalogRow`, `PosterGridModel`.
- Produces:
  - `CatalogRowsModel.set_filter(mode: str) -> None` (mode ∈ `all|movie|series|trending`)
  - `CatalogController.setFilter(mode: str) -> None` (`@Slot(str)`)
  - `set_rows` re-applies the active filter.

- [ ] **Step 1: Write the failing tests**

Append to `tests/presentation/test_catalog_rows_model.py`:

```python
from gravitas.application.browse_catalog import CatalogRow
from gravitas.domain.models import MediaItem
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel


def _row(title: str, type_: str, catalog_id: str) -> CatalogRow:
    return CatalogRow(
        title=title,
        addon_id="a",
        type=type_,  # type: ignore[arg-type]
        catalog_id=catalog_id,
        items=[MediaItem(id="tt1", type=type_, name="X", poster=None)],  # type: ignore[arg-type]
    )


_ROWS = [
    _row("Popular Movies", "movie", "top"),
    _row("New Series", "series", "year"),
    _row("Trending Now", "movie", "trending"),
    _row("Documentaries", "series", "docs"),
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


def test_filter_trending_matches_title_or_catalog_id_case_insensitive(qapp: object) -> None:
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
```

Append to `tests/presentation/test_catalog_controller.py`:

```python
async def test_set_filter_narrows_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    controller = CatalogController(FakeBrowse(), model)  # type: ignore[arg-type]
    await controller.load_catalog()
    controller.setFilter("series")
    assert model.rowCount() == 0  # FakeBrowse yields a single movie row
    controller.setFilter("movie")
    assert model.rowCount() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/presentation/test_catalog_rows_model.py tests/presentation/test_catalog_controller.py -q`
Expected: FAIL — `AttributeError: 'CatalogRowsModel' object has no attribute 'set_filter'` / `setFilter`.

- [ ] **Step 3: Implement model filtering**

In `src/gravitas/presentation/models/catalog_rows_model.py`, replace `__init__` and `set_rows`, and add the filter helpers. New `__init__`:

```python
    def __init__(self) -> None:
        super().__init__()
        # Full row set and the currently-visible (filtered) subset. Rows are
        # (title, addon_id, type, catalog_id, poster_model); the PosterGridModel
        # reference is held here to keep it alive for QML binding.
        self._all_rows: list[tuple[str, str, str, str, PosterGridModel]] = []
        self._rows: list[tuple[str, str, str, str, PosterGridModel]] = []
        self._filter = "all"
```

New `set_rows`:

```python
def set_rows(self, rows: list[CatalogRow]) -> None:
    self.beginResetModel()
    built: list[tuple[str, str, str, str, PosterGridModel]] = []
    for row in rows:
        poster_model = PosterGridModel()
        poster_model.set_items(row.items)
        built.append((row.title, row.addon_id, row.type, row.catalog_id, poster_model))
    self._all_rows = built
    self._rows = self._filtered(self._all_rows, self._filter)
    self.endResetModel()


def set_filter(self, mode: str) -> None:
    self.beginResetModel()
    self._filter = mode
    self._rows = self._filtered(self._all_rows, mode)
    self.endResetModel()


@staticmethod
def _filtered(
    rows: list[tuple[str, str, str, str, PosterGridModel]], mode: str
) -> list[tuple[str, str, str, str, PosterGridModel]]:
    if mode in ("movie", "series"):
        return [r for r in rows if r[2] == mode]
    if mode == "trending":
        keywords = ("top", "trending", "popular")
        return [r for r in rows if any(k in r[0].lower() or k in r[3].lower() for k in keywords)]
    return list(rows)
```

(`rowCount`/`data`/`roleNames` are unchanged — they already read `self._rows`.)

- [ ] **Step 4: Implement controller slot**

In `src/gravitas/presentation/controllers/catalog_controller.py`, update the import line:

```python
from PySide6.QtCore import QObject, Signal, Slot
```

Add this method to `CatalogController`:

```python
    @Slot(str)  # type: ignore[misc]
    def setFilter(self, mode: str) -> None:
        self._model.set_filter(mode)
```

- [ ] **Step 5: Run tests + gates to verify pass**

Run: `uv run pytest tests/presentation/test_catalog_rows_model.py tests/presentation/test_catalog_controller.py -q && uv run mypy src && uv run ruff check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/presentation/models/catalog_rows_model.py src/gravitas/presentation/controllers/catalog_controller.py tests/presentation/test_catalog_rows_model.py tests/presentation/test_catalog_controller.py
git commit -m "feat(home): type/trending filter on catalog rows model"
```

---

### Task 4: AddonListModel

**Files:**
- Create: `src/gravitas/presentation/models/addon_list_model.py`
- Test: `tests/presentation/test_models.py`

**Interfaces:**
- Consumes: `AddonManifest`.
- Produces: `class AddonListModel(QAbstractListModel)` with `set_addons(manifests: list[AddonManifest], protected_ids: set[str]) -> None` and roles `NameRole`/`IdRole`/`RemovableRole` (QML names `name`, `addonId`, `removable`).

- [ ] **Step 1: Write the failing test**

Append to `tests/presentation/test_models.py`:

```python
from gravitas.domain.models import AddonManifest
from gravitas.presentation.models.addon_list_model import AddonListModel


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/presentation/test_models.py -k addon_list -q`
Expected: FAIL — `ModuleNotFoundError: ...addon_list_model`.

- [ ] **Step 3: Implement the model**

Create `src/gravitas/presentation/models/addon_list_model.py`:

```python
"""Qt list model exposing installed addons to the Settings page."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.domain.models import AddonManifest

_ROOT_INDEX = QModelIndex()


class AddonListModel(QAbstractListModel):
    NameRole = Qt.ItemDataRole.UserRole + 1
    IdRole = Qt.ItemDataRole.UserRole + 2
    RemovableRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self) -> None:
        super().__init__()
        # (name, id, removable)
        self._rows: list[tuple[str, str, bool]] = []

    def set_addons(self, manifests: list[AddonManifest], protected_ids: set[str]) -> None:
        self.beginResetModel()
        self._rows = [(m.name, m.id, m.id not in protected_ids) for m in manifests]
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
        name, addon_id, removable = self._rows[index.row()]
        match role:
            case AddonListModel.NameRole:
                return name
            case AddonListModel.IdRole:
                return addon_id
            case AddonListModel.RemovableRole:
                return removable
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            AddonListModel.NameRole: QByteArray(b"name"),
            AddonListModel.IdRole: QByteArray(b"addonId"),
            AddonListModel.RemovableRole: QByteArray(b"removable"),
        }
```

- [ ] **Step 4: Run test + gates to verify pass**

Run: `uv run pytest tests/presentation/test_models.py -k addon_list -q && uv run mypy src && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/models/addon_list_model.py tests/presentation/test_models.py
git commit -m "feat(settings): AddonListModel for installed addons"
```

---

### Task 5: SettingsController

**Files:**
- Create: `src/gravitas/presentation/controllers/settings_controller.py`
- Test: `tests/presentation/test_settings_controller.py`

**Interfaces:**
- Consumes: `UninstallAddon` (Task 2), `AddonRepository` (`installed`, `is_protected`), `AddonListModel` (Task 4), a catalog controller with `async def load_catalog()`.
- Produces: `class SettingsController(QObject)` with signals `errorOccurred(str)`, `addonsChanged()`; methods `refreshAddons()` (`@Slot()`) and `removeAddon(addon_id: str)` (`@asyncSlot(str)`).

- [ ] **Step 1: Write the failing test**

Create `tests/presentation/test_settings_controller.py`:

```python
import pytest

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.presentation.controllers.settings_controller import SettingsController
from gravitas.presentation.models.addon_list_model import AddonListModel
from tests.application.test_addon_repository import FakeSource


class FakeCatalogController:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def load_catalog(self) -> None:
        self.refresh_calls += 1


def _build(
    repo: AddonRepository,
) -> tuple[SettingsController, AddonListModel, FakeCatalogController]:
    model = AddonListModel()
    catalog = FakeCatalogController()
    controller = SettingsController(UninstallAddon(repo), repo, model, catalog)  # type: ignore[arg-type]
    return controller, model, catalog


async def test_refresh_populates_model_with_protected_flag(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    protected = await repo.install("https://a/", protected=True)
    controller, model, _ = _build(repo)
    controller.refreshAddons()
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), AddonListModel.RemovableRole) is False
    assert model.data(model.index(0, 0), AddonListModel.IdRole) == protected.id


async def test_remove_addon_uninstalls_and_refreshes(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")  # not protected
    controller, model, catalog = _build(repo)
    controller.refreshAddons()
    await controller.removeAddon(manifest.id)
    assert repo.installed() == []
    assert catalog.refresh_calls == 1
    assert model.rowCount() == 0


async def test_remove_protected_emits_error(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/", protected=True)
    controller, model, catalog = _build(repo)
    controller.refreshAddons()
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.removeAddon(manifest.id)
    assert len(errors) == 1
    assert repo.installed() != []
    assert catalog.refresh_calls == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/presentation/test_settings_controller.py -q`
Expected: FAIL — `ModuleNotFoundError: ...settings_controller`.

- [ ] **Step 3: Implement the controller**

Create `src/gravitas/presentation/controllers/settings_controller.py`:

```python
"""QObject bridge: manage installed addons for the Settings page."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain.errors import GravitasError
from gravitas.presentation.models.addon_list_model import AddonListModel


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class SettingsController(QObject):
    errorOccurred = Signal(str)
    addonsChanged = Signal()

    def __init__(
        self,
        uninstall: UninstallAddon,
        repo: AddonRepository,
        model: AddonListModel,
        catalog_controller: _RefreshesCatalog,
    ) -> None:
        super().__init__()
        self._uninstall = uninstall
        self._repo = repo
        self._model = model
        self._catalog_controller = catalog_controller

    @Slot()  # type: ignore[misc]
    def refreshAddons(self) -> None:
        installed = self._repo.installed()
        protected = {m.id for m in installed if self._repo.is_protected(m.id)}
        self._model.set_addons(installed, protected)
        self.addonsChanged.emit()

    @asyncSlot(str)  # type: ignore[untyped-decorator]
    async def removeAddon(self, addon_id: str) -> None:
        try:
            await self._uninstall(addon_id)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
            return
        await self._catalog_controller.load_catalog()
        self.refreshAddons()
```

- [ ] **Step 4: Run test + gates to verify pass**

Run: `uv run pytest tests/presentation/test_settings_controller.py -q && uv run mypy src && uv run ruff check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/controllers/settings_controller.py tests/presentation/test_settings_controller.py
git commit -m "feat(settings): SettingsController for addon add/list/remove"
```

---

### Task 6: Composition root wiring

**Files:**
- Modify: `src/gravitas/main.py`
- Test: `tests/test_composition.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: context properties `settingsController`, `addonListModel` available to QML; Cinemeta installed with `protected=True`; settings list primed at bootstrap; a newly-added addon refreshes the settings list.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_composition.py` (this file builds the app via `build_app`; match its existing pattern for obtaining the engine — reuse the existing fixture/helper it already uses rather than re-instantiating):

```python
def test_settings_context_properties_present(qapp: object) -> None:
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app([], DEFAULT_ADDON)
    ctx = engine.rootContext()
    assert ctx.contextProperty("settingsController") is not None
    assert ctx.contextProperty("addonListModel") is not None
```

If `tests/test_composition.py` already has a helper that returns a built engine, call it instead of `build_app([], DEFAULT_ADDON)` to stay consistent. Inspect the file first.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition.py -k settings_context -q`
Expected: FAIL — the context properties are null / assertion error.

- [ ] **Step 3: Wire main.py**

In `src/gravitas/main.py`:

Add imports near the other application/presentation imports:

```python
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.presentation.controllers.settings_controller import SettingsController
from gravitas.presentation.models.addon_list_model import AddonListModel
```

In `build_app`, after `addon_controller = AddonController(...)`, add:

```python
install_addon = InstallAddon(repo)
addon_list_model = AddonListModel()
settings_controller = SettingsController(
    UninstallAddon(repo), repo, addon_list_model, catalog_controller
)
# Keep the Settings list in sync after a user installs a new addon.
addon_controller.addonInstalled.connect(lambda _name: settings_controller.refreshAddons())
```

Note: `addon_controller` is currently built as `AddonController(InstallAddon(repo), catalog_controller)`. Change it to reuse the shared `install_addon` — move the `install_addon = InstallAddon(repo)` line **above** the `addon_controller = ...` line and build it as `AddonController(install_addon, catalog_controller)`.

Add the two context properties alongside the others:

```python
    ctx.setContextProperty("settingsController", settings_controller)
    ctx.setContextProperty("addonListModel", addon_list_model)
```

Replace the `bootstrap` coroutine body with a protected default install (this also removes the startup "Installed: Cinemeta" toast):

```python
    async def bootstrap() -> None:
        # Install the default addon as protected (non-removable), then bring
        # the UI up to date deterministically: load the catalog rows and prime
        # the Settings addon list before bootstrap() returns.
        await install_addon(default_addon_url, protected=True)
        await catalog_controller.load_catalog()
        settings_controller.refreshAddons()
```

Add the two new objects to the keep-alive tuple `engine._gravitas_refs`:

```python
    engine._gravitas_refs = (  # type: ignore[attr-defined]
        catalog_controller,
        detail_controller,
        player_controller,
        addon_controller,
        discover_controller,
        settings_controller,
        rows_model,
        discover_model,
        stream_model,
        addon_list_model,
    )
```

- [ ] **Step 4: Run test + full suite + gates**

Bootstrap calls `install_addon(default_addon_url, protected=True)`, but `InstallAddon.__call__` currently forwards only `url` and would reject the kwarg. Update it first.

In `src/gravitas/application/install_addon.py`, change `__call__`:

```python
    async def __call__(self, url: str, *, protected: bool = False) -> AddonManifest:
        return await self._repo.install(url, protected=protected)
```

Then run: `uv run pytest -q && uv run mypy src && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/main.py src/gravitas/application/install_addon.py tests/test_composition.py
git commit -m "feat(app): wire SettingsController + protected default addon"
```

---

### Task 7: Add gear + trash glyphs to Icons

**Files:**
- Modify: `src/gravitas/presentation/qml/components/Icons.qml`

**Interfaces:**
- Produces: `Icons.gear`, `Icons.trash` string properties.

- [ ] **Step 1: Add the glyphs**

In `src/gravitas/presentation/qml/components/Icons.qml`, add after the `x` line:

```qml
    readonly property string gear: String.fromCharCode(0xe270)
    readonly property string trash: String.fromCharCode(0xe4a6)
```

- [ ] **Step 2: Verify app still launches (glyphs are valid)**

Run: `uv run gravitas` (needs a display). Confirm the app window opens without QML errors in the terminal, then close it.
Expected: no `Icons.qml` parse/reference errors printed.

- [ ] **Step 3: Commit**

```bash
git add src/gravitas/presentation/qml/components/Icons.qml
git commit -m "feat(ui): add gear + trash Phosphor glyphs"
```

---

### Task 8: TopBar + Main/Home integration

**Files:**
- Create: `src/gravitas/presentation/qml/components/TopBar.qml`
- Modify: `src/gravitas/presentation/qml/Main.qml`
- Modify: `src/gravitas/presentation/qml/Home.qml`

**Interfaces:**
- Consumes: `catalogController.setFilter(mode)` (Task 3), `Icons.gear` (Task 7), `Theme.*`, `AppButton`, `AppIcon`.
- Produces: `TopBar` component with signals `tabSelected(string mode)` and `openSettings()`, and property `activeMode`.

- [ ] **Step 1: Create TopBar.qml**

Create `src/gravitas/presentation/qml/components/TopBar.qml`:

```qml
import QtQuick
import QtQuick.Controls
import "."

Rectangle {
    id: bar
    signal tabSelected(string mode)
    signal openSettings()
    property string activeMode: "all"

    height: 56
    color: Theme.surface

    readonly property var tabs: [
        { label: "All", mode: "all" },
        { label: "Movies", mode: "movie" },
        { label: "Series", mode: "series" },
        { label: "Trending", mode: "trending" }
    ]

    Row {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8

        Repeater {
            model: bar.tabs
            delegate: Item {
                required property var modelData
                width: tabLabel.implicitWidth + 24
                height: 36

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radius
                    color: bar.activeMode === modelData.mode
                        ? Theme.surfacePress
                        : (tabHover.hovered ? Theme.surfaceHover : "transparent")
                    Behavior on color { ColorAnimation { duration: Theme.durFast } }
                }
                Text {
                    id: tabLabel
                    anchors.centerIn: parent
                    text: modelData.label
                    font.pixelSize: Theme.fontBody
                    color: bar.activeMode === modelData.mode ? Theme.accent : Theme.text
                }
                HoverHandler { id: tabHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        bar.activeMode = modelData.mode
                        bar.tabSelected(modelData.mode)
                    }
                }
            }
        }
    }

    AppButton {
        id: gearButton
        ghost: true
        iconGlyph: Icons.gear
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        onClicked: bar.openSettings()
    }
}
```

- [ ] **Step 2: Integrate into Main.qml**

In `src/gravitas/presentation/qml/Main.qml`, replace the `StackView { ... }` block with a `TopBar` above a `StackView`, and add the settings page. Replace lines 12–45 (the `StackView` + `Component`s) with:

```qml
    TopBar {
        id: topBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        visible: {
            var it = stack.currentItem
            return it !== null
                && (it.objectName === "homePage" || it.objectName === "settingsPage")
        }
        onTabSelected: (mode) => {
            while (stack.depth > 1) stack.pop()
            catalogController.setFilter(mode)
        }
        onOpenSettings: stack.push(settingsPage)
    }

    StackView {
        id: stack
        anchors.top: topBar.visible ? topBar.bottom : parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        initialItem: homePage
    }

    Component {
        id: homePage
        Home {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onSeeAll: (addonId, type, catalogId) => {
                discoverController.open(addonId, type, catalogId)
                stack.push(discoverPage)
            }
        }
    }
    Component {
        id: detailPage
        Detail {
            onPlayUrl: (url) => stack.push(playerPage, {url: url})
            onBack: () => stack.pop()
        }
    }
    Component {
        id: playerPage
        Player { onBack: stack.pop() }
    }
    Component {
        id: discoverPage
        Discover {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onBack: () => stack.pop()
        }
    }
    Component {
        id: settingsPage
        Settings { onBack: stack.pop() }
    }
```

(The `Connections` blocks and `Toast` stay as-is. Add a `Connections` block for `settingsController` errors — see Task 9 Step 2.)

- [ ] **Step 3: Set Home objectName + strip the addon bar**

In `src/gravitas/presentation/qml/Home.qml`:

Add `objectName: "homePage"` to the root `Item` (right after `id: home`).

Delete the entire `Rectangle { id: addonBar ... }` block (the URL field + Add button).

Re-anchor `rowsView`: change `anchors.top: addonBar.bottom` to `anchors.top: parent.top`.

- [ ] **Step 4: Verify launch — tabs filter, gear present**

Run: `uv run gravitas`
Expected: nav bar shows All/Movies/Series/Trending + gear; clicking Movies/Series narrows the rows; Trending shows top/popular rows; All restores everything; no addon URL strip on Home. (Settings page opens in Task 9 — the gear will error until `Settings.qml` exists; if testing before Task 9, expect a "Settings is not a type" error on gear click only.)

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/qml/components/TopBar.qml src/gravitas/presentation/qml/Main.qml src/gravitas/presentation/qml/Home.qml
git commit -m "feat(ui): app top nav bar with type/trending tabs"
```

---

### Task 9: Settings page

**Files:**
- Create: `src/gravitas/presentation/qml/Settings.qml`
- Modify: `src/gravitas/presentation/qml/Main.qml`

**Interfaces:**
- Consumes: `addonController.addAddon(url)`, `settingsController.removeAddon(id)`, `addonListModel`, `Icons.trash`/`Icons.arrowLeft`, `AppTextField`, `AppButton`, `Theme.*`.
- Produces: `Settings` component with signal `back()`.

- [ ] **Step 1: Create Settings.qml**

Create `src/gravitas/presentation/qml/Settings.qml`:

```qml
import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: settings
    objectName: "settingsPage"
    signal back()

    Column {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 24
        spacing: 20

        Row {
            spacing: 12
            AppButton {
                ghost: true
                iconGlyph: Icons.arrowLeft
                onClicked: settings.back()
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Settings"
                color: Theme.text
                font.pixelSize: Theme.fontTitle
            }
        }

        // --- Addons section ---
        Text {
            text: "Addons"
            color: Theme.textDim
            font.pixelSize: Theme.fontBody
        }
        Row {
            width: parent.width
            spacing: 8
            AppTextField {
                id: urlField
                width: parent.width - addButton.width - parent.spacing
                placeholderText: "Addon manifest URL…"
            }
            AppButton {
                id: addButton
                text: "Add"
                onClicked: {
                    addonController.addAddon(urlField.text)
                    urlField.text = ""
                }
            }
        }
        ListView {
            width: parent.width
            height: Math.min(contentHeight, 300)
            model: addonListModel
            interactive: true
            clip: true
            spacing: 4
            delegate: Rectangle {
                width: ListView.view.width
                height: 44
                radius: Theme.radius
                color: Theme.surface
                required property string name
                required property string addonId
                required property bool removable
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: parent.name
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                }
                AppButton {
                    ghost: true
                    iconGlyph: Icons.trash
                    visible: parent.removable
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: settingsController.removeAddon(parent.addonId)
                }
            }
        }

        // --- About section ---
        Text {
            text: "About"
            color: Theme.textDim
            font.pixelSize: Theme.fontBody
        }
        Column {
            spacing: 4
            Text {
                text: "Gravitas"
                color: Theme.text
                font.pixelSize: Theme.fontBody
            }
            Text {
                text: "A memory-efficient, Linux-first media center."
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
            Text {
                text: "github.com/…/gravitas"
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
        }
    }
}
```

Note: confirm `Theme.fontSmall` exists; if not, use `Theme.fontBody`. Inspect `components/Theme.qml` first and use whatever small/dim size it defines.

- [ ] **Step 2: Add settingsController error Connections in Main.qml**

In `src/gravitas/presentation/qml/Main.qml`, add alongside the other `Connections` blocks:

```qml
    Connections {
        target: settingsController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
```

- [ ] **Step 3: Verify launch — add/remove/protected/About**

Run: `uv run gravitas`
Expected: gear opens Settings; Cinemeta listed with **no** trash button; adding a valid manifest URL appends it with a trash button; removing it drops the row and updates Home; back button returns to Home with the nav bar intact; About section renders.

- [ ] **Step 4: Full suite + gates**

Run: `uv run pytest -q && uv run mypy src && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/qml/Settings.qml src/gravitas/presentation/qml/Main.qml
git commit -m "feat(settings): settings page with addon management + about"
```

---

## Notes for the implementer

- QML has no unit tests — Tasks 7–9 are verified by launching (`uv run gravitas`, needs a display). The Python quality gates still must pass after every task.
- `required property` in QML delegates: inside a `Rectangle` delegate, `parent.name` refers to the delegate itself only when the property is declared on that same `Rectangle` — the code above declares them on the delegate root, so `parent.name`/`parent.addonId`/`parent.removable` from a child resolves to the delegate. If a binding warns about `parent`, give the delegate root an `id` and reference that id instead.
- Trending is a client-side keyword filter over already-loaded rows; it makes no network calls and can legitimately be empty if no installed catalog matches `top`/`trending`/`popular`.
- Removing addons is not persisted (in-memory store); Cinemeta re-bootstraps protected on next launch. This is intended for this milestone.
