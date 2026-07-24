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
