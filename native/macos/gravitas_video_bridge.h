// C ABI for the macOS zero-copy video bridge. Deliberately tiny: everything
// Python touches crosses here as opaque pointers and plain integers, so the
// Python side needs no CPython extension, no shiboken generation, and no
// compiler at run time -- just ctypes.
//
// WHY MPV LIVES ON THIS SIDE. The bridge owns mpv's render context: it
// creates it, renders with it and frees it. Driving that from Python would be
// less code, and it was, until the lifetime turned out to be unrepresentable
// there. mpv's context must be freed on Qt's RENDER thread; Qt announces that
// moment through scene-graph signals emitted on the render thread; and a
// Python slot on those signals has to take the GIL -- which the GUI thread is
// holding while it waits for the render thread to finish. Neither side moves
// again. (Sampled: QSGRenderThread in PyGILState_Ensure, main thread in
// _pthread_cond_wait inside a Python frame.) A C++ slot has no such problem,
// so teardown lives here, connected to the window directly, and Python never
// runs on the render thread at a moment Qt cannot tolerate.
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

#ifndef GRAVITAS_VIDEO_BRIDGE_H
#define GRAVITAS_VIDEO_BRIDGE_H

#ifdef __cplusplus
extern "C" {
#endif

// ABI version. The loader refuses a bridge whose number it does not know,
// which is what keeps a stale build from being loaded against newer Python.
#define GV_VIDEO_BRIDGE_ABI 6

// The library is built with -fvisibility=hidden so nothing but these entry
// points is exported; each one has to opt back in.
#define GV_API __attribute__((visibility("default")))

GV_API int gv_video_bridge_abi(void);

// The Qt version this was compiled against, as "6.11.1". The loader compares
// it to qVersion() and refuses a mismatch: QRhi offers no binary
// compatibility guarantee across releases, so a mismatch is a crash waiting
// for a frame, not a degraded mode.
GV_API const char *gv_video_bridge_qt_version(void);

// Why the last call that returned a failure failed. Thread-local.
GV_API const char *gv_video_bridge_error(void);

typedef struct GvVideoBridge GvVideoBridge;

// Create the bridge, its OpenGL context and mpv's render context, for
// `window` (a QQuickWindow*) and `mpv` (an mpv_handle*). No surface yet -- see
// gv_video_bridge_set_size(). NULL on failure.
//
// mpv's render context has to exist before mpv can bring up its video output
// at all, and only once it has does mpv know the video's size, so creation
// deliberately does not wait for a resolution.
GV_API GvVideoBridge *gv_video_bridge_create(void *window, void *mpv);

// Release everything early. Not normally needed: the bridge tears itself down
// when the window's scene graph goes away. Safe to call twice.
GV_API void gv_video_bridge_destroy(GvVideoBridge *bridge);

// Point the bridge at a new video resolution, retiring any previous surface.
// Returns 1 on success. mpv's render context survives.
GV_API int gv_video_bridge_set_size(GvVideoBridge *bridge, int width, int height);

// The item to wake when mpv has a frame. Qt only calls updatePaintNode on
// items marked dirty, and only QQuickItem::update() marks one -- asking the
// WINDOW to update schedules a render in which our item is not dirty, so
// nothing is drawn and the picture only moves when something else (a resize)
// dirties the item. The item is woken through a named slot rather than
// touched directly, since update() is protected.
GV_API void gv_video_bridge_set_item(GvVideoBridge *bridge, void *item);

// Draw one mpv frame into the surface. Returns 1 if a frame was rendered.
GV_API int gv_video_bridge_render(GvVideoBridge *bridge);

// The QSGTexture* for a QSGSimpleTextureNode, or NULL before a size is set.
// Never freed by the caller; see LIFETIME above.
GV_API void *gv_video_bridge_texture(GvVideoBridge *bridge);

// Which pixel format the surface ended up with, for logging. "" before a
// size is set.
GV_API const char *gv_video_bridge_format(GvVideoBridge *bridge);

#ifdef __cplusplus
} // extern "C"
#endif

#endif // GRAVITAS_VIDEO_BRIDGE_H
