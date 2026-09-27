"""The native engine's Vulkan bridge, where it can be checked without a GPU:
adopting a device is refused rather than attempted without what it needs."""

from __future__ import annotations

import pytest

from gravitas.presentation.video import native_vk_bridge

HANDLES = {"physical_device": 1, "device": 2, "queue_family": 0, "queue_index": 0}


def test_no_window_is_refused(qapp: object) -> None:
    assert native_vk_bridge.adopt(0, HANDLES) is False


def test_without_the_library_nothing_is_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_vk_bridge, "library", lambda: None)
    assert native_vk_bridge.instance() is None
    assert native_vk_bridge.adopt(1234, HANDLES) is False
