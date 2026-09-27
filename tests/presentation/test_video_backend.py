from __future__ import annotations

from gravitas.presentation.video.backend import choose_video_backend


def test_vulkan_requested_and_available_wins() -> None:
    assert choose_video_backend(True, True, False, False) == "vulkan"


def test_vulkan_requested_but_unavailable_falls_back_to_opengl() -> None:
    # The core no-regression guarantee: a failed Vulkan opt-in costs nothing.
    assert choose_video_backend(True, False, False, False) == "opengl"


def test_metal_paths_are_unaffected_by_vulkan() -> None:
    assert choose_video_backend(False, False, True, True) == "metal-zero-copy"
    assert choose_video_backend(False, False, True, False) == "metal-software"


def test_default_is_opengl() -> None:
    assert choose_video_backend(False, False, False, False) == "opengl"


def test_vulkan_never_competes_with_metal() -> None:
    # They are mutually exclusive by platform; if both somehow ask, Vulkan
    # (Linux) is checked first and Metal flags are false there anyway.
    assert choose_video_backend(True, True, True, True) == "vulkan"
