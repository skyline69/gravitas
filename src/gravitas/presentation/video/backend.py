"""Which video item the composition root registers, decided without Qt.

Pulled out of main.py so the choice -- and the no-regression guarantee that a
failed Vulkan opt-in falls back to OpenGL rather than a slower path -- is unit
tested. Capability (`*_available`) is resolved by the caller; this is pure.
"""

from __future__ import annotations


def choose_video_backend(
    want_vulkan: bool,
    vulkan_available: bool,
    want_metal: bool,
    metal_available: bool,
) -> str:
    if want_vulkan and vulkan_available:
        return "vulkan"
    if want_metal:
        return "metal-zero-copy" if metal_available else "metal-software"
    return "opengl"
