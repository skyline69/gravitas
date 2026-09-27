"""The native engine's video item: when it asks for frames, and how it fails."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEventLoop, QRectF, QTimer
from PySide6.QtQuick import QQuickWindow

from gravitas.presentation.video.native_item import NativeVideoItem


class StubSource:
    def __init__(self) -> None:
        self.wanting = True
        self.size: tuple[int, int] | None = (1920, 1080)
        self.renders: list[tuple[int, int, int]] = []
        self.fail = False

    def video_size(self) -> tuple[int, int] | None:
        return self.size

    def wants_frames(self) -> bool:
        if self.fail:
            raise RuntimeError("the player has been shut down")
        return self.wanting

    def render(self, buffer: memoryview, width: int, height: int, stride: int) -> bool:
        if self.fail:
            raise RuntimeError("render failed")
        assert len(buffer) >= stride * height
        self.renders.append((width, height, stride))
        return False


def _spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _counting_updates(item: NativeVideoItem) -> list[int]:
    calls: list[int] = []
    item.update = lambda: calls.append(1)  # type: ignore[method-assign]
    return calls


def test_frames_are_requested_only_while_the_engine_wants_them(qapp: object) -> None:
    item = NativeVideoItem()
    source = StubSource()
    item.handle = source
    updates = _counting_updates(item)
    item._request_frame()
    assert len(updates) == 1
    source.wanting = False
    item._request_frame()
    assert len(updates) == 1


def test_an_idle_item_polls_for_the_engine_to_want_frames_again(qapp: object) -> None:
    """A resume or a paused seek makes the engine want frames while nothing
    is animating; the kick timer is what notices."""
    item = NativeVideoItem()
    source = StubSource()
    source.wanting = False
    item.handle = source
    updates = _counting_updates(item)
    source.wanting = True
    _spin(150)
    assert updates
    item.handle = None
    count = len(updates)
    _spin(150)
    assert len(updates) == count


def test_an_engine_that_is_gone_asks_for_nothing(qapp: object) -> None:
    item = NativeVideoItem()
    source = StubSource()
    item.handle = source
    updates = _counting_updates(item)
    source.fail = True
    item._request_frame()
    assert updates == []


def test_nothing_renders_without_a_window_or_a_picture(qapp: object) -> None:
    item = NativeVideoItem()
    source = StubSource()
    item.handle = source
    marker: Any = object()
    assert item.updatePaintNode(marker, None) is marker  # no window yet
    window = QQuickWindow()
    item.setParentItem(window.contentItem())
    item.setSize(window.contentItem().size())
    source.size = None  # nothing decoded yet
    assert item.updatePaintNode(marker, None) is marker
    assert source.renders == []


def test_a_failed_render_disables_the_item_instead_of_raising(qapp: object) -> None:
    item = NativeVideoItem()
    source = StubSource()
    item.handle = source
    window = QQuickWindow()
    window.resize(640, 360)
    item.setParentItem(window.contentItem())
    item.setSize(window.contentItem().size())
    source.fail = True
    assert item.updatePaintNode(None, None) is None
    assert item._failed
    # A new handle (the next playback) gets a fresh chance.
    item.handle = StubSource()
    assert not item._failed


def test_the_buffer_follows_the_letterboxed_size(qapp: object) -> None:
    item = NativeVideoItem()
    source = StubSource()
    item.handle = source
    window = QQuickWindow()
    item.setParentItem(window.contentItem())
    item.setSize(QRectF(0, 0, 960, 540).size())
    item.updatePaintNode(None, None)
    width, height, stride = source.renders[-1]
    assert (width, height, stride) == (960, 540, 960 * 4)


def test_resizing_latches_like_the_mpv_item(qapp: object) -> None:
    item = NativeVideoItem()
    item.geometryChange(QRectF(0, 0, 640, 360), QRectF(0, 0, 480, 270))
    assert item._resizing is True
    _spin(400)
    assert item._resizing is False


class SharedSource(StubSource):
    """An engine that can also render zero-copy."""

    def __init__(self) -> None:
        super().__init__()
        self.shared_calls: list[bool] = []
        self.shared_error: Exception | None = None

    def attach_vulkan(self, **_device: int) -> tuple[int, int]:
        return (1, 2)

    def render_shared(self, again: bool = False) -> tuple[int, int, int] | None:
        self.shared_calls.append(again)
        if self.shared_error is not None:
            raise self.shared_error
        return (0xDEAD, 1920, 1080)


class FakeBridge:
    def __init__(self, texture: int = 0) -> None:
        self._texture = texture

    def texture(self, image: int, width: int, height: int) -> int:
        return self._texture


def _shown(item: NativeVideoItem) -> QQuickWindow:
    window = QQuickWindow()
    item.setParentItem(window.contentItem())
    item.setSize(QRectF(0, 0, 640, 360).size())
    return window


def test_off_vulkan_the_item_copies_frames(qapp: object) -> None:
    item = NativeVideoItem()
    source = SharedSource()
    item.handle = source
    _window = _shown(item)
    item.updatePaintNode(None, None)
    assert item._zero_copy is False  # the offscreen scene graph is not Vulkan
    assert source.shared_calls == []
    assert source.renders, "the frame went through the readback path"


def test_a_zero_copy_failure_falls_back_to_copying(qapp: object) -> None:
    item = NativeVideoItem()
    source = SharedSource()
    item.handle = source
    _window = _shown(item)
    item._attach = lambda window, src: FakeBridge()  # type: ignore[method-assign]
    item._zero_copy = True
    source.shared_error = RuntimeError("libplacebo could not render")
    item.updatePaintNode(None, None)
    assert item._zero_copy is False
    assert source.renders, "the same frame is drawn the other way"


def test_a_texture_the_bridge_cannot_wrap_falls_back_too(qapp: object) -> None:
    item = NativeVideoItem()
    source = SharedSource()
    item.handle = source
    _window = _shown(item)
    item._attach = lambda window, src: FakeBridge(texture=0)  # type: ignore[method-assign]
    item._zero_copy = True
    item.updatePaintNode(None, None)
    # A new item asks for the image even if nothing new is due.
    assert source.shared_calls == [True]
    assert item._zero_copy is False
    assert source.renders


class AttachingBridge(FakeBridge):
    def __init__(self, fail: bool = False) -> None:
        super().__init__(texture=0)
        self.engine = 0
        self.attached: list[object] = []
        self.fail = fail

    def attach(self, source: object) -> None:
        if self.fail:
            raise RuntimeError("the renderer could not start on Qt's device")
        self.attached.append(source)


class FakeBackend:
    def __init__(self, bridge: AttachingBridge) -> None:
        self.bridge = bridge

    def library(self) -> object:
        return object()

    def last_error(self) -> str:
        return ""

    def for_window(self, window_pointer: int) -> AttachingBridge:
        return self.bridge


def test_an_engine_is_attached_to_the_window_once(qapp: object) -> None:
    item = NativeVideoItem()
    source = SharedSource()
    window = _shown(item)
    bridge = AttachingBridge()
    item._backend = FakeBackend(bridge)
    item._zero_copy = True
    assert item._attach(window, source) is bridge
    assert item._attach(window, source) is bridge
    assert bridge.attached == [source]
    # A new engine (the next playback's) is attached in its turn.
    other = SharedSource()
    item._attach(window, other)
    assert bridge.attached == [source, other]


def test_an_engine_that_cannot_attach_copies_frames(qapp: object) -> None:
    item = NativeVideoItem()
    source = SharedSource()
    window = _shown(item)
    item._backend = FakeBackend(AttachingBridge(fail=True))
    item._zero_copy = True
    assert item._attach(window, source) is None
    assert item._zero_copy is False
