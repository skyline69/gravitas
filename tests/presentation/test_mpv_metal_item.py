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
