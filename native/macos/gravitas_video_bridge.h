// C ABI for the macOS zero-copy video bridge. Deliberately tiny: everything
// Python touches crosses here as opaque pointers and plain integers, so the
// Python side needs no CPython extension, no shiboken generation, and no
// compiler at run time -- just ctypes.
//
// THREADING. Every function except gv_video_bridge_error() must be called on
// Qt's RENDER thread (inside updatePaintNode, or a sceneGraphInvalidated
// handler connected directly). Scene-graph and GPU resources belong to that
// thread; touching them from the GUI thread crashes.
//
// SHAPE. The GL context is created once, with the bridge, and lives as long as
// it does; the surface mpv draws into is created separately and can be
// replaced when the video's resolution changes. They are split because mpv's
// render context binds to the GL context at creation and must be created
// BEFORE mpv can load a file -- which is also when the video's size first
// becomes known. One lifetime cannot serve both.
//
// LIFETIME. The bridge hands out a QSGTexture that the caller must not free,
// and must not let a scene-graph node own (attach it with
// setOwnsTexture(false)). Qt's batch renderer keeps using a texture for an
// unbounded number of frames after the node referencing it is gone, so
// replacing the surface does not free the old one: it is retired inside the
// bridge and released with everything else in gv_video_bridge_destroy(), which
// is only safe once the scene graph is invalidated.

#ifndef GRAVITAS_VIDEO_BRIDGE_H
#define GRAVITAS_VIDEO_BRIDGE_H

#ifdef __cplusplus
extern "C" {
#endif

// ABI version. The loader refuses a bridge whose number it does not know,
// which is what keeps a stale build from being loaded against newer Python.
#define GV_VIDEO_BRIDGE_ABI 4

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

// Create the bridge and its OpenGL context for `window` (a QQuickWindow*).
// No surface yet -- see gv_video_bridge_set_size(). NULL on failure.
GV_API GvVideoBridge *gv_video_bridge_create(void *window);

// Release everything, including every retired surface. Only safe once Qt
// guarantees nothing references the textures, i.e. from sceneGraphInvalidated.
GV_API void gv_video_bridge_destroy(GvVideoBridge *bridge);

// Point the bridge at a new video resolution, retiring any previous surface.
// Returns 1 on success. The GL context, and so mpv's render context, survives.
GV_API int gv_video_bridge_set_size(GvVideoBridge *bridge, int width, int height);

// Make the bridge's GL context current (1 on success). Every successful call
// must be paired with gv_video_bridge_end().
GV_API int gv_video_bridge_begin(GvVideoBridge *bridge);

// Flush the GL work -- Metal has no implicit ordering with GL, and without
// this the frame tears -- then restore the previously current context.
GV_API void gv_video_bridge_end(GvVideoBridge *bridge);

// The framebuffer object mpv renders into, or 0 before a size is set.
GV_API unsigned int gv_video_bridge_fbo(GvVideoBridge *bridge);

// Which pixel format the surface ended up with, for logging. "" before a
// size is set.
GV_API const char *gv_video_bridge_format(GvVideoBridge *bridge);

// The framebuffer's GL internal format (e.g. GL_RGB10_A2). mpv needs this to
// know how much precision the target has: told nothing, it assumes 8 bits and
// dithers away the extra depth a 10-bit surface exists to keep.
GV_API unsigned int gv_video_bridge_gl_internal_format(GvVideoBridge *bridge);

// The QSGTexture* for a QSGSimpleTextureNode, or NULL before a size is set.
// Never freed by the caller; see LIFETIME above.
GV_API void *gv_video_bridge_texture(GvVideoBridge *bridge);

#ifdef __cplusplus
} // extern "C"
#endif

#endif // GRAVITAS_VIDEO_BRIDGE_H
