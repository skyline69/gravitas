// C ABI for the native engine's zero-copy video on macOS/Metal.
//
// The native engine (native/player/) renders with Metal on macOS. To render
// straight into a texture Qt's scene graph samples, it has to work on Qt's
// own MTLDevice and submit to Qt's own command queue -- and three things
// about those only C++ can reach: the handles (through QRhi), wrapping an
// MTLTexture as a QSGTexture, and the moment the scene graph goes away,
// which Qt announces on the RENDER thread, where Python must not run (see
// gravitas_video_bridge.mm for the deadlock that taught this).
//
// Unlike the Vulkan bridge (native/linux/gravitas_native_vk.h), nothing has
// to be torn down in the engine when the scene graph goes: Metal objects are
// reference counted, the engine holds its own references to the device and
// queue, and this bridge holds one to every texture it wraps -- so neither
// side can free what the other still uses, whatever order things end in.
//
// Built into the same library as the mpv bridge (scripts/build_video_bridge.py)
// and loaded by presentation/video/native_metal_bridge.py. Additive: a library
// without these symbols simply leaves the native engine on its readback path.
//
// THREADING. create, device and texture run on Qt's render thread (inside
// updatePaintNode); stale and destroy on any thread.
//
// LIFETIME. Textures handed out belong to the bridge (attach them with
// setOwnsTexture(false)) and live until the scene graph goes away: Qt's batch
// renderer keeps using a texture for frames after the node that referenced it
// is gone. Each keeps its MTLTexture alive with it, because
// QRhiTexture::createFrom does not retain it.

#ifndef GRAVITAS_NATIVE_METAL_H
#define GRAVITAS_NATIVE_METAL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifndef GV_API
#define GV_API __attribute__((visibility("default")))
#endif

// Qt's Metal device and the command queue its scene graph renders with.
typedef struct GvNativeMetalDevice {
    uint64_t device;  // id<MTLDevice>
    uint64_t queue;   // id<MTLCommandQueue>
} GvNativeMetalDevice;

typedef struct GvNativeMetal GvNativeMetal;

// Why the last call that failed on this thread failed.
GV_API const char *gv_native_mtl_error(void);

// Reach Qt's Metal device for `window` (a QQuickWindow* on the Metal RHI).
// NULL when the window is not on Metal; gv_native_mtl_error() says why.
GV_API GvNativeMetal *gv_native_mtl_create(void *window);

// Qt's device and queue. 0 when the bridge is stale.
GV_API int gv_native_mtl_device(GvNativeMetal *bridge, GvNativeMetalDevice *out);

// The QSGTexture* for `texture` (an id<MTLTexture> of
// MTLPixelFormatRGB10A2Unorm the engine rendered, on Qt's device), wrapped
// once and reused. NULL on failure. Never freed by the caller.
GV_API void *gv_native_mtl_texture(GvNativeMetal *bridge, uint64_t texture, int width, int height);

// 1 once the scene graph this bridge belongs to has gone away (or been
// replaced). A stale bridge must be destroyed and replaced.
GV_API int gv_native_mtl_stale(GvNativeMetal *bridge);

// Free the bridge. Its textures went with the scene graph (or go now, if it
// is still alive -- then only call this on the render thread).
GV_API void gv_native_mtl_destroy(GvNativeMetal *bridge);

#ifdef __cplusplus
}  // extern "C"
#endif

#endif  // GRAVITAS_NATIVE_METAL_H
