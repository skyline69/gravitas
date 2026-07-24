// Linux Vulkan zero-copy video bridge -- see gravitas_video_bridge_vk.h.
//
// mpv renders through OpenGL into an exportable VkImage that Qt's Vulkan
// device samples. The shared surface is opaque-FD external memory: Vulkan (on
// Qt's device) allocates it, OpenGL (on an EGL context matched to the same
// physical GPU by device UUID) imports the FD and renders into it, and a
// shared semaphore orders the GL write before the Vulkan sample. This is the
// Linux/Vulkan sibling of native/macos/, and every threading and lifetime rule
// is inherited from it.
//
// This translation unit currently implements create() and a minimal teardown.
// set_size / render / texture are filled in by later tasks.

#include "gravitas_video_bridge_vk.h"

// Vulkan first: Qt's qvulkaninstance.h defines VK_NO_PROTOTYPES before it
// includes vulkan.h, which would hide the core prototypes this file calls
// (vkGetPhysicalDeviceProperties2). Including it here with prototypes on makes
// Qt's later include a no-op while leaving the symbols declared; -lvulkan
// resolves them.
#include <vulkan/vulkan.h>

#include <QtCore/QCoreApplication>
#include <QtGui/rhi/qrhi.h>
#include <QtGui/rhi/qrhi_platform.h>
#include <QtGui/QVulkanInstance>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGRendererInterface>

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GL/gl.h>
#include <GL/glext.h>

#include <dlfcn.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#ifndef EGL_NO_CONFIG_KHR
#define EGL_NO_CONFIG_KHR ((EGLConfig)0)
#endif

namespace {

// --- libmpv's render API, as much of it as this needs -------------------
//
// Mirrors libmpv/render.h and render_gl.h. These are part of libmpv's stable
// ABI, so declaring them here is safe and saves depending on mpv's headers at
// build time. libmpv is reached through dlsym rather than linked: python-mpv
// has already loaded it, and this way the bridge carries no build-time mpv
// dependency (and no version skew from one).

using MpvRenderContext = struct mpv_render_context;

struct MpvRenderParam {
    int type;
    void *data;
};

enum {
    MPV_RENDER_PARAM_INVALID = 0,
    MPV_RENDER_PARAM_API_TYPE = 1,
    MPV_RENDER_PARAM_OPENGL_INIT_PARAMS = 2,
    MPV_RENDER_PARAM_OPENGL_FBO = 3,
    MPV_RENDER_PARAM_FLIP_Y = 4,
    MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME = 12,
};

struct MpvOpenGLInitParams {
    void *(*get_proc_address)(void *ctx, const char *name);
    void *get_proc_address_ctx;
};

struct MpvOpenGLFBO {
    int fbo;
    int w;
    int h;
    int internal_format;
};

using MpvRenderContextCreate = int (*)(MpvRenderContext **, void *, MpvRenderParam *);
using MpvRenderContextSetUpdateCallback = void (*)(MpvRenderContext *, void (*)(void *), void *);
using MpvRenderContextRender = int (*)(MpvRenderContext *, MpvRenderParam *);
using MpvRenderContextFree = void (*)(MpvRenderContext *);

struct MpvRenderApi {
    MpvRenderContextCreate create = nullptr;
    MpvRenderContextSetUpdateCallback setUpdateCallback = nullptr;
    MpvRenderContextRender render = nullptr;
    MpvRenderContextFree free = nullptr;

    bool resolve()
    {
        if (create)
            return true;
        // RTLD_NOLOAD first: python-mpv loaded libmpv through ctypes
        // (RTLD_LOCAL), so its symbols are absent from the global namespace but
        // the image is present. Naming it again with NOLOAD gets a handle to
        // that same core rather than risking a second one.
        static const char *names[] = {
            "libmpv.so.2",
            "libmpv.so",
            "/usr/lib64/libmpv.so.2",
        };
        void *handle = nullptr;
        for (const char *name : names) {
            handle = dlopen(name, RTLD_LAZY | RTLD_NOLOAD);
            if (handle)
                break;
        }
        if (!handle) {
            for (const char *name : names) {
                handle = dlopen(name, RTLD_LAZY);
                if (handle)
                    break;
            }
        }
        if (!handle)
            return false;
        create = (MpvRenderContextCreate)dlsym(handle, "mpv_render_context_create");
        setUpdateCallback = (MpvRenderContextSetUpdateCallback)dlsym(
            handle, "mpv_render_context_set_update_callback");
        render = (MpvRenderContextRender)dlsym(handle, "mpv_render_context_render");
        free = (MpvRenderContextFree)dlsym(handle, "mpv_render_context_free");
        return create && setUpdateCallback && render && free;
    }
};

MpvRenderApi g_mpv;

// Reported through gv_video_bridge_vk_error(). Thread-local: the render thread
// and the GUI thread can both be in here during teardown.
thread_local std::string g_error;

void set_error(const char *message) { g_error = message ? message : ""; }

void logf(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    std::fprintf(stderr, "gravitas-vk: ");
    std::vfprintf(stderr, fmt, args);
    std::fprintf(stderr, "\n");
    va_end(args);
}

// mpv asks for GL entry points by name; its context is our EGL context.
void *gl_symbol(void *, const char *name)
{
    return reinterpret_cast<void *>(eglGetProcAddress(name));
}

// The GL external-object entry points the surface path needs. Loaded once in
// create(); a missing one means this driver cannot do opaque-FD interop and
// create() fails so the caller falls back.
struct GlExt {
    PFNGLGETUNSIGNEDBYTEI_VEXTPROC getUnsignedBytevi = nullptr;
    PFNGLCREATEMEMORYOBJECTSEXTPROC createMemoryObjects = nullptr;
    PFNGLIMPORTMEMORYFDEXTPROC importMemoryFd = nullptr;
    PFNGLTEXTURESTORAGEMEM2DEXTPROC textureStorageMem2D = nullptr;
    PFNGLDELETEMEMORYOBJECTSEXTPROC deleteMemoryObjects = nullptr;
    PFNGLGENSEMAPHORESEXTPROC genSemaphores = nullptr;
    PFNGLDELETESEMAPHORESEXTPROC deleteSemaphores = nullptr;
    PFNGLIMPORTSEMAPHOREFDEXTPROC importSemaphoreFd = nullptr;
    PFNGLSIGNALSEMAPHOREEXTPROC signalSemaphore = nullptr;
    PFNGLWAITSEMAPHOREEXTPROC waitSemaphore = nullptr;
    // Core 4.5 DSA, loaded through EGL for uniformity. (glDeleteTextures is
    // core 1.1 and linked directly, so it is not here.)
    PFNGLCREATETEXTURESPROC createTextures = nullptr;
    PFNGLTEXTUREPARAMETERIPROC textureParameteri = nullptr;
    PFNGLCREATEFRAMEBUFFERSPROC createFramebuffers = nullptr;
    PFNGLDELETEFRAMEBUFFERSPROC deleteFramebuffers = nullptr;
    PFNGLNAMEDFRAMEBUFFERTEXTUREPROC namedFramebufferTexture = nullptr;

    bool load()
    {
        auto get = [](const char *name) {
            return reinterpret_cast<void *>(eglGetProcAddress(name));
        };
        getUnsignedBytevi = (PFNGLGETUNSIGNEDBYTEI_VEXTPROC)get("glGetUnsignedBytei_vEXT");
        createMemoryObjects = (PFNGLCREATEMEMORYOBJECTSEXTPROC)get("glCreateMemoryObjectsEXT");
        importMemoryFd = (PFNGLIMPORTMEMORYFDEXTPROC)get("glImportMemoryFdEXT");
        textureStorageMem2D = (PFNGLTEXTURESTORAGEMEM2DEXTPROC)get("glTextureStorageMem2DEXT");
        deleteMemoryObjects = (PFNGLDELETEMEMORYOBJECTSEXTPROC)get("glDeleteMemoryObjectsEXT");
        genSemaphores = (PFNGLGENSEMAPHORESEXTPROC)get("glGenSemaphoresEXT");
        deleteSemaphores = (PFNGLDELETESEMAPHORESEXTPROC)get("glDeleteSemaphoresEXT");
        importSemaphoreFd = (PFNGLIMPORTSEMAPHOREFDEXTPROC)get("glImportSemaphoreFdEXT");
        signalSemaphore = (PFNGLSIGNALSEMAPHOREEXTPROC)get("glSignalSemaphoreEXT");
        waitSemaphore = (PFNGLWAITSEMAPHOREEXTPROC)get("glWaitSemaphoreEXT");
        createTextures = (PFNGLCREATETEXTURESPROC)get("glCreateTextures");
        textureParameteri = (PFNGLTEXTUREPARAMETERIPROC)get("glTextureParameteri");
        createFramebuffers = (PFNGLCREATEFRAMEBUFFERSPROC)get("glCreateFramebuffers");
        deleteFramebuffers = (PFNGLDELETEFRAMEBUFFERSPROC)get("glDeleteFramebuffers");
        namedFramebufferTexture = (PFNGLNAMEDFRAMEBUFFERTEXTUREPROC)get("glNamedFramebufferTexture");
        return getUnsignedBytevi && createMemoryObjects && importMemoryFd && textureStorageMem2D
            && deleteMemoryObjects && genSemaphores && deleteSemaphores && importSemaphoreFd
            && signalSemaphore && waitSemaphore && createTextures && textureParameteri
            && createFramebuffers && deleteFramebuffers && namedFramebufferTexture;
    }
};

}  // namespace

struct GvVideoBridgeVk {
    QQuickWindow *window = nullptr;
    QRhi *rhi = nullptr;

    // Qt's Vulkan device, borrowed -- never destroyed here.
    VkInstance instance = VK_NULL_HANDLE;
    VkPhysicalDevice physicalDevice = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    uint32_t queueFamily = 0;
    uint8_t deviceUuid[VK_UUID_SIZE] = {};

    // Our own EGL/OpenGL context for mpv, on the same physical GPU.
    EGLDisplay egl = EGL_NO_DISPLAY;
    EGLContext eglContext = EGL_NO_CONTEXT;
    GlExt gl;

    MpvRenderContext *mpv = nullptr;
    // The item to wake when mpv has a frame. Read and written only on the GUI
    // thread -- mpv's thread never touches it (see on_mpv_update).
    QObject *item = nullptr;
    QMetaObject::Connection itemGone;

    QMetaObject::Connection invalidated;
    bool torn = false;
};

namespace {

// mpv's update callback, on mpv's thread. It must NOT read bridge->item here:
// the item can start dying between the check and the post, a race the item's
// destruction wins often enough to crash. Post to the APPLICATION (which
// outlives every item and window) and read bridge->item only inside the
// lambda, which runs on the GUI thread -- the same thread that clears it -- so
// the pointer cannot go stale underneath it.
void on_mpv_update(void *ctx)
{
    auto *bridge = static_cast<GvVideoBridgeVk *>(ctx);
    QCoreApplication *app = QCoreApplication::instance();
    if (!app)
        return;
    QMetaObject::invokeMethod(
        app,
        [bridge]() {
            if (bridge->torn || !bridge->item)
                return;
            QMetaObject::invokeMethod(bridge->item, "requestUpdate", Qt::DirectConnection);
        },
        Qt::QueuedConnection);
}

// Free the render-thread resources. Runs on the render thread, either from
// sceneGraphInvalidated or from destroy().
void tear_down(GvVideoBridgeVk *bridge)
{
    if (bridge->torn)
        return;
    bridge->torn = true;
    if (bridge->mpv) {
        g_mpv.setUpdateCallback(bridge->mpv, nullptr, nullptr);
        g_mpv.free(bridge->mpv);
        bridge->mpv = nullptr;
    }
    if (bridge->egl != EGL_NO_DISPLAY) {
        eglMakeCurrent(bridge->egl, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (bridge->eglContext != EGL_NO_CONTEXT)
            eglDestroyContext(bridge->egl, bridge->eglContext);
        eglTerminate(bridge->egl);
        bridge->egl = EGL_NO_DISPLAY;
        bridge->eglContext = EGL_NO_CONTEXT;
    }
}

// Create a surfaceless EGL/OpenGL context on the EGL device whose GL device
// UUID matches Qt's Vulkan physical device. Returns true and fills egl/context
// on success. The context is left current on the calling (render) thread.
bool bring_up_egl(GvVideoBridgeVk *bridge)
{
    auto queryDevices = (PFNEGLQUERYDEVICESEXTPROC)eglGetProcAddress("eglQueryDevicesEXT");
    auto getPlatformDisplay =
        (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
    if (!queryDevices || !getPlatformDisplay) {
        set_error("EGL device enumeration extensions missing");
        return false;
    }

    EGLDeviceEXT devices[16];
    EGLint deviceCount = 0;
    if (!queryDevices(16, devices, &deviceCount) || deviceCount == 0) {
        set_error("no EGL devices found");
        return false;
    }

    for (EGLint i = 0; i < deviceCount; ++i) {
        EGLDisplay dpy = getPlatformDisplay(EGL_PLATFORM_DEVICE_EXT, devices[i], nullptr);
        if (dpy == EGL_NO_DISPLAY)
            continue;
        if (!eglInitialize(dpy, nullptr, nullptr))
            continue;
        if (!eglBindAPI(EGL_OPENGL_API)) {
            eglTerminate(dpy);
            continue;
        }
        const EGLint attribs[] = {EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 5,
                                  EGL_NONE};
        EGLContext ctx = eglCreateContext(dpy, EGL_NO_CONFIG_KHR, EGL_NO_CONTEXT, attribs);
        if (ctx == EGL_NO_CONTEXT) {
            eglTerminate(dpy);
            continue;
        }
        if (!eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, ctx)) {
            eglDestroyContext(dpy, ctx);
            eglTerminate(dpy);
            continue;
        }

        // Compare this GL device's UUID to the Vulkan physical device's.
        auto getBytevi =
            (PFNGLGETUNSIGNEDBYTEI_VEXTPROC)eglGetProcAddress("glGetUnsignedBytei_vEXT");
        GLubyte glUuid[GL_UUID_SIZE_EXT] = {};
        bool match = false;
        if (getBytevi) {
            getBytevi(GL_DEVICE_UUID_EXT, 0, glUuid);
            match = std::memcmp(glUuid, bridge->deviceUuid, VK_UUID_SIZE) == 0;
        }
        if (match) {
            bridge->egl = dpy;
            bridge->eglContext = ctx;
            return true;
        }
        eglMakeCurrent(dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        eglDestroyContext(dpy, ctx);
        eglTerminate(dpy);
    }
    set_error("no EGL device matches the Vulkan GPU");
    return false;
}

}  // namespace

extern "C" {

int gv_video_bridge_vk_abi(void) { return GV_VIDEO_BRIDGE_VK_ABI; }

const char *gv_video_bridge_vk_qt_version(void) { return qVersion(); }

const char *gv_video_bridge_vk_error(void) { return g_error.c_str(); }

GvVideoBridgeVk *gv_video_bridge_vk_create(void *window, void *mpv)
{
    auto *win = static_cast<QQuickWindow *>(window);
    if (!win) {
        set_error("null window");
        return nullptr;
    }
    QSGRendererInterface *rif = win->rendererInterface();
    if (!rif || rif->graphicsApi() != QSGRendererInterface::GraphicsApi::Vulkan) {
        set_error("scene graph is not on the Vulkan RHI");
        return nullptr;
    }
    auto *rhi = static_cast<QRhi *>(rif->getResource(win, QSGRendererInterface::RhiResource));
    if (!rhi) {
        set_error("no QRhi on the window");
        return nullptr;
    }
    const auto *nh = static_cast<const QRhiVulkanNativeHandles *>(rhi->nativeHandles());
    if (!nh || nh->physDev == VK_NULL_HANDLE || nh->dev == VK_NULL_HANDLE) {
        set_error("QRhi exposed no Vulkan device");
        return nullptr;
    }

    if (!g_mpv.resolve()) {
        set_error("libmpv render API not found");
        return nullptr;
    }

    auto *bridge = new GvVideoBridgeVk();
    bridge->window = win;
    bridge->rhi = rhi;
    bridge->instance = nh->inst ? nh->inst->vkInstance() : VK_NULL_HANDLE;
    bridge->physicalDevice = nh->physDev;
    bridge->device = nh->dev;
    bridge->queue = nh->gfxQueue;
    bridge->queueFamily = nh->gfxQueueFamilyIdx;

    // The Vulkan physical device's UUID, to match an EGL device against.
    VkPhysicalDeviceIDProperties idProps{};
    idProps.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES;
    VkPhysicalDeviceProperties2 props2{};
    props2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
    props2.pNext = &idProps;
    vkGetPhysicalDeviceProperties2(bridge->physicalDevice, &props2);
    std::memcpy(bridge->deviceUuid, idProps.deviceUUID, VK_UUID_SIZE);
    logf("Vulkan device: %s", props2.properties.deviceName);

    if (!bring_up_egl(bridge)) {
        tear_down(bridge);
        delete bridge;
        return nullptr;
    }
    logf("EGL device matched Vulkan GPU by UUID; surfaceless GL context up");

    if (!bridge->gl.load()) {
        set_error("GL external-object extensions missing (EXT_memory_object/EXT_semaphore)");
        tear_down(bridge);
        delete bridge;
        return nullptr;
    }

    MpvOpenGLInitParams glInit{gl_symbol, nullptr};
    MpvRenderParam params[] = {
        {MPV_RENDER_PARAM_API_TYPE, const_cast<char *>("opengl")},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &glInit},
        {MPV_RENDER_PARAM_INVALID, nullptr},
    };
    int rc = g_mpv.create(&bridge->mpv, mpv, params);
    if (rc < 0 || !bridge->mpv) {
        set_error("mpv_render_context_create failed");
        tear_down(bridge);
        delete bridge;
        return nullptr;
    }
    g_mpv.setUpdateCallback(bridge->mpv, on_mpv_update, bridge);
    logf("mpv render context created; bridge ready (no surface yet)");

    // Teardown on the render thread when the scene graph goes away. Direct
    // connection so it runs on the emitting (render) thread, where the GL
    // context and mpv context must be freed.
    bridge->invalidated = QObject::connect(
        win, &QQuickWindow::sceneGraphInvalidated, win,
        [bridge]() { tear_down(bridge); }, Qt::DirectConnection);

    return bridge;
}

void gv_video_bridge_vk_destroy(GvVideoBridgeVk *bridge)
{
    if (!bridge)
        return;
    QObject::disconnect(bridge->itemGone);
    QObject::disconnect(bridge->invalidated);
    tear_down(bridge);
    delete bridge;
}

int gv_video_bridge_vk_set_size(GvVideoBridgeVk *bridge, int width, int height)
{
    (void)bridge;
    (void)width;
    (void)height;
    set_error("not implemented");
    return 0;
}

void gv_video_bridge_vk_set_item(GvVideoBridgeVk *bridge, void *itemPtr)
{
    auto *item = static_cast<QObject *>(itemPtr);
    if (!bridge || bridge->item == item)
        return;
    QObject::disconnect(bridge->itemGone);
    bridge->item = item;
    if (!item)
        return;
    // The player page is destroyed and rebuilt for every playback, so the item
    // this points at is routinely outlived by the bridge. Clear it the moment
    // it dies, on the GUI thread, so on_mpv_update never wakes a freed item.
    bridge->itemGone =
        QObject::connect(item, &QObject::destroyed, [bridge]() { bridge->item = nullptr; });
}

int gv_video_bridge_vk_stale(GvVideoBridgeVk *bridge)
{
    return (bridge && bridge->torn) ? 1 : 0;
}

int gv_video_bridge_vk_render(GvVideoBridgeVk *bridge)
{
    (void)bridge;
    set_error("not implemented");
    return 0;
}

void *gv_video_bridge_vk_texture(GvVideoBridgeVk *bridge)
{
    (void)bridge;
    return nullptr;
}

const char *gv_video_bridge_vk_format(GvVideoBridgeVk *bridge)
{
    (void)bridge;
    return "";
}

}  // extern "C"
