"""Which scene-graph backend Qt Quick runs on, and what that costs the player.

One module because two unrelated places have to agree on the answer: `main.py`
picks the RHI and the matching video item, and `mpv_player.py` picks a hwdec
that can feed it. They cannot import each other, and a disagreement is a black
video rather than an error.

macOS defaults to Qt's native Metal backend; everywhere else the OpenGL RHI is
forced. That split is not a preference, it is the render loop:

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

Linux and Windows keep OpenGL: they already have the threaded loop, so there
is nothing to buy and a working zero-copy video path to lose.

GRAVITAS_GRAPHICS overrides the choice on macOS -- `opengl` restores the
zero-copy player at the cost of the render loop, `metal` is the default. On
Linux, `vulkan` opts into the Vulkan RHI and its native video bridge. It is
ignored on Windows, where OpenGL is not a preference but a requirement.

GRAVITAS_HDR rides on that Linux opt-in and names the whole HDR chain at once
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
    """True when Qt Quick should run on the Vulkan RHI (Linux opt-in only).

    Intent only -- whether the native bridge can actually be loaded is decided
    in main.py, which commits the RHI only if it can. Off Linux this is always
    False: macOS chooses between Metal and OpenGL, Windows is OpenGL-only.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if platform != LINUX:
        return False
    return environ.get(_VARIABLE, "").strip().lower() == "vulkan"


def hdr_mode(
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
) -> str | None:
    """The requested HDR chain (`"hdr10"` / `"scrgb"`), or None for SDR.

    Rides on the Vulkan opt-in rather than standing alone: HDR needs a
    swapchain format Qt only offers through the RHI, and the OpenGL path here
    renders into a scene-graph FBO it does not own. Asking for HDR without
    `GRAVITAS_GRAPHICS=vulkan` is therefore a contradiction, and answering None
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
