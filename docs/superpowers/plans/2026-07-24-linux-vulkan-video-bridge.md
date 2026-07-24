# Linux Vulkan Zero-Copy Video Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in Vulkan RHI path on Linux that keeps video zero-copy, by making mpv render through OpenGL into a Vulkan-exported GPU surface that Qt's Vulkan device samples — mirroring the existing macOS Metal bridge, for NVIDIA/Wayland first.

**Architecture:** A native C++ bridge (`native/linux/`) owns mpv's OpenGL render context and renders into an exportable `VkImage` allocated on Qt's QRhi device; opaque-FD external memory + a shared semaphore cross the OpenGL/Vulkan boundary. A ctypes loader (`vulkan_bridge.py`) refuses any ABI/Qt mismatch and falls back. `main.py` commits the Vulkan RHI **only** after the bridge loads, so failure costs nothing — the app stays on today's OpenGL zero-copy path.

**Tech Stack:** Python 3, PySide6 (Qt6/QML), libmpv render API, C++17, Vulkan, EGL/OpenGL, ctypes, pytest, mypy --strict, ruff.

## Global Constraints

- Clean Architecture dependency rule holds: `presentation → application → domain ← infrastructure`. New code lives in `presentation/video/` and `infrastructure/graphics.py`; only `main.py` (composition root) wires concrete classes.
- The three quality gates must pass on every commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.
- `mypy --strict` on `src` only; `mpv`/`ctypes` boundaries use precise `# type: ignore[code]`, never blanket ignores.
- Tests never require libmpv or a GPU. Native interop is **launch-verified on real hardware** — the same coverage class as QML.
- Default behaviour is unchanged and default-off: without `GRAVITAS_GRAPHICS=vulkan` on Linux, nothing in this plan runs. macOS Metal, Windows, and the default Linux OpenGL path are untouched.
- The bridge is built against the exact Qt inside the PySide6 wheel; QRhi has no binary-compatibility guarantee. The loader enforces this at runtime (ABI int + `qVersion()` string).
- Native ABI symbol prefix is `gv_video_bridge_vk_*` with its own ABI number, distinct from the macOS `gv_video_bridge_*`, so the two bridges can never be cross-loaded.
- Ring buffer size is `N = 3`. Surface format is `RGBA8` / `VK_FORMAT_R8G8B8A8_UNORM` (`RGBA16F` reserved for later HDR). GL and Vulkan tiling are both OPTIMAL.

---

## File Structure

**Created:**
- `src/gravitas/presentation/video/vulkan_bridge.py` — ctypes loader; ABI + Qt-version refusal; `library()`, `available()`, `last_error()`, `BridgeUnavailable`.
- `src/gravitas/presentation/video/mpv_vulkan_item.py` — `QQuickItem` + `QSGSimpleTextureNode`; drives the bridge from the render thread.
- `native/linux/gravitas_video_bridge_vk.h` — the C ABI.
- `native/linux/gravitas_video_bridge_vk.cpp` — the implementation (Qt shell + opaque-FD interop backend).
- `tests/infrastructure/` additions and `tests/presentation/test_mpv_vulkan_item.py` — loader + geometry + wiring tests.
- `src/gravitas/presentation/video/libgravitas_video_bridge_vk.so` — build output (git-ignored, produced by the build script).

**Modified:**
- `src/gravitas/infrastructure/graphics.py` — add `vulkan_scene_graph()`.
- `src/gravitas/main.py` — RHI/item selection gated on `vulkan_bridge.available()` before `setGraphicsApi`.
- `scripts/build_video_bridge.py` — add a Linux build path.
- `packaging/gravitas.spec` — bundle the `.so`; hidden import.
- `.github/workflows/ci.yml` and/or `.github/workflows/release.yml` — build the `.so`, ship it.
- `.gitignore` — ignore `libgravitas_video_bridge_vk.so`.

**Phasing:** Phase 1 (Tasks 1–4) is pure Python, pure TDD, and ships working software on its own — Vulkan requested but bridge absent falls back to OpenGL, fully tested. Phase 2 (Tasks 5–9) is native bring-up, ordered by the spec's risks, verified by launching the app. Phase 3 (Task 10) is packaging and CI.

---

## Task 1: `vulkan_scene_graph()` predicate

**Files:**
- Modify: `src/gravitas/infrastructure/graphics.py`
- Test: `tests/infrastructure/test_graphics.py`

**Interfaces:**
- Produces: `vulkan_scene_graph(environ: Mapping[str, str] | None = None, platform: str | None = None) -> bool` — True only on Linux when `GRAVITAS_GRAPHICS=vulkan` (case-insensitive, whitespace-trimmed). Reports **intent only**, not capability.

- [ ] **Step 1: Write the failing tests**

Add to `tests/infrastructure/test_graphics.py`:

```python
from gravitas.infrastructure.graphics import vulkan_scene_graph


def test_vulkan_is_opt_in_on_linux() -> None:
    assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": "vulkan"}, LINUX) is True
    # Forgiving spelling, same as the metal switch.
    assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": " Vulkan "}, LINUX) is True


def test_vulkan_is_off_by_default_on_linux() -> None:
    assert vulkan_scene_graph({}, LINUX) is False
    assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": "opengl"}, LINUX) is False


def test_vulkan_is_never_selected_off_linux() -> None:
    for platform in (MACOS, WINDOWS):
        assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": "vulkan"}, platform) is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/infrastructure/test_graphics.py -v`
Expected: FAIL, `ImportError: cannot import name 'vulkan_scene_graph'`.

- [ ] **Step 3: Implement the predicate**

Add to `src/gravitas/infrastructure/graphics.py` (constant `LINUX = "linux"` at top, next to `MACOS`/`WINDOWS`):

```python
LINUX = "linux"


def vulkan_scene_graph(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """True when Qt Quick should run on the Vulkan RHI (Linux opt-in only).

    Intent only -- whether the native bridge can actually be loaded is decided
    in main.py, which commits the RHI only if it can. Off Linux this is always
    False: macOS chooses between Metal and OpenGL, Windows is OpenGL-only.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform != LINUX:
        return False
    return environ.get(_VARIABLE, "").strip().lower() == "vulkan"
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/infrastructure/test_graphics.py -v`
Expected: PASS (new tests plus the existing five).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
git add src/gravitas/infrastructure/graphics.py tests/infrastructure/test_graphics.py
git commit -m "feat(graphics): add vulkan_scene_graph opt-in predicate for Linux"
```

---

## Task 2: `vulkan_bridge.py` loader

**Files:**
- Create: `src/gravitas/presentation/video/vulkan_bridge.py`
- Test: `tests/presentation/test_vulkan_bridge.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing from this plan.
- Produces: `library() -> ctypes.CDLL` (raises `BridgeUnavailable`), `available() -> bool`, `last_error(library_) -> str`, `BridgeUnavailable(Exception)`, module globals `_ABI: int`, `_LIBRARY: Path`, `_library`, `_load_failure`. Bound symbols: `gv_video_bridge_vk_abi`, `_qt_version`, `_error`, `_create(void*, void*) -> void*`, `_destroy`, `_set_size(void*, int, int) -> int`, `_set_item(void*, void*)`, `_stale(void*) -> int`, `_render(void*) -> int`, `_format(void*) -> char*`, `_texture(void*) -> void*`.

This is a direct analog of `src/gravitas/presentation/video/metal_bridge.py`. Read that file first; keep the structure, swap the symbol prefix to `gv_video_bridge_vk_`, the library name to `libgravitas_video_bridge_vk.so`, the platform gate to `linux`, and start `_ABI` at `1`.

- [ ] **Step 1: Write the failing tests**

Create `tests/presentation/test_vulkan_bridge.py` (mirrors the loader half of `test_mpv_metal_item.py`):

```python
"""The Linux Vulkan bridge loader's refusal and fallback rules.

The rendering needs a Vulkan scene graph and the native .so, so it is verified
by running the app; what is testable here is that a missing, stale, or
mismatched library is a clean fallback rather than a crash.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from gravitas.presentation.video import vulkan_bridge


@pytest.fixture(autouse=True)
def _forget_cached_load() -> None:
    vulkan_bridge._library = None
    vulkan_bridge._load_failure = None


def test_loader_refuses_anything_but_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(vulkan_bridge.BridgeUnavailable, match="not Linux"):
        vulkan_bridge.library()
    assert vulkan_bridge.available() is False


def test_a_missing_library_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vulkan_bridge.Path, "is_file", lambda _self: False)
    assert vulkan_bridge.available() is False


def test_loader_refuses_a_bridge_built_for_another_qt(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLibrary:
        def gv_video_bridge_vk_abi(self) -> int:
            return vulkan_bridge._ABI

        def gv_video_bridge_vk_qt_version(self) -> bytes:
            return b"6.0.0"

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vulkan_bridge.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(vulkan_bridge, "_bind", lambda _lib: FakeLibrary())
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: object())
    with pytest.raises(vulkan_bridge.BridgeUnavailable, match=r"built against Qt 6\.0\.0"):
        vulkan_bridge.library()


def test_loader_refuses_a_stale_abi(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import qVersion

    class FakeLibrary:
        def gv_video_bridge_vk_abi(self) -> int:
            return vulkan_bridge._ABI - 1

        def gv_video_bridge_vk_qt_version(self) -> bytes:
            return qVersion().encode()

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vulkan_bridge.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(vulkan_bridge, "_bind", lambda _lib: FakeLibrary())
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: object())
    with pytest.raises(vulkan_bridge.BridgeUnavailable, match="rebuild the bridge"):
        vulkan_bridge.library()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/presentation/test_vulkan_bridge.py -v`
Expected: FAIL, `ModuleNotFoundError: ... vulkan_bridge`.

- [ ] **Step 3: Implement the loader**

Create `src/gravitas/presentation/video/vulkan_bridge.py` by copying `metal_bridge.py` and applying exactly these changes: module docstring says Linux/Vulkan; `_ABI = 1`; `_LIBRARY = Path(__file__).parent / "libgravitas_video_bridge_vk.so"`; every bound symbol renamed `gv_video_bridge_*` → `gv_video_bridge_vk_*`; the platform gate `if sys.platform != "darwin": raise unavailable("not macOS")` becomes `if sys.platform != "linux": raise unavailable("not Linux")`. The `_bind` signatures are identical in shape (pointers as `c_void_p`, ints as `c_int`, strings as `c_char_p`) — keep the comment about ctypes' `c_int` default truncating 64-bit pointers.

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/presentation/test_vulkan_bridge.py -v`
Expected: PASS.

- [ ] **Step 5: Ignore the build artifact, run gates, commit**

Add to `.gitignore`:
```
src/gravitas/presentation/video/libgravitas_video_bridge_vk.so
```

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
git add src/gravitas/presentation/video/vulkan_bridge.py tests/presentation/test_vulkan_bridge.py .gitignore
git commit -m "feat(video): add Linux Vulkan bridge ctypes loader with ABI/Qt refusal"
```

---

## Task 3: `mpv_vulkan_item.py` video item

**Files:**
- Create: `src/gravitas/presentation/video/mpv_vulkan_item.py`
- Test: `tests/presentation/test_mpv_vulkan_item.py`

**Interfaces:**
- Consumes: `vulkan_bridge.library/last_error/BridgeUnavailable` (Task 2).
- Produces: `MpvVulkanVideoItem(QQuickItem)` with a `handle` `Property("QVariant")`, `requestUpdate()` `Slot`, module-level `_BRIDGES: dict[int, int]`, and helpers `fitted_rect(item_w, item_h, video_w, video_h) -> QRectF`, `mpv_pointer(handle) -> int`.

This is a near-copy of `mpv_metal_item.py`. Read it first. The item logic — `_BRIDGES` keyed by window pointer, `_bridge()` create/reuse/stale, `updatePaintNode` calling set_size/render/texture, `setOwnsTexture(False)`, `wrapInstance(address, QSGTexture)` — is identical; only the imported loader (`vulkan_bridge`) and the symbol names (`gv_video_bridge_vk_*`) differ. `fitted_rect` and `mpv_pointer` are duplicated here rather than shared, matching the existing per-item convention (the software and Metal items each carry their own).

- [ ] **Step 1: Write the failing tests**

Create `tests/presentation/test_mpv_vulkan_item.py`. Mirror the geometry and `_FakeBridge` tests from `test_mpv_metal_item.py`, renaming the module under test and the fake's methods to `gv_video_bridge_vk_*`:

```python
from __future__ import annotations

import pytest

from gravitas.presentation.video.mpv_vulkan_item import fitted_rect, mpv_pointer


def test_video_fills_an_item_of_the_same_aspect() -> None:
    rect = fitted_rect(1920, 1080, 1920, 1080)
    assert (rect.x(), rect.y()) == (0, 0)
    assert (rect.width(), rect.height()) == (1920, 1080)


def test_taller_item_letterboxes() -> None:
    rect = fitted_rect(1920, 1200, 1920, 1080)
    assert (rect.width(), rect.height()) == (1920, 1080)
    assert (rect.x(), rect.y()) == (0, 60)


def test_degenerate_sizes_do_not_divide_by_zero() -> None:
    assert fitted_rect(800, 600, 0, 0).width() == 800
    assert fitted_rect(0, 0, 1920, 1080).width() == 0


def test_mpv_pointer_survives_a_handle_that_is_not_mpv() -> None:
    assert mpv_pointer(object()) == 0
    assert mpv_pointer(None) == 0


class _FakeBridge:
    def __init__(self) -> None:
        self.created = 0

    def gv_video_bridge_vk_create(self, _window: int, _mpv: object) -> int:
        self.created += 1
        return 0xBEEF00 + self.created

    def gv_video_bridge_vk_stale(self, _bridge: object) -> int:
        return 1 if getattr(self, "stale", False) else 0

    def gv_video_bridge_vk_set_item(self, _bridge: object, item: int) -> None:
        self.item = item


def test_a_second_player_page_reuses_the_first_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_vulkan_item

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_vulkan_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_vulkan_item, "_BRIDGES", {})
    monkeypatch.setattr(mpv_vulkan_item, "mpv_pointer", lambda _handle: 0x1234)

    window = QObject()
    first = mpv_vulkan_item.MpvVulkanVideoItem()
    first._handle = object()
    handle = first._bridge(fake, window)
    assert handle is not None

    second = mpv_vulkan_item.MpvVulkanVideoItem()
    second._handle = first._handle
    assert second._bridge(fake, window) == handle
    assert fake.created == 1


def test_a_recreated_scene_graph_gets_a_new_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_vulkan_item

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_vulkan_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_vulkan_item, "_BRIDGES", {})
    monkeypatch.setattr(mpv_vulkan_item, "mpv_pointer", lambda _handle: 0x1234)

    window = QObject()
    item = mpv_vulkan_item.MpvVulkanVideoItem()
    item._handle = object()
    first = item._bridge(fake, window)
    assert fake.created == 1

    fake.stale = True
    second = item._bridge(fake, window)
    assert second != first
    assert fake.created == 2
    assert item._size == (0, 0)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/presentation/test_mpv_vulkan_item.py -v`
Expected: FAIL, `ModuleNotFoundError: ... mpv_vulkan_item`.

- [ ] **Step 3: Implement the item**

Create `src/gravitas/presentation/video/mpv_vulkan_item.py` by copying `mpv_metal_item.py` and applying exactly: class renamed `MpvMetalVideoItem` → `MpvVulkanVideoItem`; import `from gravitas.presentation.video.vulkan_bridge import BridgeUnavailable, last_error, library`; every `bridge.gv_video_bridge_*` call renamed to `bridge.gv_video_bridge_vk_*`; docstring updated to describe the Vulkan/opaque-FD surface instead of the IOSurface. Keep `fitted_rect`, `mpv_pointer`, `_BRIDGES`, `_bridge`, `updatePaintNode`, `requestUpdate` byte-for-byte in structure.

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/presentation/test_mpv_vulkan_item.py -v`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
git add src/gravitas/presentation/video/mpv_vulkan_item.py tests/presentation/test_mpv_vulkan_item.py
git commit -m "feat(video): add Vulkan zero-copy video item mirroring the Metal one"
```

---

## Task 4: Wire selection into `main.py`

**Files:**
- Create: `src/gravitas/presentation/video/backend.py` (a pure, testable selector)
- Test: `tests/presentation/test_video_backend.py`
- Modify: `src/gravitas/main.py:372-401` (the video-item registration block)

**Interfaces:**
- Consumes: `vulkan_scene_graph` (Task 1), `vulkan_bridge.available` (Task 2), `metal_scene_graph`.
- Produces: `choose_video_backend(want_vulkan: bool, vulkan_available: bool, want_metal: bool, metal_available: bool) -> str` returning one of `"vulkan"`, `"metal-zero-copy"`, `"metal-software"`, `"opengl"`. Pure — no Qt, no imports of the item classes — so the composition-root decision is unit-tested without a GUI.

- [ ] **Step 1: Write the failing tests**

Create `tests/presentation/test_video_backend.py`:

```python
from __future__ import annotations

from gravitas.presentation.video.backend import choose_video_backend


def test_vulkan_requested_and_available_wins() -> None:
    assert choose_video_backend(True, True, False, False) == "vulkan"


def test_vulkan_requested_but_unavailable_falls_back_to_opengl() -> None:
    # The core no-regression guarantee: a failed Vulkan opt-in costs nothing.
    assert choose_video_backend(True, False, False, False) == "opengl"


def test_metal_paths_are_unaffected_by_vulkan() -> None:
    assert choose_video_backend(False, False, True, True) == "metal-zero-copy"
    assert choose_video_backend(False, False, True, False) == "metal-software"


def test_default_is_opengl() -> None:
    assert choose_video_backend(False, False, False, False) == "opengl"


def test_vulkan_never_competes_with_metal() -> None:
    # They are mutually exclusive by platform; if both somehow ask, Vulkan
    # (Linux) is checked first and Metal flags are false there anyway.
    assert choose_video_backend(True, True, True, True) == "vulkan"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run pytest tests/presentation/test_video_backend.py -v`
Expected: FAIL, `ModuleNotFoundError: ... backend`.

- [ ] **Step 3: Implement the selector**

Create `src/gravitas/presentation/video/backend.py`:

```python
"""Which video item the composition root registers, decided without Qt.

Pulled out of main.py so the choice -- and the no-regression guarantee that a
failed Vulkan opt-in falls back to OpenGL rather than a slower path -- is unit
tested. Capability (`*_available`) is resolved by the caller; this is pure.
"""

from __future__ import annotations


def choose_video_backend(
    want_vulkan: bool,
    vulkan_available: bool,
    want_metal: bool,
    metal_available: bool,
) -> str:
    if want_vulkan and vulkan_available:
        return "vulkan"
    if want_metal:
        return "metal-zero-copy" if metal_available else "metal-software"
    return "opengl"
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run pytest tests/presentation/test_video_backend.py -v`
Expected: PASS.

- [ ] **Step 5: Wire it into `main.py`**

In `src/gravitas/main.py`, add imports near the other graphics imports (line ~56):

```python
from gravitas.infrastructure.graphics import metal_scene_graph, vulkan_scene_graph
from gravitas.presentation.video.backend import choose_video_backend
```

Replace the RHI pin block (currently lines ~235-242, the `if not metal_scene_graph(): setGraphicsApi(OpenGL)`) and the item-registration block (currently ~372-401) with a single decision made **before** `setGraphicsApi`:

```python
    # Decide the video backend before pinning the RHI: a Vulkan opt-in is only
    # honoured if its native bridge actually loads, so a failure never leaves
    # the app on a worse path than today's OpenGL zero-copy one.
    want_vulkan = vulkan_scene_graph()
    vulkan_available = False
    if want_vulkan:
        from gravitas.presentation.video import vulkan_bridge

        vulkan_available = vulkan_bridge.available()

    want_metal = metal_scene_graph()
    metal_available = False
    if want_metal:
        from gravitas.presentation.video import metal_bridge

        metal_available = metal_bridge.available()

    backend = choose_video_backend(
        want_vulkan, vulkan_available, want_metal, metal_available
    )

    # Pin the RHI to match the chosen backend. Vulkan and OpenGL are set
    # explicitly; Metal is Qt's macOS default, so it is left unset.
    if backend == "vulkan":
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Vulkan)
    elif backend == "opengl":
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)

    video_item: type[QQuickItem]
    if backend == "vulkan":
        from gravitas.presentation.video.mpv_vulkan_item import MpvVulkanVideoItem

        video_item = MpvVulkanVideoItem
        _log.info("video renders zero-copy on Vulkan (Linux opt-in)")
    elif backend == "metal-zero-copy":
        from gravitas.presentation.video.mpv_metal_item import MpvMetalVideoItem

        video_item = MpvMetalVideoItem
        _log.info("video renders zero-copy: mpv on the GPU, no frame copies")
    elif backend == "metal-software":
        from gravitas.presentation.video.mpv_sw_item import MpvSwVideoItem

        video_item = MpvSwVideoItem
        _log.info("video renders in software: frames are copied back and uploaded")
    else:
        from gravitas.presentation.video.mpv_item import MpvVideoItem

        video_item = MpvVideoItem
    qmlRegisterType(video_item, "Gravitas", 1, 0, "MpvVideo")  # type: ignore[call-overload]
```

Note: this preserves the exact macOS branch behaviour (Metal default RHI, zero-copy vs software item via `metal_bridge.available()`), so the pre-existing `if metal_scene_graph(): ... else: MpvVideoItem` logic is fully subsumed. Check the macOS OpenGL-fallback block further down (`main.py:643-659`, `sys.platform == "darwin" and not metal_scene_graph()`) still reads correctly — it is unrelated to Vulkan and should be left as is.

- [ ] **Step 6: Verify nothing regressed**

Run: `uv run pytest -q`
Expected: PASS (full suite). Then the gates:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

- [ ] **Step 7: Launch-verify the fallback (no native bridge exists yet)**

Run: `GRAVITAS_GRAPHICS=vulkan uv run gravitas`
Expected: the app launches and browses normally; the log shows the Vulkan opt-in was requested but fell back (bridge not built) and the app is on OpenGL. Playing a video still works via the OpenGL zero-copy item. This proves the no-regression guarantee before any native code exists.

- [ ] **Step 8: Commit**

```bash
git add src/gravitas/presentation/video/backend.py tests/presentation/test_video_backend.py src/gravitas/main.py
git commit -m "feat(main): select Vulkan video backend only when its bridge loads"
```

---

## Task 5: Native C ABI header, skeleton, and Linux build path

**Files:**
- Create: `native/linux/gravitas_video_bridge_vk.h`
- Create: `native/linux/gravitas_video_bridge_vk.cpp` (stubs this task; filled in Tasks 6–9)
- Modify: `scripts/build_video_bridge.py`

**Interfaces:**
- Produces: `libgravitas_video_bridge_vk.so` exporting the `gv_video_bridge_vk_*` C ABI that Task 2's loader binds.

Native GPU interop cannot be unit-tested headless (no GPU in CI; `llvmpipe` cannot drive the opaque-FD NVIDIA path). Tasks 5–9 are therefore **build-and-launch-verified milestones**, ordered by the spec's risks. Each ends with a concrete manual check.

- [ ] **Step 1: Write the header**

Create `native/linux/gravitas_video_bridge_vk.h`, adapting `native/macos/gravitas_video_bridge.h` (keep its THREADING / LIFETIME / "why mpv lives in C++" commentary — every rule transfers):

```c
#ifndef GRAVITAS_VIDEO_BRIDGE_VK_H
#define GRAVITAS_VIDEO_BRIDGE_VK_H

#ifdef __cplusplus
extern "C" {
#endif

#define GV_VIDEO_BRIDGE_VK_ABI 1
#define GV_API __attribute__((visibility("default")))

GV_API int gv_video_bridge_vk_abi(void);
GV_API const char *gv_video_bridge_vk_qt_version(void);
GV_API const char *gv_video_bridge_vk_error(void);

typedef struct GvVideoBridgeVk GvVideoBridgeVk;

GV_API GvVideoBridgeVk *gv_video_bridge_vk_create(void *window, void *mpv);
GV_API void gv_video_bridge_vk_destroy(GvVideoBridgeVk *bridge);
GV_API int gv_video_bridge_vk_set_size(GvVideoBridgeVk *bridge, int width, int height);
GV_API void gv_video_bridge_vk_set_item(GvVideoBridgeVk *bridge, void *item);
GV_API int gv_video_bridge_vk_stale(GvVideoBridgeVk *bridge);
GV_API int gv_video_bridge_vk_render(GvVideoBridgeVk *bridge);
GV_API void *gv_video_bridge_vk_texture(GvVideoBridgeVk *bridge);
GV_API const char *gv_video_bridge_vk_format(GvVideoBridgeVk *bridge);

#ifdef __cplusplus
}
#endif

#endif
```

- [ ] **Step 2: Write the skeleton `.cpp`**

Create `native/linux/gravitas_video_bridge_vk.cpp` with every entry point defined but inert: `gv_video_bridge_vk_abi` returns `GV_VIDEO_BRIDGE_VK_ABI`; `gv_video_bridge_vk_qt_version` returns `qVersion()`; `_error` returns a thread-local `std::string`'s c_str; `_create` returns `nullptr` and sets the error to `"not implemented"`; the rest return `0`/`nullptr`/void. Include `<QtCore/qglobal.h>` for `qVersion()`. This is enough for the loader to accept the library and for `create` to fail cleanly (→ black video under the opt-in, app still runs).

- [ ] **Step 3: Add the Linux build path to `scripts/build_video_bridge.py`**

The existing script is macOS-only (`clang++`, frameworks). Add a Linux branch selected by `sys.platform`. Read the module docstring's aqtinstall instructions and add the Linux equivalent. The Linux build:

```python
LINUX_SOURCE = REPO / "native" / "linux" / "gravitas_video_bridge_vk.cpp"
LINUX_OUTPUT = (
    REPO / "src" / "gravitas" / "presentation" / "video"
    / "libgravitas_video_bridge_vk.so"
)


def build_linux(qt_prefix: Path, output: Path) -> int:
    qt_libs = qt_prefix / "lib"
    rhi_root = qt_prefix / "include" / "QtGui" / qVersion()
    if not (rhi_root / "QtGui" / "rhi" / "qrhi.h").is_file():
        print(
            f"error: no QRhi headers for Qt {qVersion()} under {rhi_root}.\n"
            f"       The Qt SDK must be the same version as the PySide6 wheel.",
            file=sys.stderr,
        )
        return 1
    command = [
        "g++", "-std=c++17", "-fPIC", "-shared", "-O2",
        "-fvisibility=hidden",
        "-Wall", "-Wextra",
        "-o", str(output), str(LINUX_SOURCE),
        "-I", str(qt_prefix / "include"),
        "-I", str(qt_prefix / "include" / "QtCore"),
        "-I", str(qt_prefix / "include" / "QtGui"),
        "-I", str(qt_prefix / "include" / "QtQuick"),
        "-I", str(rhi_root),
        "-I", str(rhi_root / "QtGui"),
        f"-L{qt_libs}",
        "-lQt6Core", "-lQt6Gui", "-lQt6Quick",
        "-lEGL", "-lGL", "-lvulkan",
        f"-Wl,-rpath,{pyside_qt_dir() / 'lib'}",
    ]
    print(" ".join(command))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        return result.returncode
    print(f"built {output}")
    return 0
```

In `main()`, branch on `sys.platform`: `darwin` keeps the existing path; `linux` calls `build_linux(args.qt, LINUX_OUTPUT if args.output is default else args.output)`; anything else errors. The `pyside_qt_dir()` helper already exists and is reused.

- [ ] **Step 4: Install matching Qt headers and build**

```bash
QT_VERSION=$(uv run python -c "from PySide6.QtCore import qVersion; print(qVersion())")
uvx --from aqtinstall aqt install-qt linux desktop "$QT_VERSION" gcc_64 \
    --outputdir ~/Qt --archives qtbase qtdeclarative
uv run python scripts/build_video_bridge.py --qt ~/Qt/$QT_VERSION/gcc_64
```
Expected: `built .../libgravitas_video_bridge_vk.so`. If the QRhi header path differs in this Qt build, adjust the `rhi_root` line until `qrhi.h` is found — that is the one path most likely to vary between aqt layouts.

- [ ] **Step 5: Verify the loader accepts it**

Run: `uv run python -c "from gravitas.presentation.video import vulkan_bridge; print(vulkan_bridge.available())"`
Expected: `True` (ABI matches, Qt version matches). If `False`, print `vulkan_bridge._load_failure` to see which gate refused it.

- [ ] **Step 6: Launch-verify clean failure**

Run: `GRAVITAS_GRAPHICS=vulkan uv run gravitas`
Expected: app launches on the **Vulkan RHI** now (bridge available → RHI committed). The UI renders (QML on Vulkan). Playing a video shows black with a logged `create` failure `"not implemented"` — expected at this milestone; the app must not crash.

- [ ] **Step 7: Commit**

```bash
git add native/linux/ scripts/build_video_bridge.py
git commit -m "feat(video): Linux Vulkan bridge C ABI, skeleton, and build path"
```

---

## Task 6: `create()` — Qt handles, device UUID match, EGL context, mpv render context

**Files:**
- Modify: `native/linux/gravitas_video_bridge_vk.cpp`

**Risks addressed:** R3 (device selection), R4 (EGL surfaceless + UUID match).

Implement `gv_video_bridge_vk_create(window, mpv)` on the render thread. Read `native/macos/gravitas_video_bridge.mm`'s create path for the mpv-context and error-handling shape; the Vulkan/EGL parts are new. Sequence:

1. Cast `window` to `QQuickWindow*`; `QSGRendererInterface *rif = window->rendererInterface()`. Assert `rif->graphicsApi() == QSGRendererInterface::Vulkan`.
2. Pull native handles via `rif->getResource(window, QSGRendererInterface::Resource)`: `VkInstance` (`VulkanInstanceResource` → `QVulkanInstance*` then `->vkInstance()`), `PhysicalDeviceResource`, `DeviceResource`, `CommandQueueResource` (graphics `VkQueue`), `GraphicsQueueFamilyIndexResource`. Any null → set error, return `nullptr`.
3. `vkGetPhysicalDeviceProperties2` with `VkPhysicalDeviceIDProperties` chained → capture `deviceUUID[VK_UUID_SIZE]`.
4. Bring up EGL: `eglQueryDevicesEXT` to enumerate `EGLDeviceEXT`; for each, create a display and query `GL_DEVICE_UUID_EXT` (via a temporary context, or `EGL_DRM_DEVICE_FILE_EXT` UUID path) and pick the device whose UUID equals the Vulkan `deviceUUID`. Create a surfaceless context: `eglBindAPI(EGL_OPENGL_API)`, `eglCreateContext` with no config surface, `EGL_KHR_surfaceless_context`. Store display+context in the bridge struct. No UUID match → set error `"no EGL device matches the Vulkan GPU"`, return `nullptr`.
5. `eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)`; load the GL external-object entry points (`glCreateMemoryObjectsEXT`, `glImportMemoryFdEXT`, `glTextureStorageMem2DEXT`, `glGenSemaphoresEXT`, `glImportSemaphoreFdEXT`, `glSignalSemaphoreEXT`, `glWaitSemaphoreEXT`, `glTextureParameteri` tiling) via `eglGetProcAddress`; fail if any is missing.
6. Create the mpv render context exactly as the macOS bridge does but with `get_proc_address = eglGetProcAddress`: `mpv_render_context_create` with `MPV_RENDER_PARAM_API_TYPE = "opengl"` and `MPV_RENDER_PARAM_OPENGL_INIT_PARAMS`.
7. Store `VkInstance/PhysicalDevice/Device/Queue/queueFamily`, the EGL display/context, and the mpv context in `GvVideoBridgeVk`. Return it.

- [ ] **Step 1: Implement `create` per the sequence above**
- [ ] **Step 2: Build**: `uv run python scripts/build_video_bridge.py --qt ~/Qt/$QT_VERSION/gcc_64`. Expected: builds clean (`-Wall -Wextra` quiet).
- [ ] **Step 3: Launch-verify create succeeds**: `GRAVITAS_GRAPHICS=vulkan uv run gravitas`, play a video. Expected log: device-UUID match found, EGL surfaceless context created, mpv render context created. Video is still black (no surface yet — Task 7), but `create` no longer fails. No crash on play, on stop, or on quit.
- [ ] **Step 4: Commit**: `git commit -am "feat(video): Vulkan bridge create — device match, EGL, mpv context"`

---

## Task 7: `set_size()` + `texture()` — exportable image ring, GL import, first frame

**Files:**
- Modify: `native/linux/gravitas_video_bridge_vk.cpp`

**Risks addressed:** R6 (mpv rendering into imported-memory GL texture), plus the QSGTexture wrap. This task gets **one static frame on screen**.

Implement `gv_video_bridge_vk_set_size(bridge, w, h)`, `gv_video_bridge_vk_texture(bridge)`, `gv_video_bridge_vk_format(bridge)`, and a minimal `gv_video_bridge_vk_render(bridge)` (no cross-API sync yet — just render mpv and `glFinish` so a frame is guaranteed present before Qt samples; correct sync arrives in Task 8).

`set_size`, on the render thread, for each of `N=3` ring slots:
1. Create an exportable `VkImage`: `VK_FORMAT_R8G8B8A8_UNORM`, `VK_IMAGE_TILING_OPTIMAL`, usage `SAMPLED | COLOR_ATTACHMENT | TRANSFER_DST`, with `VkExternalMemoryImageCreateInfo{VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT}`.
2. Allocate dedicated exportable `VkDeviceMemory` (`VkExportMemoryAllocateInfo{OPAQUE_FD_BIT}` + `VkMemoryDedicatedAllocateInfo`); bind. Export the memory FD with `vkGetMemoryFdKHR`.
3. GL import: `glCreateMemoryObjectsEXT` → `glImportMemoryFdEXT(memObj, size, GL_HANDLE_TYPE_OPAQUE_FD_EXT, fd)` (this consumes the fd) → create a GL texture, set `glTextureParameteri(tex, GL_TEXTURE_TILING_EXT, GL_OPTIMAL_TILING_EXT)` → `glTextureStorageMem2DEXT(tex, 1, GL_RGBA8, w, h, memObj, 0)`. Wrap in a GL FBO (`glNamedFramebufferTexture`).
4. Wrap the `VkImage` as a `QSGTexture`: build a `QRhiTexture` with `QRhi::newTexture(QRhiTexture::RGBA8, {w,h}, ...)` then `QRhiTexture::createFrom({quint64(vkImage), VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL})` (does **not** retain the image), then `QQuickWindow::createTextureFromRhiTexture(rhiTex)` (**takes ownership** of the QRhiTexture). Obtain the `QRhi*` via `rif->getResource(window, QSGRendererInterface::RhiResource)`.
5. Retire the previous ring's surfaces into a `std::vector` kept until teardown — **never free here** (Qt's batch renderer samples a retired texture for unbounded frames; the macOS crash). Store the new `QSGTexture*` per slot; `texture()` returns the current slot's.
6. Set the format string (`"rgba8"`) for `format()`.

`render()` (minimal this task): `eglMakeCurrent`; `mpv_render_context_render` into the current slot's GL FBO with `opengl_fbo = {fbo, w, h}` and `block_for_target_time = 0`; `glFinish()`; advance the slot; return 1.

- [ ] **Step 1: Implement `set_size`, `texture`, `format`, minimal `render`**
- [ ] **Step 2: Build**: `uv run python scripts/build_video_bridge.py --qt ~/Qt/$QT_VERSION/gcc_64`. Expected: clean build.
- [ ] **Step 3: Launch-verify a frame appears**: `GRAVITAS_GRAPHICS=vulkan uv run gravitas`, play a video, let it reach a frame, then pause. Expected: the paused frame is visible and correct (right colors — if red/blue swapped, the Vulkan format vs GL format disagree; if garbage/striped, the tiling flags disagree). Log shows `zero-copy video surface at WxH, rgba8`. Motion may stutter or tear — that is Task 8.
- [ ] **Step 4: Commit**: `git commit -am "feat(video): Vulkan bridge surface ring, GL import, QSGTexture wrap"`

---

## Task 8: `render()` — the cross-API sync protocol

**Files:**
- Modify: `native/linux/gravitas_video_bridge_vk.cpp`

**Risks addressed:** R1 (reverse-sync / tearing), R2 (injecting the wait into Qt's queue). This task makes **motion play without tearing**, and removes the Task-7 `glFinish` stall.

Replace the minimal `render()` with the full protocol from the spec's "render" section. In `create` (extend it) allocate, per ring slot, one exportable binary `VkSemaphore` "gl_done" and one "vk_done", export FDs, and import them into GL (`glGenSemaphoresEXT` + `glImportSemaphoreFdEXT`), plus a per-slot `VkFence`.

Per `render()` call, on the render thread:
1. `eglMakeCurrent`.
2. `glWaitSemaphoreEXT(vk_done[slot], ...)` with the slot's texture in the wait list and `GL_LAYOUT_SHADER_READ_ONLY_EXT` — GL must not overwrite a buffer Qt may still sample.
3. `mpv_render_context_render` into the slot's FBO, `block_for_target_time = 0` (no `glFinish` now).
4. `glSignalSemaphoreEXT(gl_done[slot], ...)` with the texture transitioned to `GL_LAYOUT_SHADER_READ_ONLY_EXT`; `glFlush()`.
5. `vkQueueSubmit` on Qt's graphics `VkQueue`: `pWaitSemaphores=[gl_done[slot]]`, `pWaitDstStageMask=VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT`, `pSignalSemaphores=[vk_done[slot]]`, a command buffer doing the `UNDEFINED→SHADER_READ_ONLY_OPTIMAL` layout barrier, signalling the slot's `VkFence`. Because Qt composites on the same queue right after `updatePaintNode`, this orders Qt's sampling after mpv's write, and re-arms `vk_done` for step 2's next cycle.
6. Advance the ring; return 1.

**If R2 proves false** (Qt does not composite on the same `VkQueue`, or overlaps submits): switch the wait-injection to `QQuickWindow::beforeRendering`/`afterRenderPassRecording` connected in `create`, submitting the wait into Qt's frame there instead. Note in a comment which path was used and why.

- [ ] **Step 1: Extend `create` to allocate per-slot semaphores + fences; implement the full `render`**
- [ ] **Step 2: Build**: clean build.
- [ ] **Step 3: Launch-verify motion**: play video with fast motion (panning/action). Expected: smooth, no tearing, no horizontal split. Watch the log for Vulkan validation errors if `VK_LAYER_KHRONOS_validation` is installed (`export VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation` for one run) — zero validation errors is the bar. Seek a few times; confirm no freeze.
- [ ] **Step 4: Commit**: `git commit -am "feat(video): Vulkan bridge cross-API semaphore sync, no tearing"`

---

## Task 9: Teardown, `stale()`, `destroy()`

**Files:**
- Modify: `native/linux/gravitas_video_bridge_vk.cpp`

**Risks addressed:** the macOS lifetime rules — render-thread teardown, GIL-free, retire-not-free, scene-graph recreation on fullscreen toggle.

Implement, mirroring `native/macos/gravitas_video_bridge.mm`'s teardown:
1. In `create`, connect (with a C++ lambda/slot, `Qt::DirectConnection` on the render thread) `QQuickWindow::sceneGraphInvalidated` to a teardown that frees the mpv render context (detach `update_cb` first), all `VkImage`/`VkDeviceMemory`/`VkSemaphore`/`VkFence`/`QSGTexture` including the retired list, the GL objects, and the EGL context — all on the render thread. Set an internal `stale` flag.
2. `gv_video_bridge_vk_stale(bridge)` returns 1 once that flag is set (a fullscreen toggle recreates the scene graph). The Python `_bridge()` already drops and rebuilds on stale (Task 3) — no "stopped" latch, per the macOS lesson.
3. `gv_video_bridge_vk_destroy(bridge)` does the same teardown idempotently (safe to call twice); it is the early-release path, normally unused.
4. `gv_video_bridge_vk_set_item(bridge, item)` stores the item pointer; the mpv `update_cb` invokes the item's `requestUpdate` named slot on the GUI thread (`QMetaObject::invokeMethod(item, "requestUpdate", Qt::QueuedConnection)`), exactly as macOS does.

- [ ] **Step 1: Implement teardown, `stale`, `destroy`, `set_item`**
- [ ] **Step 2: Build**: clean build.
- [ ] **Step 3: Launch-verify lifetime**: (a) play → toggle fullscreen → video keeps playing (bridge rebuilt on the new scene graph, log shows a new bridge); (b) play → stop → play a second file → it renders (one render context per core reused); (c) quit during playback → clean exit, no segfault, no libmpv teardown crash. Run each two or three times.
- [ ] **Step 4: Commit**: `git commit -am "feat(video): Vulkan bridge render-thread teardown and staleness"`

---

## Task 10: Packaging and CI

**Files:**
- Modify: `packaging/gravitas.spec`
- Modify: `.github/workflows/release.yml` (and `.github/workflows/ci.yml` for the build check)

**Interfaces:**
- Consumes: the build script's Linux path (Task 5), the `.so` output location.

- [ ] **Step 1: Bundle the `.so` in the PyInstaller spec**

In `packaging/gravitas.spec`, next to the existing macOS `.dylib` block (lines ~240-250), add a Linux sibling:

```python
elif sys.platform.startswith("linux"):
    _bridge = ROOT / "src/gravitas/presentation/video/libgravitas_video_bridge_vk.so"
    if _bridge.is_file():
        binaries.append((str(_bridge), "gravitas/presentation/video"))
    else:
        print("note: no Vulkan video bridge built; Linux video stays on OpenGL")
```

Add `gravitas.presentation.video.mpv_vulkan_item` to `hiddenimports` (line ~308).

- [ ] **Step 2: Build the bridge in the Linux release job**

In `.github/workflows/release.yml`, in the `linux` job (before "Build one-dir bundle", ~line 44), add steps mirroring the macOS job's "Install Qt headers"/"Build the video bridge" (lines ~71-83), using `aqt install-qt linux desktop "$QT_VERSION" gcc_64 --archives qtbase qtdeclarative` and `scripts/build_video_bridge.py --qt "$RUNNER_TEMP/Qt/$QT_VERSION/gcc_64"`. Install the build deps first: `sudo apt-get install -y g++ libegl1-mesa-dev libgl1-mesa-dev libvulkan-dev`.

Add an assertion step after the bundle build (mirror the macOS "Check what the bundle got", ~line 86) that greps `dist/Gravitas/` for `libgravitas_video_bridge_vk.so` and warns (does not fail — the bridge is optional) if absent.

- [ ] **Step 3: Build + loader test in CI**

In `.github/workflows/ci.yml`, add a job (or steps in the existing Linux job) that installs the build deps + Qt headers, runs `scripts/build_video_bridge.py`, then `uv run pytest tests/presentation/test_vulkan_bridge.py tests/presentation/test_mpv_vulkan_item.py tests/infrastructure/test_graphics.py -q`. Document in a comment that the interop itself is launch-verified on hardware, not in CI (no GPU; `llvmpipe` cannot drive opaque-FD), the same coverage class as QML.

- [ ] **Step 4: Verify the release build locally if possible**

Run: `uv run pyinstaller packaging/gravitas.spec` (after building the `.so`). Expected: `dist/Gravitas/` contains `libgravitas_video_bridge_vk.so` under `gravitas/presentation/video/`.

- [ ] **Step 5: Commit**

```bash
git add packaging/gravitas.spec .github/workflows/release.yml .github/workflows/ci.yml
git commit -m "build(video): bundle and CI-build the Linux Vulkan video bridge"
```

---

## Self-Review

**Spec coverage:**
- Why / opt-in / no-regression → Tasks 1, 4 (predicate, selector, fallback launch-verify).
- C ABI → Task 5. create/device-match/EGL/mpv → Task 6. set_size/ring/QSGTexture → Task 7. render/sync → Task 8. lifetime/stale/teardown → Task 9.
- Loader (ABI + Qt refuse, fallback) → Task 2. Item → Task 3. main.py wiring order (availability before setGraphicsApi) → Task 4.
- Build script → Task 5. Packaging → Task 10. CI + launch-verified gap → Task 10.
- Tests (loader, graphics truth table, geometry) → Tasks 1–3; mypy on all → every task's gate step.
- Swappable interop seam → the Task 6–8 code is structured as Qt-shell + opaque-FD backend; the universal Mesa/DMA-BUF phase is explicitly a later spec (out of scope here), so no task — correct.
- Risks R1–R6 → mapped onto Tasks 6 (R3,R4), 7 (R6), 8 (R1,R2); R5 (Qt pin) is enforced by Task 2's loader + Task 5's exact-version build.

**Placeholder scan:** no TBD/TODO; native tasks carry concrete API sequences and explicit manual verification rather than fabricated line-by-line code, because GPU interop bring-up is iterative and untestable headless — this is stated, not hidden.

**Type/name consistency:** symbol prefix `gv_video_bridge_vk_*` and `_ABI` used identically in the header (Task 5), loader `_bind` (Task 2), item calls (Task 3), and `_FakeBridge` (Task 3 tests). `choose_video_backend` return strings match the `main.py` branch labels (Task 4). `MpvVulkanVideoItem` name consistent across Tasks 3 and 4.
