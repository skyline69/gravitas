// C ABI for the Linux Vulkan zero-copy video bridge. Deliberately tiny:
// everything Python touches crosses here as opaque pointers and plain
// integers, so the Python side needs no CPython extension, no shiboken
// generation, and no compiler at run time -- just ctypes.
//
// This is the Linux/Vulkan sibling of native/macos/gravitas_video_bridge.h.
// The macOS bridge renders mpv through OpenGL into an IOSurface that Metal
// samples; here mpv renders through OpenGL (an EGL context matched to Qt's
// Vulkan GPU by device UUID) into an exportable VkImage that Qt's Vulkan
// device samples, the shared surface being opaque-FD external memory. Every
// threading and lifetime rule below is inherited from the macOS bridge, for
// the same reasons.
//
// WHY MPV LIVES ON THIS SIDE. The bridge owns mpv's render context: it
// creates it, renders with it and frees it. Driving that from Python would be
// less code, and it was, until the lifetime turned out to be unrepresentable
// there. mpv's context must be freed on Qt's RENDER thread; Qt announces that
// moment through scene-graph signals emitted on the render thread; and a
// Python slot on those signals has to take the GIL -- which the GUI thread is
// holding while it waits for the render thread to finish. Neither side moves
// again. A C++ slot has no such problem, so teardown lives here, connected to
// the window directly, and Python never runs on the render thread at a moment
// Qt cannot tolerate.
//
// THREADING. create/set_size/render/texture must be called on Qt's render
// thread (inside updatePaintNode). Everything else is internal.
//
// LIFETIME. The QSGTexture handed out must not be freed by the caller, and
// must not be owned by a scene-graph node (attach it with
// setOwnsTexture(false)). Qt's batch renderer keeps using a texture for an
// unbounded number of frames after the node referencing it is gone, so a
// surface replaced by a resolution change is retired inside the bridge rather
// than released, and everything is freed together when the window's scene
// graph goes away.

#ifndef GRAVITAS_VIDEO_BRIDGE_VK_H
#define GRAVITAS_VIDEO_BRIDGE_VK_H

#ifdef __cplusplus
extern "C" {
#endif

// ABI version. The loader refuses a bridge whose number it does not know,
// which is what keeps a stale build from being loaded against newer Python.
// Distinct from the macOS bridge's number and namespace so the two can never
// be cross-loaded.
#define GV_VIDEO_BRIDGE_VK_ABI 2

// The library is built with -fvisibility=hidden so nothing but these entry
// points is exported; each one has to opt back in.
#define GV_API __attribute__((visibility("default")))

GV_API int gv_video_bridge_vk_abi(void);

// The Qt version this was compiled against, as "6.11.1". The loader compares
// it to qVersion() and refuses a mismatch: QRhi offers no binary
// compatibility guarantee across releases, so a mismatch is a crash waiting
// for a frame, not a degraded mode.
GV_API const char *gv_video_bridge_vk_qt_version(void);

// Why the last call that returned a failure failed. Thread-local.
GV_API const char *gv_video_bridge_vk_error(void);

// Whether this machine's drivers can actually do the GL/Vulkan interop, on
// its own instance and device, before Qt exists. 1 if yes.
//
// Advertising the extensions is not the same as implementing them: Mesa's
// llvmpipe offers all four of EXT_memory_object(_fd) and EXT_semaphore(_fd)
// and then fails every imported allocation with GL_OUT_OF_MEMORY. Asking the
// driver to do the thing is the only reliable question.
//
// This has to answer BEFORE QQuickWindow::setGraphicsApi, because that is the
// last moment the caller can still choose OpenGL -- a bridge that fails later
// leaves the scene graph on Vulkan with no way back and the user watching a
// black rectangle. Hence its own throwaway VkInstance/VkDevice: Qt's are not
// available yet, and the point is not to need them.
//
// It probes the FIRST physical device, which is the one Qt's QRhi takes unless
// QT_VK_PHYSICAL_DEVICE_INDEX says otherwise. On a multi-GPU machine with that
// variable set, this can answer for a different device than Qt will use.
GV_API int gv_video_bridge_vk_probe(void);

typedef struct GvVideoBridgeVk GvVideoBridgeVk;

// Create the bridge, its EGL/OpenGL context and mpv's render context, for
// `window` (a QQuickWindow* already on the Vulkan RHI) and `mpv` (an
// mpv_handle*). No surface yet -- see gv_video_bridge_vk_set_size(). NULL on
// failure (Qt not on Vulkan, no EGL device matching the Vulkan GPU, missing
// GL external-object extensions, mpv context creation failed).
GV_API GvVideoBridgeVk *gv_video_bridge_vk_create(void *window, void *mpv);

// Release everything early. Not normally needed: the bridge tears itself down
// when the window's scene graph goes away. Safe to call twice.
GV_API void gv_video_bridge_vk_destroy(GvVideoBridgeVk *bridge);

// Point the bridge at a new video resolution, retiring any previous surface
// ring. Returns 1 on success. mpv's render context survives.
GV_API int gv_video_bridge_vk_set_size(GvVideoBridgeVk *bridge, int width, int height);

// The item to wake when mpv has a frame. Qt only calls updatePaintNode on
// items marked dirty, and only QQuickItem::update() marks one -- asking the
// WINDOW to update schedules a render in which our item is not dirty. The item
// is woken through a named slot ("requestUpdate") rather than touched
// directly, since update() is protected.
GV_API void gv_video_bridge_vk_set_item(GvVideoBridgeVk *bridge, void *item);

// 1 once the scene graph this bridge belongs to has gone away -- a fullscreen
// toggle recreates it. A stale bridge draws nothing and must be replaced.
GV_API int gv_video_bridge_vk_stale(GvVideoBridgeVk *bridge);

// Draw one mpv frame into the current ring surface. Returns 1 if a frame was
// rendered.
GV_API int gv_video_bridge_vk_render(GvVideoBridgeVk *bridge);

// The QSGTexture* for the current ring surface, for a QSGSimpleTextureNode, or
// NULL before a size is set. Never freed by the caller; see LIFETIME above.
GV_API void *gv_video_bridge_vk_texture(GvVideoBridgeVk *bridge);

// Which pixel format the surface ended up with, for logging. "" before a size
// is set.
GV_API const char *gv_video_bridge_vk_format(GvVideoBridgeVk *bridge);

#ifdef __cplusplus
}  // extern "C"
#endif

#endif  // GRAVITAS_VIDEO_BRIDGE_VK_H
