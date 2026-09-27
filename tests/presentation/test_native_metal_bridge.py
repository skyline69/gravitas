"""The native engine's Metal bridge, where it can be checked without a scene
graph: nothing is offered without the library, and a null window is refused
rather than dereferenced."""

from __future__ import annotations

import sys

import pytest

from gravitas.presentation.video import native_metal_bridge


def test_without_the_library_nothing_is_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_metal_bridge, "library", lambda: None)
    assert native_metal_bridge.for_window(1234) is None
    assert native_metal_bridge.last_error() == ""


@pytest.mark.skipif(sys.platform != "darwin", reason="the Metal bridge is macOS-only")
def test_no_window_is_refused(qapp: object) -> None:
    if native_metal_bridge.library() is None:
        pytest.skip("the video bridge is not built")
    assert native_metal_bridge.for_window(0) is None
    assert native_metal_bridge.last_error() == "null window"
