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
