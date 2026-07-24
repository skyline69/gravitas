# Linux Vulkan zero-copy video bridge

Status: approved, ready to plan
Date: 2026-07-24

## Why

On Linux, Gravitas already renders video the good way: NVIDIA/Mesa GPU decode
(`hwdec=auto-safe`), zero copy-back, and libmpv's OpenGL render API drawing
straight into a Qt Quick scene-graph FBO under the OpenGL RHI with the threaded
render loop. It is the best case among the three platforms — macOS and Windows
are the compromised ones.

This spec adds an **opt-in** path to run Qt Quick on the **Vulkan RHI** on Linux
while keeping video zero-copy. That is not a performance win over the current
OpenGL path — the OpenGL path is already zero-copy and smooth on Linux. It
exists for cases where Vulkan is the right or only choice: a broken/absent
OpenGL driver where Vulkan still works, Wayland explicit-sync futures, or a
future Qt that de-prioritises the OpenGL RHI. It is built now so the capability
exists, gated off by default with zero cost to existing users.

The hard constraint that shapes everything: **libmpv's render API speaks OpenGL
and software, never Vulkan.** So under a Vulkan RHI, mpv cannot render into the
scene-graph texture directly. A native bridge makes mpv render through OpenGL
into a GPU surface that Qt's Vulkan device then samples — the exact structure of
the existing macOS bridge (mpv → OpenGL → IOSurface → Metal samples it), with
the shared surface being Vulkan-exported memory instead of an IOSurface.

## Scope

- **This spec: NVIDIA proprietary driver on Wayland only**, using opaque-FD
  external memory interop. Fully hardened: native bridge, ABI-versioned loader,
  fallback, build script, packaging, CI, tests.
- **Deferred to a later spec: the universal path** (Mesa iris/radeonsi, X11,
  DMA-BUF interop). The C++ bridge is architected with a swappable interop
  backend so that phase is purely additive — see "Swappable interop seam".
- Non-Linux platforms are untouched. macOS keeps its Metal bridge; Windows and
  the default Linux path keep OpenGL.

## Decisions locked during brainstorming

1. **Goal:** full hardened production path, mirroring the macOS bridge in every
   part (loader / fallback / build / packaging / CI / tests).
2. **Portability:** NVIDIA first; universal solution is a later, additive phase.
3. **Fallback:** if Vulkan is requested but the bridge cannot load (wrong Qt,
   missing `.so`, non-NVIDIA, init failure), **never switch the RHI** — stay on
   today's OpenGL zero-copy path. Bridge availability is checked *before*
   `setGraphicsApi`. Worst case equals today's behaviour; no regression.
4. **Interop approach A:** Vulkan (Qt's device) allocates the exportable image,
   GL (mpv's context) imports it, a shared binary semaphore orders
   GL-write-before-Vulkan-sample. Ownership matches macOS (Qt's device owns the
   surface), so the lifetime rules carry over verbatim.
5. **Language:** C++. The bridge's dominant dependency is Qt C++ (QRhi / scene
   graph), which has no C API and no mature Rust binding; cxx-qt targets
   QObject/QML, not QRhi internals. A Rust core would only make sense as a hybrid
   behind a C++ Qt shim, whose second-toolchain cost is not justified for a first
   NVIDIA target. Revisit for the universal phase if its Vulkan logic grows.

## Interop approaches considered

- **A — Vulkan allocates, GL imports, shared binary semaphore, N-buffered
  (chosen).** Qt's QRhi `VkDevice` allocates exportable `VkImage` +
  `VkDeviceMemory`; mpv's own EGL GL context imports the FD and renders into it.
  Ownership matches macOS, so the "Qt keeps sampling a texture for unbounded
  frames after its node is gone" rule is handled identically (retire, never free).
- **B — GL allocates / DMA-BUF export, Vulkan imports.** Reverses ownership; the
  `QSGTexture` then wraps memory Qt does not own, and Qt's unbounded texture
  lifetime fights GL-side reuse. Worse teardown. Rejected.
- **C — CPU / compositor surface.** Defeats zero-copy. Rejected.

## Threading contract

Three threads, same split as the macOS bridge:

- **GUI thread** — item construction, `handle` injection.
- **mpv render thread** — mpv's `update_cb` fires; the bridge does *not* render
  here. It wakes the item through a named slot (`QQuickItem::update()` is
  protected), which marks the item dirty so Qt schedules `updatePaintNode`.
- **Qt render thread** — `create` / `set_size` / `render` / `texture` / `stale`
  all run here, inside `updatePaintNode`. mpv's GL render also happens here, with
  the bridge's EGL context made current inside `render()`. Vulkan has no
  current-context TLS, so a GL context current on this thread does not collide
  with Qt's Vulkan device.

## Native bridge — C ABI

`native/linux/gravitas_video_bridge_vk.{h,cpp}`. Tiny opaque-pointer + plain-int
C ABI, so the Python side stays pure `ctypes` (no CPython extension, no shiboken,
no compiler at run time). Entry points mirror the macOS header in spirit, with a
distinct symbol prefix (`gv_video_bridge_vk_*`) and its own ABI number so a macOS
and a Linux bridge can never be cross-loaded:

- `int gv_video_bridge_vk_abi(void)`
- `const char *gv_video_bridge_vk_qt_version(void)` — compared to `qVersion()`
- `const char *gv_video_bridge_vk_error(void)` — thread-local last-error
- `GvVideoBridgeVk *gv_video_bridge_vk_create(void *window, void *mpv)`
- `void gv_video_bridge_vk_destroy(GvVideoBridgeVk *)`
- `int gv_video_bridge_vk_set_size(GvVideoBridgeVk *, int w, int h)`
- `void gv_video_bridge_vk_set_item(GvVideoBridgeVk *, void *item)`
- `int gv_video_bridge_vk_stale(GvVideoBridgeVk *)`
- `int gv_video_bridge_vk_render(GvVideoBridgeVk *)`
- `void *gv_video_bridge_vk_texture(GvVideoBridgeVk *)` — a `QSGTexture *`
- `const char *gv_video_bridge_vk_format(GvVideoBridgeVk *)`

Built with `-fvisibility=hidden`; only the `extern "C"` entry points are exported.

## Native bridge — create (device match is the gate)

`create(window, mpv)`, on the render thread:

1. `QSGRendererInterface *rif = window->rendererInterface()`. Pull `VkInstance`,
   `VkPhysicalDevice`, `VkDevice`, the graphics `VkQueue`, and the graphics
   queue-family index via the documented `QSGRendererInterface::Resource` enums.
   Qt has already chosen the device; whatever it picked is what the bridge matches
   against. If Qt picked `llvmpipe`, `create` fails cleanly and the caller falls
   back.
2. Query `VkPhysicalDeviceIDProperties.deviceUUID` for that physical device.
3. Create a **surfaceless EGL context** (`EGL_KHR_surfaceless_context`) on the
   `EGLDeviceEXT` whose `GL_DEVICE_UUID_EXT` equals that `deviceUUID`. A UUID
   mismatch means the opaque-FD import would be invalid, so this fails → fallback.
   **This is the single most important correctness gate.**
4. Create mpv's render context (`opengl`, `get_proc_address = eglGetProcAddress`)
   — a C++ port of `presentation/video/mpv_item.py`'s context creation.
5. Create exportable `VkSemaphore`s (handle type `OPAQUE_FD`), export their FDs,
   and import them into GL (`glGenSemaphoresEXT` / `glImportSemaphoreFdEXT`).

mpv's render context is created before any surface exists: mpv brings up its
video output first and only then knows the video size, exactly as the macOS
header documents.

## Native bridge — set_size (exportable image, GL imports it)

`set_size(w, h)`, on the render thread:

1. Allocate a ring of **N = 3** exportable `VkImage`
   (`VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT`, `VK_IMAGE_TILING_OPTIMAL`),
   each with dedicated exportable `VkDeviceMemory`; export each memory FD.
2. GL side per image: `glCreateMemoryObjectsEXT` → `glImportMemoryFdEXT` with
   `GL_HANDLE_TYPE_OPAQUE_FD_EXT` → set `GL_TEXTURE_TILING_EXT =
   GL_OPTIMAL_TILING_EXT` → `glTextureStorageMem2DEXT(GL_RGBA8, w, h, mem, 0)`.
   GL tiling and Vulkan tiling **must agree** (both OPTIMAL) or the sample is
   garbage.
3. Wrap each GL texture in a GL FBO for mpv's render target.
4. Wrap each `VkImage` as a `QSGTexture`: `QRhiTexture::createFrom` (does **not**
   retain the native image — the macOS header's exact rule) →
   `QQuickWindow::createTextureFromRhiTexture` (**takes ownership** — also
   verbatim from macOS).
5. **Retire, never free** previous surfaces into a retired list; free everything
   only at teardown. Qt's batch renderer keeps sampling a texture for an unbounded
   number of frames after the node referencing it is gone — the macOS crash, same
   rule here.
6. Format `RGBA8` now; `RGBA16F` reserved for a later HDR milestone. Reported via
   `format()` for logging.

## Native bridge — render (the sync protocol)

Per frame, on the render thread. This is the highest-risk piece; the bring-up
order is designed around validating it (see Risks).

1. `eglMakeCurrent(bridge context)`.
2. **GL waits** the "Vulkan-done-sampling" semaphore for the target ring slot
   (`glWaitSemaphoreEXT`), so GL never overwrites a buffer Qt is still sampling.
3. `mpv_render_context_render` into that slot's GL FBO, with
   `block_for_target_time = false` — never stall Qt's render thread on the
   frame's presentation time (the `mpv_item.py` rule; that wait shows up as
   UI-wide lag).
4. **GL signals** the "GL-done-writing" semaphore with the texture transitioned
   to `GL_LAYOUT_SHADER_READ_ONLY_EXT`; `glFlush`.
5. **Inject a wait into Qt's queue:** `vkQueueSubmit` on Qt's graphics `VkQueue`
   with `pWaitSemaphores = [gl_done]`, `pWaitDstStageMask = FRAGMENT_SHADER`,
   carrying the `UNDEFINED → SHADER_READ_ONLY_OPTIMAL` layout-transition barrier.
   Because Qt submits its compositing on the same queue right after
   `updatePaintNode`, this orders Qt's sampling after mpv's write.
6. Hand that slot's `QSGTexture` to the node; advance the ring.

**Reverse direction** (Qt still reading vs mpv reuse): Qt does not signal our
semaphores, so it is covered two ways — the **N = 3 ring** (under vsync, Qt is at
least two frames past a slot by the time it recurs) plus a **per-slot `VkFence`**
checked before reuse as a belt.

## Native bridge — lifetime and teardown

Every rule is inherited from the macOS bridge, for the same reasons:

- Free mpv's render context and all Vulkan/GL objects **on the render thread**,
  driven by `QQuickWindow::sceneGraphInvalidated` connected in C++.
- **mpv lives in C++, not Python.** Freeing the render context on the render
  thread from a Python slot deadlocks on the GIL while the GUI thread waits for
  the render thread — the sampled macOS deadlock. A C++ slot has no such problem.
- `stale()` returns 1 once the scene graph this bridge belongs to has gone away
  (a fullscreen toggle recreates it); a stale bridge draws nothing and is
  replaced. No "stopped" latch — the macOS bridge proved that unreliable.

## Python side

- **`infrastructure/graphics.py`** stays pure (takes `environ`/`platform`
  arguments, fully testable). Add `vulkan_scene_graph(environ, platform)` → True
  only on Linux when `GRAVITAS_GRAPHICS=vulkan`. It reports **intent only**;
  capability (does the bridge load) is decided in `main.py`. macOS's
  `metal_scene_graph` / `video_needs_system_memory` are untouched. Value matrix:
  Linux `vulkan` → Vulkan, anything else → OpenGL; macOS `opengl` / `metal`
  unchanged; Windows unaffected.

- **`presentation/video/vulkan_bridge.py`** — a line-for-line mirror of
  `metal_bridge.py`: its own `_ABI`, `_LIBRARY = libgravitas_video_bridge_vk.so`,
  `_bind` signatures, `library()` with ABI + `qVersion()` refusal, `available()`,
  `last_error()`. Cached both ways; never raises past `BridgeUnavailable`.

- **`presentation/video/mpv_vulkan_item.py`** — a `QQuickItem` using
  `updatePaintNode` + `QSGSimpleTextureNode` (**not** `QQuickFramebufferObject`,
  which is OpenGL-only). Mirrors `mpv_metal_item.py`. Reuses the shared `handle`
  `Property` and the `_UpdateBridge` weak-reference pattern that survives the
  item's death (the callback-outlives-item fix). Pulls `texture()` each
  `updatePaintNode`; `set_item` wires the mpv frame callback to `update()`.

- **`main.py` wiring order** — the fallback decision made concrete:
  1. `want_vulkan = vulkan_scene_graph()`.
  2. `if want_vulkan and vulkan_bridge.available():` →
     `QQuickWindow.setGraphicsApi(Vulkan)`, register `MpvVulkanVideoItem` as
     `MpvVideo`, log zero-copy Vulkan.
  3. else → today's path unchanged (`setGraphicsApi(OpenGL)` off-macOS,
     `MpvVideoItem`).

  Availability is checked **before** `setGraphicsApi`, so the RHI is committed
  only when the bridge has loaded. Worst case is today's OpenGL path.

- **Device selection:** QRhi's Vulkan backend prefers a discrete/integrated
  device over a CPU device (`llvmpipe` reports CPU), so NVIDIA is selected by
  default; a wrong pick is caught by the create-time UUID gate → fallback. If
  forcing becomes necessary during bring-up, the lever is
  `QQuickWindow::setGraphicsDevice(QQuickGraphicsDevice::fromPhysicalDevice(...))`.
  Deferred unless needed.

## Build

Extend `scripts/build_video_bridge.py` with a Linux path (or a `_vk.py` sibling).
Qt headers come from an aqtinstall SDK matching the wheel's Qt exactly
(`aqt install-qt linux desktop <ver> gcc_64 --archives qtbase qtdeclarative`),
including the versioned QRhi private headers, same as macOS. Compile with
`g++ -std=c++17 -fPIC -shared -O2 -fvisibility=hidden`; link `Qt6Core Qt6Gui
Qt6Quick EGL GL vulkan`; set `-rpath` to the wheel's `Qt/lib` so Qt resolves to
the already-loaded copy. Output `libgravitas_video_bridge_vk.so` next to the
Python that loads it. The exact-Qt-match discipline is enforced by the loader at
run time — QRhi carries no binary-compatibility guarantee across releases.

## Packaging (AppImage + Flatpak)

- `packaging/gravitas.spec` bundles the `.so` (it already sweeps
  `presentation/video`; the release job asserts the file is present). The
  default-off gate means it **ships inert** — zero risk to default users.
- **Do not bundle** `libvulkan.so.1` or the ICDs — the Vulkan loader and the
  NVIDIA driver come from the host, exactly like `libGL`. Bundling would break the
  driver match.
- **Flatpak:** Vulkan + NVIDIA needs `org.freedesktop.Platform.GL.nvidia` in the
  runtime, which is already required for any GPU acceleration, so there is no new
  blocker while the default stays OpenGL. The opt-in Vulkan path is validated
  in-sandbox separately.

## CI

- A new job builds the `.so` (aqtinstall Qt matching the wheel) and runs the
  Python loader tests, the `graphics.py` tests, `mypy`, and `ruff`.
- It **cannot exercise the NVIDIA interop headless** — there is no GPU runner, and
  `llvmpipe` Vulkan cannot drive the opaque-FD NVIDIA path. The interop is
  **launch-verified on real hardware**, the same coverage class as
  QML-verified-at-launch. Stated here rather than hidden.
- The release Linux jobs gain the build step so the AppImage and Flatpak carry
  the `.so`.

## Tests

- `vulkan_bridge.py` loader: ABI mismatch → `BridgeUnavailable`, Qt-version
  mismatch → fallback, missing `.so` → fallback, cached-failure — mirroring the
  macOS loader tests.
- `graphics.py`: the `vulkan_scene_graph()` truth table over {platform, env} —
  pure and fully covered.
- The composition root (`main.py` ordering) is launch-verified.
- `mypy --strict` on all new Python; precise `# type: ignore[code]` for the
  untyped `ctypes` / `mpv` boundaries, never blanket ignores.

## Swappable interop seam (future universal / Mesa phase)

The C++ bridge is split into two layers so the universal phase is additive:

- **Qt-facing shell** — device-handle extraction, `QSGTexture` creation,
  threading, lifetime, and the *shape* of the sync protocol. Driver-agnostic.
- **Interop backend interface**, with one implementation now: `OpaqueFdInterop`
  (exportable `VkImage` → export → GL import, plus the shared semaphores). A
  future `DmaBufInterop` (`EGL_MESA_image_dma_buf_export` /
  `VK_EXT_external_memory_dma_buf`) drops in behind the same interface for Mesa
  **without touching the shell, lifetime, or sync**. Backend selection probes the
  available extensions at `create`. Opaque-FD is the only implementation in this
  spec; the interface is what makes doing NVIDIA-first clean rather than throwaway.

## Risks (ranked)

- **R1 — reverse-sync correctness** (Qt reading vs mpv reuse): the N = 3 ring +
  per-slot fence is belt-and-suspenders on paper. Validate first, with a
  torn-frame test on moving content.
- **R2 — injecting the wait into Qt's queue** (render step 5): assumes Qt
  composites on the same `VkQueue` right after `updatePaintNode`. If Qt uses a
  separate or batched queue, switch to `QQuickWindow::beforeRendering` /
  `afterRendering` hooks. Verify against the QRhi Vulkan submission model early.
- **R3 — device selection**: relies on QRhi preferring a discrete device over the
  CPU one. The `setGraphicsDevice` lever exists if that assumption is wrong.
- **R4 — EGL surfaceless context + `EGLDeviceEXT` UUID match** on NVIDIA Wayland:
  device enumeration has driver-specific quirks.
- **R5 — Qt version pin**: same discipline as macOS (PySide6 is already
  exact-pinned); verify the QRhi private headers are available via aqtinstall on
  Linux.
- **R6 — mpv rendering into an imported-memory GL texture** (OPTIMAL-tiling
  external object as a render target): confirm NVIDIA accepts it.

**Bring-up order follows the risks:** R2 → R3 → R4 to get one static frame on
screen, then R1 for motion without tearing, then the loader / fallback /
packaging hardening.

## Out of scope

- Mesa (iris / radeonsi), Intel, AMD — the universal phase, a later spec.
- X11 sessions — Wayland only here.
- HDR / `RGBA16F` surfaces — reserved, format is parameterised for it.
- Any change to the default OpenGL path, macOS Metal, or Windows.
