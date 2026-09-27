"""Which scene-graph backend Qt Quick runs on, and what that costs the player.

One module because two unrelated places have to agree on the answer: `main.py`
picks the RHI and the matching video item, and `mpv_player.py` picks a hwdec
that can feed it. They cannot import each other, and a disagreement is a black
video rather than an error.

macOS defaults to Qt's native Metal backend and Linux to Vulkan; Windows is
pinned to OpenGL. The macOS split is not a preference, it is the render loop:

  * Qt Quick refuses its THREADED render loop on macOS with OpenGL (forcing it
    with QSG_RENDER_LOOP=threaded crashes inside -[NSOpenGLContext setView:]
    on the render thread), so forcing OpenGL there lands on the BASIC loop,
    where animations, QML incubation, Python and texture uploads all share the
    GUI thread. Tab switches visibly hitch; the same code on Linux and Windows
    is smooth because both get the threaded loop.
  * libmpv's render API speaks OpenGL and nothing else, so on Metal the video
    item cannot render into a scene-graph FBO. It goes through libmpv's
    software render API instead -- a CPU buffer uploaded as a texture -- which
    costs a per-frame conversion the OpenGL path does not pay, and needs
    frames in system memory (hwdec must copy back).

Windows keeps OpenGL for mpv: it already has the threaded loop, and D3D11/DXVA2
surfaces cannot be imported into it anyway. The native engine does not need
it, and renders on Vulkan there as on Linux (see native_graphics).
Linux defaults to Vulkan, which is the backend Qt is investing in, and keeps
video zero-copy through the native bridge in native/linux/. Any machine whose
drivers cannot do that interop falls back to OpenGL automatically -- see
main.py, which asks before committing.

GRAVITAS_GRAPHICS overrides both defaults with `opengl`: on macOS that
restores the zero-copy player at the cost of the render loop, on Linux it
returns to the pre-Vulkan path. On Windows it only applies to the native
engine (`d3d11` or `opengl`), since mpv's OpenGL is not a preference there but
a requirement.

GRAVITAS_HDR rides on that Linux Vulkan path and names the whole chain at once
(see HDR_MODES), because the surface format, the swapchain and mpv's transfer
curve are one decision wearing three hats.

Both HDR chains are UNVERIFIED. Each one runs, gets the swapchain it asks for
and plays without artefacts, but nobody has yet confirmed on an HDR display
which -- if either -- is colorimetrically right, and they cannot both be. The
open question is whether Qt re-applies a transfer curve to an imported texture
on its way into an HDR swapchain, which would silently double-encode one of
them. Off by default for exactly that reason. To settle it, compare a bright
specular highlight against the player's own white UI text in the same frame:
real HDR highlights go far past SDR white, a flattened chain does not.

Both functions take their environment and platform as arguments so the branch
that does not match the host stays testable.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

LINUX = "linux"
MACOS = "darwin"
WINDOWS = "win32"

_VARIABLE = "GRAVITAS_GRAPHICS"
_HDR_VARIABLE = "GRAVITAS_HDR"

#: The two ways an HDR frame can reach the display through Qt Quick, and the
#: whole chain each one implies. `hdr10` encodes to PQ in Rec. 2020 and needs
#: only 10 bits, because PQ spends them perceptually; `scrgb` stays in linear
#: light with sRGB primaries and needs float, because linear 10-bit bands. They
#: are not interchangeable: the surface format, the swapchain Qt asks KWin for,
#: and the transfer curve mpv renders with all have to agree, so they travel
#: together as one name rather than as three settings that can disagree.
HDR_MODES = ("hdr10", "scrgb")

#: The scene graphs the native engine can run on under Windows, the default
#: first.
WINDOWS_NATIVE_GRAPHICS = ("vulkan", "d3d11", "opengl")


def metal_scene_graph(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """True when Qt Quick should keep its native RHI (Metal) instead of OpenGL."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform != MACOS:
        return False
    return environ.get(_VARIABLE, "").strip().lower() != "opengl"


def video_needs_system_memory(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """True when decoded frames must land in system memory, not on the GPU.

    Two unrelated causes, one consequence -- mpv has to be told to copy frames
    back (`hwdec=auto-copy`) instead of handing over a GPU surface:

      * Windows decodes to D3D11/DXVA2 surfaces, which the OpenGL video item
        cannot import.
      * macOS on Metal renders video through libmpv's software render API,
        which reads frames from the CPU.
    """
    platform = sys.platform if platform is None else platform
    if platform == WINDOWS:
        return True
    return metal_scene_graph(environ, platform)


def vulkan_scene_graph(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """True when Qt Quick should run on the Vulkan RHI (the Linux default).

    Intent only. Whether the bridge loads, and whether this machine's drivers
    can actually do the GL/Vulkan interop, are decided in main.py -- which
    commits the RHI only if both hold and otherwise takes OpenGL, still fully
    zero-copy. That fallback is what makes Vulkan defensible as a default on
    hardware nobody has tested: being wrong costs nothing.

    `GRAVITAS_GRAPHICS=opengl` forces the old path back, which is the switch to
    reach for if a driver misbehaves in a way the probe does not catch.

    Off Linux this is always False: macOS chooses between Metal and OpenGL,
    Windows is OpenGL-only.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform != LINUX:
        return False
    return environ.get(_VARIABLE, "").strip().lower() != "opengl"


def hdr_mode(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str | None:
    """The requested HDR chain (`"hdr10"` / `"scrgb"`), or None for SDR.

    Rides on the Vulkan backend rather than standing alone: HDR needs a
    swapchain format Qt only offers through the RHI, and the OpenGL path here
    renders into a scene-graph FBO it does not own. Asking for HDR while
    `GRAVITAS_GRAPHICS=opengl` is therefore a contradiction, and answering None
    is what keeps the rest of the app from half-configuring itself.

    An unrecognised value is None too, not an error: a typo should cost the
    brightness, not the playback.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if not vulkan_scene_graph(environ, platform):
        return None
    requested = environ.get(_HDR_VARIABLE, "").strip().lower()
    return requested if requested in HDR_MODES else None


def native_graphics(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str | None:
    """The scene graph the native engine asks for on Linux and Windows, or
    None (macOS, which chooses between Metal and OpenGL as for mpv).

    Vulkan by default: the engine then renders with libplacebo on Qt's own
    device and decodes into it (Vulkan Video), so frames never leave the GPU.
    Intent only: main.py commits to Vulkan once a GPU has answered
    (native_vk_bridge.gpu_available), and otherwise takes
    native_graphics_fallback(), where the engine renders through its
    readback path. Unlike mpv's Vulkan path, nothing here needs the GL/Vulkan
    interop probe: the engine never shares an image with OpenGL.

    `GRAVITAS_GRAPHICS=opengl` chooses OpenGL directly, and on Windows
    `d3d11` too. Anything else is the default: a typo should not cost the GPU.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    requested = environ.get(_VARIABLE, "").strip().lower()
    if platform == WINDOWS:
        return requested if requested in WINDOWS_NATIVE_GRAPHICS else "vulkan"
    if platform == LINUX:
        return "opengl" if requested == "opengl" else "vulkan"
    return None


def native_graphics_fallback(platform: str | None = None) -> str:
    """Where the native engine renders without a Vulkan GPU: D3D11, Qt's own
    Windows default, or OpenGL."""
    platform = sys.platform if platform is None else platform
    return "d3d11" if platform == WINDOWS else "opengl"
