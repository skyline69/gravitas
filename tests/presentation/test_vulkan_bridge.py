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


class _ProbingLibrary:
    """A loadable, Qt-matching bridge whose driver probe answers as told."""

    def __init__(self, works: bool) -> None:
        self._works = works

    def gv_video_bridge_vk_abi(self) -> int:
        return vulkan_bridge._ABI

    def gv_video_bridge_vk_qt_version(self) -> bytes:
        from PySide6.QtCore import qVersion

        return qVersion().encode()

    def gv_video_bridge_vk_probe(self) -> int:
        return 1 if self._works else 0

    def gv_video_bridge_vk_error(self) -> bytes:
        return b"GL memory import failed (0x505)"


def _install(monkeypatch: pytest.MonkeyPatch, library: object) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vulkan_bridge, "_library", None)
    monkeypatch.setattr(vulkan_bridge.Path, "is_file", lambda _self: True)
    monkeypatch.setattr(vulkan_bridge, "_bind", lambda _lib: library)
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: object())


def test_interop_is_supported_when_the_driver_does_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _ProbingLibrary(works=True))
    assert vulkan_bridge.interop_supported() is True


def test_a_driver_that_advertises_but_refuses_is_not_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mesa's llvmpipe loads fine, matches Qt, offers every extension the bridge
    # needs, and then fails the import. Loading is not the same question.
    _install(monkeypatch, _ProbingLibrary(works=False))
    assert vulkan_bridge.available() is True
    assert vulkan_bridge.interop_supported() is False


def test_interop_is_unsupported_when_the_library_will_not_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(vulkan_bridge, "_library", None)
    assert vulkan_bridge.interop_supported() is False
