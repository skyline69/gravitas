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
#include <QtQuick/QSGTexture>

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

// One video resolution's worth of GPU objects: a VkImage (allocated on Qt's
// device, exportable) that OpenGL imports by FD and renders into, wrapped as a
// QSGTexture Qt samples. Replaced when the resolution changes; retired rather
// than freed while the renderer may still hold it.
struct Surface {
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    GLuint glMemory = 0;  // GL memory object importing `memory`'s FD
    GLuint glTexture = 0;
    GLuint fbo = 0;
    QRhiTexture *rhiTexture = nullptr;  // owned by sceneTexture
    QSGTexture *sceneTexture = nullptr;
    // "GL is done writing this slot": signalled by GL after mpv renders, waited
    // by Qt's Vulkan queue before it samples. The wait carries the memory of
    // the GL writes across to Vulkan -- glFinish alone does not.
    VkSemaphore glDoneVk = VK_NULL_HANDLE;
    GLuint glDoneGl = 0;
    int width = 0;
    int height = 0;
};

}  // namespace

constexpr int kRingSize = 3;

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

    // External-memory / -semaphore FD entry points, loaded from Qt's device.
    PFN_vkGetMemoryFdKHR getMemoryFd = nullptr;
    PFN_vkGetSemaphoreFdKHR getSemaphoreFd = nullptr;

    // For the per-frame acquire barrier that makes GL's writes visible to Qt's
    // sampler. Reused every frame; owned by the bridge.
    VkCommandPool cmdPool = VK_NULL_HANDLE;
    VkCommandBuffer cmdBuffer = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;

    MpvRenderContext *mpv = nullptr;
    // The item to wake when mpv has a frame. Read and written only on the GUI
    // thread -- mpv's thread never touches it (see on_mpv_update).
    QObject *item = nullptr;
    QMetaObject::Connection itemGone;

    // The surface ring and the resolution it was built for. `current` is the
    // slot last rendered and handed to the node.
    Surface ring[kRingSize];
    int current = 0;
    int surfaceWidth = 0;
    int surfaceHeight = 0;
    // Surfaces the batch renderer may still be sampling. Qt gives no way to
    // ask, so they wait here until the scene graph is gone.
    std::vector<Surface> retired;

    QMetaObject::Connection invalidated;
    bool torn = false;
};

namespace {

uint32_t pick_memory_type(GvVideoBridgeVk *bridge, uint32_t typeBits, VkMemoryPropertyFlags want)
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(bridge->physicalDevice, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i) {
        if ((typeBits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want)
            return i;
    }
    return UINT32_MAX;
}

void destroy_surface(GvVideoBridgeVk *bridge, Surface &s)
{
    delete s.sceneTexture;  // frees the QRhiTexture it owns
    if (s.glDoneGl)
        bridge->gl.deleteSemaphores(1, &s.glDoneGl);
    if (s.glDoneVk)
        vkDestroySemaphore(bridge->device, s.glDoneVk, nullptr);
    if (s.fbo)
        bridge->gl.deleteFramebuffers(1, &s.fbo);
    if (s.glTexture)
        glDeleteTextures(1, &s.glTexture);
    if (s.glMemory)
        bridge->gl.deleteMemoryObjects(1, &s.glMemory);
    if (s.image)
        vkDestroyImage(bridge->device, s.image, nullptr);
    if (s.memory)
        vkFreeMemory(bridge->device, s.memory, nullptr);
    s = Surface{};
}

// Allocate one exportable VkImage on Qt's device, import it into GL by FD, wrap
// it in a GL FBO for mpv and as a QSGTexture for Qt. The EGL context must be
// current. Returns false with g_error set on any failure.
bool make_surface(GvVideoBridgeVk *bridge, int width, int height, Surface &out)
{
    VkExternalMemoryImageCreateInfo extImage{};
    extImage.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO;
    extImage.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;

    VkImageCreateInfo ici{};
    ici.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
    ici.pNext = &extImage;
    ici.imageType = VK_IMAGE_TYPE_2D;
    ici.format = VK_FORMAT_R8G8B8A8_UNORM;
    ici.extent = {static_cast<uint32_t>(width), static_cast<uint32_t>(height), 1};
    ici.mipLevels = 1;
    ici.arrayLayers = 1;
    ici.samples = VK_SAMPLE_COUNT_1_BIT;
    ici.tiling = VK_IMAGE_TILING_OPTIMAL;
    ici.usage = VK_IMAGE_USAGE_SAMPLED_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT
        | VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    ici.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ici.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    if (vkCreateImage(bridge->device, &ici, nullptr, &out.image) != VK_SUCCESS) {
        set_error("vkCreateImage failed");
        return false;
    }

    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(bridge->device, out.image, &req);
    uint32_t typeIdx =
        pick_memory_type(bridge, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (typeIdx == UINT32_MAX) {
        set_error("no device-local memory type for the surface");
        return false;
    }

    VkMemoryDedicatedAllocateInfo dedicated{};
    dedicated.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO;
    dedicated.image = out.image;
    VkExportMemoryAllocateInfo exportInfo{};
    exportInfo.sType = VK_STRUCTURE_TYPE_EXPORT_MEMORY_ALLOCATE_INFO;
    exportInfo.pNext = &dedicated;
    exportInfo.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
    VkMemoryAllocateInfo mai{};
    mai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
    mai.pNext = &exportInfo;
    mai.allocationSize = req.size;
    mai.memoryTypeIndex = typeIdx;
    if (vkAllocateMemory(bridge->device, &mai, nullptr, &out.memory) != VK_SUCCESS) {
        set_error("vkAllocateMemory failed");
        return false;
    }
    vkBindImageMemory(bridge->device, out.image, out.memory, 0);

    VkMemoryGetFdInfoKHR getFd{};
    getFd.sType = VK_STRUCTURE_TYPE_MEMORY_GET_FD_INFO_KHR;
    getFd.memory = out.memory;
    getFd.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
    int fd = -1;
    if (bridge->getMemoryFd(bridge->device, &getFd, &fd) != VK_SUCCESS || fd < 0) {
        set_error("vkGetMemoryFdKHR failed");
        return false;
    }

    // GL imports the FD (taking ownership of it) and backs a texture with that
    // memory. Tiling must match the VkImage (OPTIMAL) or the sample is garbage.
    bridge->gl.createMemoryObjects(1, &out.glMemory);
    bridge->gl.importMemoryFd(out.glMemory, req.size, GL_HANDLE_TYPE_OPAQUE_FD_EXT, fd);
    bridge->gl.createTextures(GL_TEXTURE_2D, 1, &out.glTexture);
    bridge->gl.textureParameteri(out.glTexture, GL_TEXTURE_TILING_EXT, GL_OPTIMAL_TILING_EXT);
    bridge->gl.textureStorageMem2D(out.glTexture, 1, GL_RGBA8, width, height, out.glMemory, 0);
    if (GLenum err = glGetError()) {
        char msg[64];
        std::snprintf(msg, sizeof(msg), "GL memory import failed (0x%x)", err);
        set_error(msg);
        return false;
    }
    bridge->gl.createFramebuffers(1, &out.fbo);
    bridge->gl.namedFramebufferTexture(out.fbo, GL_COLOR_ATTACHMENT0, out.glTexture, 0);

    out.rhiTexture = bridge->rhi->newTexture(QRhiTexture::RGBA8, QSize(width, height), 1);
    QRhiTexture::NativeTexture native{quint64(out.image), VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
    if (!out.rhiTexture->createFrom(native)) {
        delete out.rhiTexture;
        out.rhiTexture = nullptr;
        set_error("QRhiTexture::createFrom failed");
        return false;
    }
    out.sceneTexture =
        bridge->window->createTextureFromRhiTexture(out.rhiTexture, QQuickWindow::TextureIsOpaque);
    if (!out.sceneTexture) {
        delete out.rhiTexture;
        out.rhiTexture = nullptr;
        set_error("createTextureFromRhiTexture failed");
        return false;
    }

    // The GL-done semaphore: an exportable binary VkSemaphore imported into GL.
    VkExportSemaphoreCreateInfo exportSem{};
    exportSem.sType = VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO;
    exportSem.handleTypes = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT;
    VkSemaphoreCreateInfo sci{};
    sci.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
    sci.pNext = &exportSem;
    if (vkCreateSemaphore(bridge->device, &sci, nullptr, &out.glDoneVk) != VK_SUCCESS) {
        set_error("vkCreateSemaphore failed");
        return false;
    }
    VkSemaphoreGetFdInfoKHR semFdInfo{};
    semFdInfo.sType = VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR;
    semFdInfo.semaphore = out.glDoneVk;
    semFdInfo.handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_OPAQUE_FD_BIT;
    int semFd = -1;
    if (bridge->getSemaphoreFd(bridge->device, &semFdInfo, &semFd) != VK_SUCCESS || semFd < 0) {
        set_error("vkGetSemaphoreFdKHR failed");
        return false;
    }
    bridge->gl.genSemaphores(1, &out.glDoneGl);
    bridge->gl.importSemaphoreFd(out.glDoneGl, GL_HANDLE_TYPE_OPAQUE_FD_EXT, semFd);

    out.width = width;
    out.height = height;
    return true;
}

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
    // Freeing the GL and QRhi objects needs our context current.
    if (bridge->egl != EGL_NO_DISPLAY)
        eglMakeCurrent(bridge->egl, EGL_NO_SURFACE, EGL_NO_SURFACE, bridge->eglContext);
    for (Surface &s : bridge->ring)
        destroy_surface(bridge, s);
    for (Surface &s : bridge->retired)
        destroy_surface(bridge, s);
    bridge->retired.clear();
    if (bridge->fence) {
        vkDestroyFence(bridge->device, bridge->fence, nullptr);
        bridge->fence = VK_NULL_HANDLE;
    }
    if (bridge->cmdPool) {
        vkDestroyCommandPool(bridge->device, bridge->cmdPool, nullptr);
        bridge->cmdPool = VK_NULL_HANDLE;
    }
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

    // FD export entry points, valid only if main.py requested the matching
    // device extensions before the scene graph came up.
    bridge->getMemoryFd =
        (PFN_vkGetMemoryFdKHR)vkGetDeviceProcAddr(bridge->device, "vkGetMemoryFdKHR");
    bridge->getSemaphoreFd =
        (PFN_vkGetSemaphoreFdKHR)vkGetDeviceProcAddr(bridge->device, "vkGetSemaphoreFdKHR");
    if (!bridge->getMemoryFd)
        logf("warning: VK_KHR_external_memory_fd not enabled on Qt's device; set_size will fail");

    // A command buffer + fence for the per-frame acquire barrier.
    VkCommandPoolCreateInfo poolInfo{};
    poolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
    poolInfo.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    poolInfo.queueFamilyIndex = bridge->queueFamily;
    vkCreateCommandPool(bridge->device, &poolInfo, nullptr, &bridge->cmdPool);
    VkCommandBufferAllocateInfo cbInfo{};
    cbInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
    cbInfo.commandPool = bridge->cmdPool;
    cbInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbInfo.commandBufferCount = 1;
    vkAllocateCommandBuffers(bridge->device, &cbInfo, &bridge->cmdBuffer);
    VkFenceCreateInfo fenceInfo{};
    fenceInfo.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
    vkCreateFence(bridge->device, &fenceInfo, nullptr, &bridge->fence);

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
    if (!bridge || bridge->torn) {
        set_error("bridge is not usable");
        return 0;
    }
    if (width <= 0 || height <= 0) {
        set_error("bad surface size");
        return 0;
    }
    if (width == bridge->surfaceWidth && height == bridge->surfaceHeight)
        return 1;
    if (!bridge->getMemoryFd || !bridge->getSemaphoreFd) {
        set_error("VK_KHR_external_memory_fd/_semaphore_fd not enabled on Qt's device");
        return 0;
    }
    eglMakeCurrent(bridge->egl, EGL_NO_SURFACE, EGL_NO_SURFACE, bridge->eglContext);

    // Retire the old ring rather than freeing it: the batch renderer may still
    // be sampling last frame's texture. They are released at teardown.
    for (Surface &s : bridge->ring) {
        if (s.sceneTexture || s.image)
            bridge->retired.push_back(s);
        s = Surface{};
    }
    for (int i = 0; i < kRingSize; ++i) {
        if (!make_surface(bridge, width, height, bridge->ring[i]))
            return 0;
    }
    bridge->surfaceWidth = width;
    bridge->surfaceHeight = height;
    bridge->current = 0;
    return 1;
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
    if (!bridge || bridge->torn || !bridge->mpv || bridge->surfaceWidth == 0)
        return 0;
    eglMakeCurrent(bridge->egl, EGL_NO_SURFACE, EGL_NO_SURFACE, bridge->eglContext);

    int next = (bridge->current + 1) % kRingSize;
    Surface &s = bridge->ring[next];

    MpvOpenGLFBO fbo{static_cast<int>(s.fbo), s.width, s.height, GL_RGBA8};
    int flipY = 0;
    int block = 0;
    MpvRenderParam params[] = {
        {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
        {MPV_RENDER_PARAM_FLIP_Y, &flipY},
        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block},
        {MPV_RENDER_PARAM_INVALID, nullptr},
    };
    g_mpv.render(bridge->mpv, params);

    // Signal "GL done" and transition the texture to shader-read layout so it
    // is ready for Vulkan to sample. flush so the signal actually reaches the
    // GPU.
    GLenum dstLayout = GL_LAYOUT_SHADER_READ_ONLY_EXT;
    bridge->gl.signalSemaphore(s.glDoneGl, 0, nullptr, 1, &s.glTexture, &dstLayout);
    glFlush();

    // Acquire the image on Qt's device: a barrier, waiting on the GL-done
    // semaphore, that makes GL's writes AVAILABLE and VISIBLE to Qt's sampler.
    // glFinish alone could not do this -- it orders GL's own timeline but leaves
    // the shared memory unacquired on the Vulkan side, which is what showed as a
    // per-frame black flicker. Fence-waited here so the frame is complete and
    // visible before this returns and Qt composites it.
    vkResetCommandBuffer(bridge->cmdBuffer, 0);
    VkCommandBufferBeginInfo begin{};
    begin.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    vkBeginCommandBuffer(bridge->cmdBuffer, &begin);
    VkImageMemoryBarrier barrier{};
    barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER;
    barrier.srcAccessMask = 0;
    barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
    barrier.oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    barrier.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.image = s.image;
    barrier.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(bridge->cmdBuffer, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                         VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 1,
                         &barrier);
    vkEndCommandBuffer(bridge->cmdBuffer);

    VkPipelineStageFlags waitStage = VK_PIPELINE_STAGE_FRAGMENT_SHADER_BIT;
    VkSubmitInfo submit{};
    submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
    submit.waitSemaphoreCount = 1;
    submit.pWaitSemaphores = &s.glDoneVk;
    submit.pWaitDstStageMask = &waitStage;
    submit.commandBufferCount = 1;
    submit.pCommandBuffers = &bridge->cmdBuffer;
    // Same thread (render thread) owns every use of Qt's queue, so no external
    // queue synchronisation is needed.
    vkQueueSubmit(bridge->queue, 1, &submit, bridge->fence);
    vkWaitForFences(bridge->device, 1, &bridge->fence, VK_TRUE, UINT64_MAX);
    vkResetFences(bridge->device, 1, &bridge->fence);

    bridge->current = next;
    return 1;
}

void *gv_video_bridge_vk_texture(GvVideoBridgeVk *bridge)
{
    if (!bridge || bridge->surfaceWidth == 0)
        return nullptr;
    return bridge->ring[bridge->current].sceneTexture;
}

const char *gv_video_bridge_vk_format(GvVideoBridgeVk *bridge)
{
    return (bridge && bridge->surfaceWidth) ? "rgba8" : "";
}

}  // extern "C"
