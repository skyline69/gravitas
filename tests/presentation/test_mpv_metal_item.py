"""The zero-copy video item's geometry, and the loader's refusal rules.

The rendering itself needs a Metal scene graph and the native bridge, so it is
verified by running the app; what is testable here is the maths and the
guards that decide whether the bridge is touched at all.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from gravitas.presentation.video import metal_bridge
from gravitas.presentation.video.mpv_metal_item import fitted_rect


def test_video_fills_an_item_of_the_same_aspect() -> None:
    rect = fitted_rect(1920, 1080, 1920, 1080)
    assert (rect.x(), rect.y()) == (0, 0)
    assert (rect.width(), rect.height()) == (1920, 1080)


def test_wider_item_pillarboxes() -> None:
    rect = fitted_rect(2000, 1000, 1920, 1080)
    assert (rect.width(), rect.height()) == (1777.7777777777778, 1000)
    assert rect.x() == (2000 - rect.width()) / 2
    assert rect.y() == 0


def test_taller_item_letterboxes() -> None:
    rect = fitted_rect(1920, 1200, 1920, 1080)
    assert (rect.width(), rect.height()) == (1920, 1080)
    assert (rect.x(), rect.y()) == (0, 60)


def test_a_small_item_scales_the_video_down_to_fit() -> None:
    rect = fitted_rect(640, 480, 1920, 1080)
    assert (rect.width(), rect.height()) == (640, 360)
    assert rect.y() == 60


def test_degenerate_sizes_do_not_divide_by_zero() -> None:
    # No file loaded yet: mpv reports no size, and the item may be unsized
    # before its first layout.
    assert fitted_rect(800, 600, 0, 0).width() == 800
    assert fitted_rect(0, 0, 1920, 1080).width() == 0


@pytest.fixture(autouse=True)
def _forget_cached_load() -> None:
    """The loader caches its verdict for the process; these tests each want
    their own."""
    metal_bridge._library = None
    metal_bridge._load_failure = None


def test_loader_refuses_anything_but_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(metal_bridge.BridgeUnavailable, match="not macOS"):
        metal_bridge.library()
    assert metal_bridge.available() is False


def test_loader_refuses_a_bridge_built_for_another_qt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole reason PySide6 is pinned: a bridge built against a different
    Qt would not fail cleanly, it would crash on a frame."""

    class FakeLibrary:
        def gv_video_bridge_abi(self) -> int:
            return metal_bridge._ABI

        def gv_video_bridge_qt_version(self) -> bytes:
            return b"6.11.0"

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(metal_bridge.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(metal_bridge, "_bind", lambda _lib: FakeLibrary())
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: object())

    with pytest.raises(metal_bridge.BridgeUnavailable, match=r"built against Qt 6\.11\.0"):
        metal_bridge.library()


def test_loader_refuses_a_stale_abi(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import qVersion

    class FakeLibrary:
        def gv_video_bridge_abi(self) -> int:
            return metal_bridge._ABI - 1

        def gv_video_bridge_qt_version(self) -> bytes:
            return qVersion().encode()

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(metal_bridge.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(metal_bridge, "_bind", lambda _lib: FakeLibrary())
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: object())

    with pytest.raises(metal_bridge.BridgeUnavailable, match="rebuild the bridge"):
        metal_bridge.library()


def test_a_missing_library_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(metal_bridge.Path, "is_file", lambda _self: False)
    assert metal_bridge.available() is False


class _FakeBridge:
    """Stands in for the native library: records what the item asks of it."""

    def __init__(self) -> None:
        self.created = 0

    def gv_video_bridge_create(self, _window: int, _mpv: object) -> int:
        self.created += 1
        return 0xBEEF00 + self.created

    def gv_video_bridge_stale(self, _bridge: object) -> int:
        return 1 if getattr(self, "stale", False) else 0

    def gv_video_bridge_set_item(self, _bridge: object, item: int) -> None:
        # Which item to wake when mpv has a frame; the page is rebuilt for
        # every playback, so this is re-pointed rather than recreated.
        self.item = item


def test_a_second_player_page_reuses_the_first_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression that made a second file play black.

    libmpv allows one render context per core, and the core outlives the
    player page -- PlayerController keeps it while a page pop only stops
    playback. An item that built its own bridge would ask for a second render
    context and get "There is already a mpv_render_context set", decoding
    fine and drawing nothing.
    """
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_metal_item

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_metal_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_metal_item, "_BRIDGES", {})
    monkeypatch.setattr(mpv_metal_item, "mpv_pointer", lambda _handle: 0x1234)

    # A bridge is keyed by the window's pointer and nothing else is read off
    # it here, so any QObject stands in -- and a real QQuickWindow needs a GUI
    # application this suite deliberately does not create.
    window = QObject()

    first = mpv_metal_item.MpvMetalVideoItem()
    first._handle = object()
    handle = first._bridge(fake, window)
    assert handle is not None

    # The page is popped and reopened: a brand new item, same window and core.
    second = mpv_metal_item.MpvMetalVideoItem()
    second._handle = first._handle
    assert second._bridge(fake, window) == handle
    assert fake.created == 1, "the second page must not build its own bridge"


def test_an_unusable_mpv_handle_does_not_reach_the_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bridge dereferences the pointer it is given, so a handle that
    cannot produce one must stop here rather than there."""
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_metal_item

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_metal_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_metal_item, "_BRIDGES", {})

    item = mpv_metal_item.MpvMetalVideoItem()
    item._handle = object()  # not an mpv instance: no .handle to cast
    assert item._bridge(fake, QObject()) is None
    assert fake.created == 0
    assert item._failed is True


def test_mpv_pointer_survives_a_handle_that_is_not_mpv() -> None:
    from gravitas.presentation.video.mpv_metal_item import mpv_pointer

    assert mpv_pointer(object()) == 0
    assert mpv_pointer(None) == 0


def test_a_recreated_scene_graph_gets_a_new_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Going fullscreen recreates the window's scene graph, and every texture
    the old bridge made belongs to a renderer that no longer exists. Drawing
    through it crashed the renderer; the item must build a new one."""
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_metal_item

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_metal_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_metal_item, "_BRIDGES", {})
    monkeypatch.setattr(mpv_metal_item, "mpv_pointer", lambda _handle: 0x1234)

    window = QObject()
    item = mpv_metal_item.MpvMetalVideoItem()
    item._handle = object()
    first = item._bridge(fake, window)
    assert fake.created == 1

    # Same window, but its scene graph has been torn down and rebuilt.
    fake.stale = True
    second = item._bridge(fake, window)
    assert second != first
    assert fake.created == 2
    assert item._size == (0, 0), "the new bridge has no surface yet"
