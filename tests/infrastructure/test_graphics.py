"""Which scene-graph backend each platform gets, and what video pays for it."""

from __future__ import annotations

from gravitas.infrastructure.graphics import (
    hdr_mode,
    metal_scene_graph,
    native_graphics,
    native_graphics_fallback,
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


def test_vulkan_is_the_linux_default() -> None:
    assert vulkan_scene_graph({}, LINUX) is True
    assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": "vulkan"}, LINUX) is True


def test_opengl_can_be_forced_back_on_linux() -> None:
    assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": "opengl"}, LINUX) is False
    # Forgiving spelling, same as the metal switch.
    assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": " OpenGL "}, LINUX) is False


def test_an_unrecognised_graphics_value_keeps_the_default() -> None:
    # Only "opengl" means anything here; a typo must not silently downgrade.
    assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": "gl"}, LINUX) is True


def test_vulkan_is_never_selected_off_linux() -> None:
    for platform in (MACOS, WINDOWS):
        assert vulkan_scene_graph({"GRAVITAS_GRAPHICS": "vulkan"}, platform) is False


VULKAN: dict[str, str] = {}  # Vulkan is the Linux default


def test_hdr_names_the_whole_chain() -> None:
    assert hdr_mode(VULKAN | {"GRAVITAS_HDR": "hdr10"}, LINUX) == "hdr10"
    assert hdr_mode(VULKAN | {"GRAVITAS_HDR": " scRGB "}, LINUX) == "scrgb"


def test_hdr_is_off_by_default() -> None:
    assert hdr_mode(VULKAN, LINUX) is None


def test_an_unrecognised_hdr_mode_costs_the_brightness_not_the_playback() -> None:
    assert hdr_mode(VULKAN | {"GRAVITAS_HDR": "hdr"}, LINUX) is None
    assert hdr_mode(VULKAN | {"GRAVITAS_HDR": "1"}, LINUX) is None


def test_hdr_needs_the_vulkan_backend_under_it() -> None:
    # The OpenGL path renders into a scene-graph FBO it does not own, and the
    # swapchain format is only reachable through the RHI. Asking for HDR on it
    # is a contradiction, not a partial win.
    assert hdr_mode({"GRAVITAS_GRAPHICS": "opengl", "GRAVITAS_HDR": "hdr10"}, LINUX) is None


def test_hdr_is_never_selected_off_linux() -> None:
    for platform in (MACOS, WINDOWS):
        assert hdr_mode(VULKAN | {"GRAVITAS_HDR": "hdr10"}, platform) is None


def test_the_native_engine_asks_for_vulkan() -> None:
    for platform in (LINUX, WINDOWS):
        assert native_graphics({}, platform) == "vulkan"
        # A typo costs nothing: the default stands.
        assert native_graphics({"GRAVITAS_GRAPHICS": "vulcan"}, platform) == "vulkan"


def test_the_native_engine_can_be_sent_to_the_readback_path() -> None:
    assert native_graphics({"GRAVITAS_GRAPHICS": " D3D11 "}, WINDOWS) == "d3d11"
    assert native_graphics({"GRAVITAS_GRAPHICS": "opengl"}, WINDOWS) == "opengl"
    assert native_graphics({"GRAVITAS_GRAPHICS": "opengl"}, LINUX) == "opengl"
    # D3D11 is Windows' alone.
    assert native_graphics({"GRAVITAS_GRAPHICS": "d3d11"}, LINUX) == "vulkan"


def test_macos_chooses_its_scene_graph_as_for_mpv() -> None:
    assert native_graphics({"GRAVITAS_GRAPHICS": "d3d11"}, MACOS) is None


def test_without_a_vulkan_gpu_the_native_engine_falls_back_per_platform() -> None:
    assert native_graphics_fallback(WINDOWS) == "d3d11"
    assert native_graphics_fallback(LINUX) == "opengl"
