"""Which scene-graph backend each platform gets, and what video pays for it."""

from __future__ import annotations

from gravitas.infrastructure.graphics import (
    metal_scene_graph,
    video_needs_system_memory,
    vulkan_scene_graph,
)

LINUX = "linux"
MACOS = "darwin"
WINDOWS = "win32"


def test_macos_defaults_to_metal() -> None:
    # The threaded render loop is worth more than the zero-copy video path:
    # OpenGL there means the basic loop and hitching animations.
    assert metal_scene_graph({}, MACOS) is True


def test_macos_can_be_sent_back_to_opengl() -> None:
    assert metal_scene_graph({"GRAVITAS_GRAPHICS": "opengl"}, MACOS) is False
    # Spelling is forgiving; anything else means the default.
    assert metal_scene_graph({"GRAVITAS_GRAPHICS": " OpenGL "}, MACOS) is False
    assert metal_scene_graph({"GRAVITAS_GRAPHICS": "metal"}, MACOS) is True
    assert metal_scene_graph({"GRAVITAS_GRAPHICS": "nonsense"}, MACOS) is True


def test_other_platforms_are_always_opengl() -> None:
    # Not a preference there: they already have the threaded render loop, and
    # OpenGL is the only backend the zero-copy video item can render into.
    for platform in (LINUX, WINDOWS):
        assert metal_scene_graph({}, platform) is False
        assert metal_scene_graph({"GRAVITAS_GRAPHICS": "metal"}, platform) is False


def test_video_copies_back_on_metal_and_on_windows() -> None:
    assert video_needs_system_memory({}, MACOS) is True
    assert video_needs_system_memory({}, WINDOWS) is True
    # ...and on Windows regardless of a variable that does not apply there.
    assert video_needs_system_memory({"GRAVITAS_GRAPHICS": "opengl"}, WINDOWS) is True


def test_video_stays_zero_copy_on_linux_and_on_macos_opengl() -> None:
    assert video_needs_system_memory({}, LINUX) is False
    assert video_needs_system_memory({"GRAVITAS_GRAPHICS": "opengl"}, MACOS) is False


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
