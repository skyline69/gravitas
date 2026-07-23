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
        self.destroyed = 0

    def gv_video_bridge_create(self, _window: int) -> int:
        self.created += 1
        return 0xBEEF00 + self.created

    def gv_video_bridge_destroy(self, _handle: object) -> None:
        self.destroyed += 1

    def gv_video_bridge_begin(self, _handle: object) -> int:
        # Freeing a render context needs mpv's GL context current, so the item
        # brackets it the same way it brackets a frame.
        self.made_current = getattr(self, "made_current", 0) + 1
        return 1

    def gv_video_bridge_end(self, _handle: object) -> None:
        self.released_current = getattr(self, "released_current", 0) + 1


def test_a_second_player_page_reuses_the_first_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression that made a second file play black.

    libmpv allows one render context per core, and the core outlives the
    player page. An item that builds its own session would make the second
    playback fail with "There is already a mpv_render_context set".
    """
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_metal_item

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_metal_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_metal_item, "_SESSIONS", {})

    # A session is keyed by the window's pointer and nothing else is read off
    # it here, so any QObject stands in -- and a real QQuickWindow needs a GUI
    # application this suite deliberately does not create.
    window = QObject()
    handle = object()  # the one mpv core, as PlayerController keeps it

    first = mpv_metal_item.MpvMetalVideoItem()
    first._handle = handle
    session = first._session(fake, window)
    assert session is not None

    # The page is popped and reopened: a brand new item, same window, same core.
    second = mpv_metal_item.MpvMetalVideoItem()
    second._handle = handle
    assert second._session(fake, window) is session
    assert fake.created == 1, "the second page must not build its own bridge"
    assert fake.destroyed == 0, "nothing may be freed while the window lives"


def test_a_rebuilt_mpv_core_drops_the_stale_render_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A render context belongs to the core it was made for. If the player is
    rebuilt, reusing it would render frames from a core that no longer runs."""
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_metal_item

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_metal_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_metal_item, "_SESSIONS", {})

    window = QObject()
    item = mpv_metal_item.MpvMetalVideoItem()
    item._handle = object()
    session = item._session(fake, window)
    assert session is not None

    freed: list[bool] = []
    session.ctx = type("Ctx", (), {"update_cb": None, "free": lambda _self: freed.append(True)})()

    replacement = mpv_metal_item.MpvMetalVideoItem()
    replacement._handle = object()  # a different core
    assert replacement._session(fake, window) is session
    assert session.ctx is None, "the old core's render context must be dropped"
    assert freed == [True]
    # ...and it was freed with mpv's GL context current, then released again.
    assert fake.made_current == 1
    assert fake.released_current == 1


def test_reusing_a_session_never_reinstalls_the_mpv_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assigning update_cb builds a new ctypes closure and drops the old one.

    mpv's VO thread calls that closure from outside Python, so replacing it
    while frames are flowing is a use-after-free -- it crashed a second into
    playback, in _CallPythonObject on mpv's vo thread. The session installs one
    callback at creation and re-points a plain attribute instead.
    """
    from PySide6.QtCore import QObject

    from gravitas.presentation.video import mpv_metal_item

    class Context:
        def __init__(self) -> None:
            self.assignments = 0
            self._cb = None

        @property
        def update_cb(self) -> object:
            return self._cb

        @update_cb.setter
        def update_cb(self, value: object) -> None:
            self.assignments += 1
            self._cb = value

    fake = _FakeBridge()
    monkeypatch.setattr(mpv_metal_item, "library", lambda: fake)
    monkeypatch.setattr(mpv_metal_item, "_SESSIONS", {})

    window = QObject()
    first = mpv_metal_item.MpvMetalVideoItem()
    first._handle = object()
    session = first._session(fake, window)
    assert session is not None
    session.ctx = Context()
    session.ctx.update_cb = session.notify  # what context creation does, once
    assert session.ctx.assignments == 1

    # Many frames, then a new item takes over, then many more frames.
    for _ in range(5):
        assert first._ensure_render_context(fake, session) is True
    second = mpv_metal_item.MpvMetalVideoItem()
    second._handle = first._handle
    for _ in range(5):
        assert second._ensure_render_context(fake, session) is True

    assert session.ctx.assignments == 1, "the callback must be installed exactly once"
    assert session._owner is not None and session._owner() is second


def test_frame_notifications_follow_the_item_on_screen() -> None:
    from gravitas.presentation.video import mpv_metal_item

    woken: list[str] = []

    class Item:
        def __init__(self, name: str) -> None:
            self.name = name

        def scheduleUpdate(self) -> None:
            woken.append(self.name)

    session = mpv_metal_item._Session(1, object())
    session.notify()  # nobody owns it yet
    assert woken == []

    first = Item("first")
    session.set_owner(first)
    session.notify()
    second = Item("second")
    session.set_owner(second)
    session.notify()
    assert woken == ["first", "second"]

    # The owning item is held weakly: a destroyed page must not keep it alive,
    # and a notification arriving after it is gone must be a no-op.
    del second
    session.notify()
    assert woken == ["first", "second"]
