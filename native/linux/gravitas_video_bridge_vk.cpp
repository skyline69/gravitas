// Linux Vulkan zero-copy video bridge -- see gravitas_video_bridge_vk.h.
//
// SKELETON. Every entry point is defined so the ctypes loader can bind the
// library and accept its ABI/Qt version, but the bridge does no work yet:
// create() fails cleanly, so under GRAVITAS_GRAPHICS=vulkan the app runs on
// the Vulkan RHI with a black video item (logged), never a crash. The real
// implementation lands in later tasks: create (device match, EGL, mpv
// context), set_size (exportable image ring, GL import, QSGTexture), render
// (cross-API sync), teardown.

#include "gravitas_video_bridge_vk.h"

#include <QtCore/qglobal.h>

#include <string>

namespace {

// Thread-local so a failure reason set on the render thread is read back by
// the same thread's next call, never torn by another.
thread_local std::string g_error;

void set_error(const char *message) { g_error = message ? message : ""; }

}  // namespace

extern "C" {

int gv_video_bridge_vk_abi(void) { return GV_VIDEO_BRIDGE_VK_ABI; }

const char *gv_video_bridge_vk_qt_version(void) { return qVersion(); }

const char *gv_video_bridge_vk_error(void) { return g_error.c_str(); }

GvVideoBridgeVk *gv_video_bridge_vk_create(void *window, void *mpv) {
    (void)window;
    (void)mpv;
    set_error("not implemented");
    return nullptr;
}

void gv_video_bridge_vk_destroy(GvVideoBridgeVk *bridge) { (void)bridge; }

int gv_video_bridge_vk_set_size(GvVideoBridgeVk *bridge, int width, int height) {
    (void)bridge;
    (void)width;
    (void)height;
    set_error("not implemented");
    return 0;
}

void gv_video_bridge_vk_set_item(GvVideoBridgeVk *bridge, void *item) {
    (void)bridge;
    (void)item;
}

int gv_video_bridge_vk_stale(GvVideoBridgeVk *bridge) {
    (void)bridge;
    return 0;
}

int gv_video_bridge_vk_render(GvVideoBridgeVk *bridge) {
    (void)bridge;
    set_error("not implemented");
    return 0;
}

void *gv_video_bridge_vk_texture(GvVideoBridgeVk *bridge) {
    (void)bridge;
    return nullptr;
}

const char *gv_video_bridge_vk_format(GvVideoBridgeVk *bridge) {
    (void)bridge;
    return "";
}

}  // extern "C"
