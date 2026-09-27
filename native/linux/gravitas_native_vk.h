// C ABI for the native engine's zero-copy video on Linux/Vulkan.
//
// The native engine (native/player/) renders with libplacebo. To render
// straight into an image Qt's scene graph samples, libplacebo has to work on
// Qt's own Vulkan device -- and three things about that device only C++ can
// reach: its handles (through QRhi), wrapping a VkImage as a QSGTexture, and
// the moment the scene graph goes away, which is announced on the RENDER
// thread. Python must not run there at that moment: the GUI thread can be
// holding the GIL while it waits for the render thread (see
// gravitas_video_bridge_vk.h for the deadlock that taught this). So teardown
// runs from C++ straight into a callback the engine registered, with no
// Python in between.
//
// Built into the same library as the mpv bridge on Linux, and into a library
// of its own on Windows (gravitas_native_vk.dll), where mpv stays on OpenGL
// and has no bridge (scripts/build_video_bridge.py). Loaded by
// presentation/video/native_vk_bridge.py. Additive: a library without these
// symbols simply leaves the native engine on its readback path.
//
// Nothing here is Linux's: Qt's handles, QRhi and the engine's device are the
// same on every platform with a Vulkan driver, which is why the file is shared.
//
// DECODING INTO THE SAME DEVICE. Qt's own device has a graphics queue and
// nothing else, so hardware decoding cannot write into it. The engine can
// instead make a device with video decode queues (its SharedDevice) on an
// instance Qt also uses, and the window adopts that device before its scene
// graph starts: gv_native_vk_instance(), then the engine, then
// gv_native_vk_adopt(). Everything above then runs on that device unchanged.
//
// THREADING. create/device/texture/on_invalidate run on Qt's render thread
// (inside updatePaintNode); destroy and stale on any thread; instance and
// adopt on the GUI thread, before the window's scene graph initialises.
//
// LIFETIME. Textures handed out belong to the bridge (attach them with
// setOwnsTexture(false)) and live until the scene graph goes away: Qt's batch
// renderer keeps using a texture for frames after the node that referenced it
// is gone. The VkImages behind them belong to the engine, which keeps them
// alive until its invalidation callback runs.

#ifndef GRAVITAS_NATIVE_VK_H
#define GRAVITAS_NATIVE_VK_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifndef GV_API
#ifdef _WIN32
#define GV_API __declspec(dllexport)
#else
#define GV_API __attribute__((visibility("default")))
#endif
#endif

// Bumped whenever an entry point below changes shape. Checked by the loader on
// Windows, where this is a library of its own; on Linux the mpv bridge's ABI
// covers the whole library.
#define GV_NATIVE_VK_ABI 1

// Qt's Vulkan device, in the shape libplacebo's pl_vulkan_import wants it.
// Handles are passed as integers so the Python side needs no Vulkan types.
typedef struct GvNativeVkDevice {
    uint64_t instance;               // VkInstance
    uint64_t get_instance_proc_addr; // PFN_vkGetInstanceProcAddr
    uint64_t physical_device;        // VkPhysicalDevice
    uint64_t device;                 // VkDevice
    uint32_t queue_family;           // Qt's graphics queue family...
    uint32_t queue_index;            // ...and queue within it
    uint32_t api_version;            // VK_MAKE_API_VERSION of what Qt runs
    uint32_t reserved;
    // The features Qt enabled at device creation, as a VkPhysicalDeviceFeatures2
    // chain (1.1-1.3 structs as the API version allows). Owned by the bridge.
    uint64_t features;
} GvNativeVkDevice;

typedef struct GvNativeVk GvNativeVk;

// The Vulkan instance for the scene graph and the engine's shared device.
typedef struct GvNativeVkInstance {
    uint64_t instance;               // VkInstance
    uint64_t get_instance_proc_addr; // PFN_vkGetInstanceProcAddr
} GvNativeVkInstance;

// Why the last call that failed on this thread failed.
GV_API const char *gv_native_vk_error(void);

// GV_NATIVE_VK_ABI, as built.
GV_API int gv_native_vk_abi(void);

// The Qt version the library was built against (QT_VERSION_STR): QRhi has no
// binary compatibility guarantee, so the loader refuses a library built for
// another Qt.
GV_API const char *gv_native_vk_qt_version(void);

// 1 when the Vulkan instance below can be made and has a GPU -- not a CPU
// rasteriser -- with Vulkan 1.2, which libplacebo needs. Asked before the
// scene graph's API is chosen, the last moment another one can be.
GV_API int gv_native_vk_gpu(void);

// Creates (once per process) the QVulkanInstance the window and the engine's
// shared device use: Vulkan 1.3, with the validation layer under
// QSG_RHI_DEBUG_LAYER=1, like Qt's own. Never destroyed -- the device made on
// it never is. 0 on failure; gv_native_vk_error() says why.
GV_API int gv_native_vk_instance(GvNativeVkInstance *out);

// Has `window` (a QQuickWindow* whose scene graph has not started) render on
// that instance, with the engine's device and its graphics queue. 0 on
// failure (no instance yet, or the scene graph already started).
GV_API int gv_native_vk_adopt(void *window, uint64_t physical_device, uint64_t device,
                              uint32_t queue_family, uint32_t queue_index);

// Reach Qt's Vulkan device for `window` (a QQuickWindow* on the Vulkan RHI).
// NULL when the window is not on Vulkan; gv_native_vk_error() says why.
GV_API GvNativeVk *gv_native_vk_create(void *window);

// Qt's device. Valid while the bridge lives.
GV_API const GvNativeVkDevice *gv_native_vk_device(GvNativeVk *bridge);

// Called once, on the render thread, when the scene graph goes away -- before
// Qt destroys the device -- so the engine can free everything it created on
// it. Registering again replaces the previous callback.
GV_API void gv_native_vk_on_invalidate(GvNativeVk *bridge, void (*callback)(void *), void *context);

// The QSGTexture* for `image` (a VK_FORMAT_R8G8B8A8_UNORM VkImage the engine
// leaves in VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL), wrapped once and reused.
// NULL on failure. Never freed by the caller.
GV_API void *gv_native_vk_texture(GvNativeVk *bridge, uint64_t image, int width, int height);

// 1 once the scene graph this bridge belongs to has gone away. A stale bridge
// must be destroyed and replaced.
GV_API int gv_native_vk_stale(GvNativeVk *bridge);

// Free the bridge. Its textures went with the scene graph (or go now, if it is
// still alive -- then only call this on the render thread).
GV_API void gv_native_vk_destroy(GvNativeVk *bridge);

#ifdef __cplusplus
}  // extern "C"
#endif

#endif  // GRAVITAS_NATIVE_VK_H
