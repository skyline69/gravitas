// Zero-copy video for macOS: mpv renders through OpenGL into an IOSurface,
// Metal samples the same surface, Qt's scene graph draws it. No frame ever
// touches the CPU.
//
// Why this exists: Qt Quick runs on Metal on macOS (see
// infrastructure/graphics.py -- OpenGL there costs the threaded render loop),
// but libmpv's render API speaks OpenGL and nothing else. An IOSurface is the
// one buffer both APIs can address, so it is the seam between them.
//
// libmpv is reached through dlsym rather than linked: python-mpv has already
// loaded it into the process by the time any of this runs, and resolving the
// four render-API symbols from the running image keeps the build free of an
// mpv dependency (and free of the version skew that would come with one).
//
// The ownership rules were each learned from a crash, and none is guessable
// from the API signatures. They are stated where they bite.

#include "gravitas_video_bridge.h"

#include <QtGui/rhi/qrhi.h>
#include <QtGui/rhi/qrhi_platform.h>
#include <QtCore/QCoreApplication>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGRendererInterface>
#include <QtQuick/QSGTexture>

#import <IOSurface/IOSurfaceObjC.h>
#import <Metal/Metal.h>

#include <OpenGL/CGLIOSurface.h>
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>

#include <dlfcn.h>

#include <atomic>
#include <cstdio>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

namespace {

// --- libmpv's render API, as much of it as this needs -------------------
//
// Mirrors libmpv/render.h and render_gl.h. These are part of libmpv's stable
// ABI, so declaring them here is safe and saves depending on mpv's headers at
// build time.

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

// Three fields, not two: mpv <= 0.35 ends this struct with a deprecated
// `const char *extra_exts` that it dereferences, and 0.36 removed it. A
// two-field struct handed to an old libmpv means mpv strlen()s whatever the
// stack held next -- a segfault inside mpv_render_context_create, on the
// render thread. Homebrew ships a new mpv, so this side has never been bitten;
// the Linux one was, in the released Flatpak. Zero-initialised, both vintages
// read something valid.
struct MpvOpenGLInitParams {
    void *(*get_proc_address)(void *ctx, const char *name);
    void *get_proc_address_ctx;
    const char *extra_exts;
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
        // Not RTLD_DEFAULT: python-mpv loads libmpv through ctypes, which
        // dlopens it RTLD_LOCAL, so its symbols are deliberately absent from
        // the global namespace. The library has to be named again to get at
        // them -- with RTLD_NOLOAD first, which returns a handle only if the
        // image is ALREADY loaded and so cannot give mpv a second core by
        // accident. The plain dlopen after it is for the case where this ever
        // runs before python-mpv has loaded anything.
        static const char *names[] = {
            "libmpv.2.dylib",
            "libmpv.dylib",
            "/opt/homebrew/lib/libmpv.2.dylib",
            "/usr/local/lib/libmpv.2.dylib",
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

// Reported through gv_video_bridge_error(). Thread-local: the render thread
// and the GUI thread can both be in here during teardown.
thread_local std::string g_error;

void setError(const char *message)
{
    g_error = message ? message : "";
}

// mpv asks for GL entry points by name. Its own context is a plain CGL
// context, so the OpenGL framework's symbols are the right ones.
void *glSymbol(void *, const char *name)
{
    static void *framework = dlopen("/System/Library/Frameworks/OpenGL.framework/OpenGL", RTLD_LAZY);
    if (!framework)
        return nullptr;
    return dlsym(framework, name);
}

// The pixel formats the surface can take, best first. Every layer has to
// agree -- IOSurface, Metal, Qt and GL all describe the same memory -- so they
// travel together rather than as four constants that can drift apart.
//
// 10-bit first: HEVC Main 10 is ordinary for film and TV now, and an 8-bit
// surface throws that precision away before Qt ever sees it (visible as
// banding in gradients). 8-bit content loses nothing by going through a
// 10-bit surface, so there is no reason to choose by content.
struct SurfaceFormat {
    unsigned iosurface;
    MTLPixelFormat metal;
    QRhiTexture::Format qt;
    GLenum glInternal;
    GLenum glFormat;
    GLenum glType;
    const char *name;
};

const SurfaceFormat kSurfaceFormats[] = {
    {'l10r', MTLPixelFormatBGR10A2Unorm, QRhiTexture::RGB10A2, GL_RGB10_A2, GL_BGRA,
     GL_UNSIGNED_INT_2_10_10_10_REV, "BGR10A2 (10-bit)"},
    {'BGRA', MTLPixelFormatBGRA8Unorm, QRhiTexture::BGRA8, GL_RGBA, GL_BGRA,
     GL_UNSIGNED_INT_8_8_8_8_REV, "BGRA8 (8-bit)"},
};

// One video resolution's worth of GPU objects. Replaced when the resolution
// changes; never freed before the bridge itself.
struct SurfaceHandles {
    IOSurfaceRef surface = nullptr;
    id<MTLTexture> metalTexture = nil;
    QSGTexture *sceneTexture = nullptr;
    GLuint glTexture = 0;
    GLuint fbo = 0;
    int width = 0;
    int height = 0;
    const SurfaceFormat *format = nullptr;
};

// The same handles, but with exactly one owner.
//
// Not full RAII -- deliberately. Releasing any of this needs the bridge's GL
// context current on Qt's render thread, and a destructor fires wherever the
// object happens to die: a std::vector reallocation would delete a QSGTexture
// on whatever thread pushed. So destruction stays explicit (releaseSurface),
// and the type system is asked to enforce the other half -- that a Surface is
// never ALIASED. Retiring one, or burying it in a Grave, is a move, and the
// move blanks the source. The "copy it across and then blank the original by
// hand" dance this replaces was correct, but only by inspection.
struct Surface : SurfaceHandles {
    Surface() = default;
    Surface(const Surface &) = delete;
    Surface &operator=(const Surface &) = delete;
    Surface(Surface &&other) noexcept
        : SurfaceHandles(std::exchange(static_cast<SurfaceHandles &>(other), {}))
    {
    }
    Surface &operator=(Surface &&other) noexcept
    {
        if (this != &other)
            static_cast<SurfaceHandles &>(*this) =
                std::exchange(static_cast<SurfaceHandles &>(other), {});
        return *this;
    }
};

} // namespace

struct GvVideoBridge {
    QQuickWindow *window = nullptr;
    QRhi *rhi = nullptr;
    id<MTLDevice> device = nil;

    CGLContextObj gl = nullptr;
    CGLContextObj previousContext = nullptr;

    MpvRenderContext *mpv = nullptr;
    // The item currently drawing this bridge. Read and written only on the
    // GUI thread, so mpv's thread can never see it half-updated.
    QObject *item = nullptr;
    QMetaObject::Connection itemGone;

    Surface current;
    // Surfaces the renderer may still be holding. Qt gives no way to ask, so
    // they wait here until the scene graph is gone.
    std::vector<Surface> retired;

    QMetaObject::Connection aboutToStop;
    QMetaObject::Connection initialized;
    QMetaObject::Connection invalidated;
    QMetaObject::Connection windowGone;
    // `torn` means this bridge is finished: its window is gone. There is
    // deliberately no "stopped" latch -- sceneGraphAboutToStop sets one
    // trivially, but nothing reliably clears it (a macOS fullscreen toggle
    // stops the scene graph without emitting either sceneGraphInitialized or
    // sceneGraphInvalidated), so the picture froze from the first toggle on.
    // Qt simply stops calling updatePaintNode when the graph is not running,
    // which is the same guarantee without a flag to get wrong.
    bool torn = false;
    // Serialises rendering against the destruction below: they run on
    // different threads and both make the GL context current.
    std::mutex lock;
    // The scene graph was replaced: Qt-side wrappers must be rebuilt before
    // the next frame can be drawn.
    bool qtDirty = false;
    // Whether this bridge still exists, in a form that survives it not
    // existing. frameReady posts a lambda to the GUI thread and destroy() can
    // free the bridge while that lambda is still sitting in the queue -- at
    // which point `torn` is a read of freed memory, so it cannot be the thing
    // that answers the question. The flag is shared, so the lambda's copy keeps
    // it alive no matter what happens to the bridge.
    std::shared_ptr<std::atomic<bool>> alive = std::make_shared<std::atomic<bool>>(true);
};

namespace {

// mpv's thread: a new frame is ready. Nothing may be rendered here -- this is
// not the render thread -- so it only asks Qt for a repaint, which arrives as
// an updatePaintNode on the render thread in the usual way.
void frameReady(void *opaque)
{
    auto *bridge = static_cast<GvVideoBridge *>(opaque);
    if (!bridge || bridge->torn)
        return;
    QCoreApplication *app = QCoreApplication::instance();
    if (!app)
        return;
    // Posted to the APPLICATION, not to the window, and the window is only
    // touched inside the lambda. Testing bridge->window here on mpv's thread
    // would be a race the destructor wins about one run in eight: the window
    // can start dying between the check and the post. The application
    // outlives every window, and the lambda runs on the GUI thread -- the
    // same thread that clears bridge->window when the window is destroyed --
    // so by the time it looks, the answer cannot change underneath it.
    QMetaObject::invokeMethod(
        app,
        [bridge, alive = bridge->alive]() {
            if (!alive->load() || bridge->torn || !bridge->item)
                return;
            // Already on the GUI thread, so this is a plain call.
            QMetaObject::invokeMethod(bridge->item, "requestUpdate", Qt::DirectConnection);
        },
        Qt::QueuedConnection);
}

void makeCurrent(GvVideoBridge *bridge)
{
    bridge->previousContext = CGLGetCurrentContext();
    CGLSetCurrentContext(bridge->gl);
}

void doneCurrent(GvVideoBridge *bridge)
{
    // Metal reads the surface when Qt's command buffer runs, and the two APIs
    // share no implicit ordering: without this the GL work may still be queued
    // and the frame tears or repeats.
    glFlush();
    CGLSetCurrentContext(bridge->previousContext);
    bridge->previousContext = nullptr;
}

// Drops only what belongs to Qt's scene graph, keeping the IOSurface and the
// GL objects that draw into it. A fullscreen toggle recreates the scene graph
// -- and with it the QRhi and possibly the MTLDevice -- but our surface and
// mpv's view of it are untouched by that, so they are kept and re-wrapped.
void releaseQtObjects(Surface &surface)
{
    delete surface.sceneTexture;  // owns the QRhiTexture
    surface.sceneTexture = nullptr;
    surface.metalTexture = nil;
}

// Frees one surface's objects. Must run on the render thread with the
// bridge's GL context current, and only when nothing can still reference it.
void releaseSurface(Surface &surface)
{
    if (surface.fbo)
        glDeleteFramebuffers(1, &surface.fbo);
    if (surface.glTexture)
        glDeleteTextures(1, &surface.glTexture);
    // Order is the reverse of construction and it matters: the QSGTexture owns
    // the QRhiTexture, which holds an UNRETAINED pointer to the MTLTexture.
    // Dropping the Metal object first leaves Qt dereferencing freed memory the
    // next time it binds.
    delete surface.sceneTexture;
    surface.sceneTexture = nullptr;
    surface.metalTexture = nil;
    if (surface.surface) {
        CFRelease(surface.surface);
        surface.surface = nullptr;
    }
    surface.fbo = 0;
    surface.glTexture = 0;
}

bool buildSurface(GvVideoBridge *bridge, int width, int height, const SurfaceFormat &format,
                  Surface &out)
{
    out.format = &format;
    const size_t bytesPerRow = IOSurfaceAlignProperty(kIOSurfaceBytesPerRow, size_t(width) * 4);
    NSDictionary *properties = @{
        (id)kIOSurfaceWidth : @(width),
        (id)kIOSurfaceHeight : @(height),
        (id)kIOSurfaceBytesPerElement : @4,
        (id)kIOSurfaceBytesPerRow : @(bytesPerRow),
        (id)kIOSurfacePixelFormat : @(format.iosurface),
    };
    out.surface = IOSurfaceCreate((__bridge CFDictionaryRef)properties);
    if (!out.surface) {
        setError("IOSurfaceCreate failed");
        return false;
    }
    out.width = width;
    out.height = height;

    // Metal's view of it, on QT'S device -- a texture from any other device
    // cannot be sampled by Qt's renderer.
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format.metal
                                                           width:width
                                                          height:height
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeShared;
    out.metalTexture = [bridge->device newTextureWithDescriptor:descriptor
                                                      iosurface:out.surface
                                                          plane:0];
    if (!out.metalTexture) {
        setError("newTextureWithDescriptor:iosurface: failed");
        return false;
    }

    // Qt's view of it. createFrom() does NOT retain the MTLTexture; the
    // Surface holds the only strong reference and outlives the QRhiTexture.
    QRhiTexture *rhiTexture = bridge->rhi->newTexture(format.qt, QSize(width, height), 1);
    if (!rhiTexture->createFrom({quint64((__bridge void *)out.metalTexture), 0})) {
        delete rhiTexture;
        setError("QRhiTexture::createFrom failed");
        return false;
    }
    // createTextureFromRhiTexture() TAKES OWNERSHIP of rhiTexture. Deleting it
    // here as well is a double free.
    out.sceneTexture =
        bridge->window->createTextureFromRhiTexture(rhiTexture, QQuickWindow::TextureIsOpaque);
    if (!out.sceneTexture) {
        delete rhiTexture;
        setError("createTextureFromRhiTexture failed");
        return false;
    }

    // GL's view of it, and the framebuffer mpv draws into.
    glGenTextures(1, &out.glTexture);
    glBindTexture(GL_TEXTURE_RECTANGLE, out.glTexture);
    // GL_TEXTURE_RECTANGLE is what CGL can back with an IOSurface; mpv does not
    // care what the attachment is, only that the framebuffer is complete.
    if (CGLTexImageIOSurface2D(bridge->gl, GL_TEXTURE_RECTANGLE, format.glInternal, width, height,
                               format.glFormat, format.glType, out.surface, 0) != kCGLNoError) {
        setError("CGLTexImageIOSurface2D failed");
        return false;
    }
    glGenFramebuffers(1, &out.fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, out.fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_RECTANGLE,
                           out.glTexture, 0);
    const GLenum status = glCheckFramebufferStatus(GL_FRAMEBUFFER);
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    if (status != GL_FRAMEBUFFER_COMPLETE) {
        char message[96];
        std::snprintf(message, sizeof(message), "framebuffer incomplete (0x%x)", status);
        setError(message);
        return false;
    }
    return true;
}

// GPU objects belonging to a scene graph that has been invalidated. They are
// NOT freed at that moment: Qt's nodes still exist while the graph is being
// torn down, they hold a pointer to our texture, and freeing it there leaves
// the renderer sampling freed memory (which is what crashed on the first
// fullscreen toggle). A new scene graph is proof the old nodes are gone, so
// the previous generation is released when the next bridge is built.
struct Grave {
    CGLContextObj gl = nullptr;
    Surface current;
    std::vector<Surface> retired;
};

std::vector<Grave> g_graves;

void buryLater(GvVideoBridge *bridge)
{
    Grave grave;
    grave.gl = bridge->gl;
    grave.current = std::move(bridge->current);
    grave.retired = std::move(bridge->retired);
    g_graves.push_back(std::move(grave));
    bridge->gl = nullptr;
    bridge->retired.clear();
}

// Called when a new scene graph exists, which cannot happen while any node of
// the old one survives.
void drainGraves()
{
    for (Grave &grave : g_graves) {
        if (!grave.gl)
            continue;
        CGLContextObj previous = CGLGetCurrentContext();
        CGLSetCurrentContext(grave.gl);
        releaseSurface(grave.current);
        for (Surface &surface : grave.retired)
            releaseSurface(surface);
        glFlush();
        CGLSetCurrentContext(previous);
        CGLDestroyContext(grave.gl);
    }
    g_graves.clear();
}

// Re-wrap the existing surface for the scene graph that exists NOW. Returns
// false if the window has no usable renderer yet.
bool rewrapForCurrentSceneGraph(GvVideoBridge *bridge)
{
    if (!bridge->window)
        return false;
    QSGRendererInterface *renderer = bridge->window->rendererInterface();
    if (!renderer || renderer->graphicsApi() != QSGRendererInterface::Metal)
        return false;
    auto *rhi = static_cast<QRhi *>(
        renderer->getResource(bridge->window, QSGRendererInterface::RhiResource));
    if (!rhi)
        return false;
    const auto *handles = static_cast<const QRhiMetalNativeHandles *>(rhi->nativeHandles());
    if (!handles || !handles->dev)
        return false;
    bridge->rhi = rhi;
    bridge->device = (id<MTLDevice>)handles->dev;

    Surface &surface = bridge->current;
    if (!surface.surface || !surface.format)
        return true;  // nothing to re-wrap yet; the next set_size will build it

    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:surface.format->metal
                                                           width:surface.width
                                                          height:surface.height
                                                       mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeShared;
    surface.metalTexture = [bridge->device newTextureWithDescriptor:descriptor
                                                          iosurface:surface.surface
                                                              plane:0];
    if (!surface.metalTexture)
        return false;
    QRhiTexture *rhiTexture =
        rhi->newTexture(surface.format->qt, QSize(surface.width, surface.height), 1);
    if (!rhiTexture->createFrom({quint64((__bridge void *)surface.metalTexture), 0})) {
        delete rhiTexture;
        return false;
    }
    surface.sceneTexture =
        bridge->window->createTextureFromRhiTexture(rhiTexture, QQuickWindow::TextureIsOpaque);
    return surface.sceneTexture != nullptr;
}

// The whole teardown, on the render thread, with nothing Python in sight.
// Idempotent: Qt may say both "about to stop" and "invalidated".
void sceneGraphGone(GvVideoBridge *bridge)
{
    // The scene graph -- and its QRhi -- is gone, so every Qt-side wrapper is
    // dead. Nodes are gone by now too, which is what makes dropping the
    // texture safe here and unsafe at sceneGraphAboutToStop. Everything that
    // is OURS (the surface, the GL context, mpv's render context) is
    // untouched by any of this and stays, so playback survives a fullscreen
    // toggle instead of having to be rebuilt -- which it could not be, since
    // libmpv allows only one render context per core and this one is alive.
    if (!bridge || bridge->qtDirty)
        return;
    bridge->qtDirty = true;
    releaseQtObjects(bridge->current);
    for (Surface &surface : bridge->retired)
        releaseQtObjects(surface);
}

void teardown(GvVideoBridge *bridge)
{
    if (!bridge || bridge->torn)
        return;
    bridge->torn = true;
    // Before anything is freed, so a lambda already queued on the GUI thread
    // sees a dead bridge rather than a half-freed one.
    bridge->alive->store(false);
    QObject::disconnect(bridge->aboutToStop);
    QObject::disconnect(bridge->initialized);
    QObject::disconnect(bridge->invalidated);
    QObject::disconnect(bridge->windowGone);
    QObject::disconnect(bridge->itemGone);
    bridge->item = nullptr;

    if (bridge->gl) {
        makeCurrent(bridge);
        if (bridge->mpv) {
            // Detach first so no callback can be in flight, then free -- both
            // with mpv's own GL context current, which libmpv requires. This
            // one IS safe to free here: it is not a scene-graph resource and
            // no Qt node refers to it.
            g_mpv.setUpdateCallback(bridge->mpv, nullptr, nullptr);
            g_mpv.free(bridge->mpv);
            bridge->mpv = nullptr;
        }
        doneCurrent(bridge);
        // Everything the scene graph might still be holding goes to the
        // graveyard instead of being released here.
        buryLater(bridge);
    }
    bridge->window = nullptr;
}

} // namespace

extern "C" {

GV_API int gv_video_bridge_abi(void)
{
    return GV_VIDEO_BRIDGE_ABI;
}

GV_API const char *gv_video_bridge_qt_version(void)
{
    return QT_VERSION_STR;
}

GV_API const char *gv_video_bridge_error(void)
{
    return g_error.c_str();
}

GV_API GvVideoBridge *gv_video_bridge_create(void *windowPtr, void *mpvHandle)
{
    setError("");
    if (!windowPtr || !mpvHandle) {
        setError("no window or no mpv handle");
        return nullptr;
    }
    if (!g_mpv.resolve()) {
        setError("libmpv's render API is not in this process");
        return nullptr;
    }
    auto *window = static_cast<QQuickWindow *>(windowPtr);
    QSGRendererInterface *renderer = window->rendererInterface();
    if (!renderer || renderer->graphicsApi() != QSGRendererInterface::Metal) {
        setError("scene graph is not on Metal");
        return nullptr;
    }
    auto *rhi =
        static_cast<QRhi *>(renderer->getResource(window, QSGRendererInterface::RhiResource));
    if (!rhi) {
        setError("no QRhi on this window");
        return nullptr;
    }
    const auto *handles = static_cast<const QRhiMetalNativeHandles *>(rhi->nativeHandles());
    if (!handles || !handles->dev) {
        setError("no MTLDevice behind the QRhi");
        return nullptr;
    }

    // A new scene graph exists, so no node of the previous one survives.
    drainGraves();

    // Owned here until every fallible step has passed; released to the caller
    // at the end. Three of these can fail, and each used to carry its own
    // hand-written cleanup.
    auto bridge = std::make_unique<GvVideoBridge>();
    bridge->window = window;
    bridge->rhi = rhi;
    bridge->device = (id<MTLDevice>)handles->dev;

    // Our own GL context: Qt has none at all on Metal. 3.2 core is what mpv's
    // renderer wants for its scalers and the videotoolbox interop path.
    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAAccelerated,
        kCGLPFAOpenGLProfile,
        (CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core,
        (CGLPixelFormatAttribute)0,
    };
    CGLPixelFormatObj pixelFormat = nullptr;
    GLint formatCount = 0;
    if (CGLChoosePixelFormat(attributes, &pixelFormat, &formatCount) != kCGLNoError
        || !pixelFormat) {
        setError("CGLChoosePixelFormat failed");
        return nullptr;
    }
    const CGLError contextError = CGLCreateContext(pixelFormat, nullptr, &bridge->gl);
    CGLDestroyPixelFormat(pixelFormat);
    if (contextError != kCGLNoError || !bridge->gl) {
        setError("CGLCreateContext failed");
        return nullptr;
    }

    // mpv's render context, on that GL context.
    makeCurrent(bridge.get());
    MpvOpenGLInitParams glParams = {glSymbol, nullptr, nullptr};
    char apiType[] = "opengl";
    MpvRenderParam params[] = {
        {MPV_RENDER_PARAM_API_TYPE, apiType},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &glParams},
        {MPV_RENDER_PARAM_INVALID, nullptr},
    };
    const int created = g_mpv.create(&bridge->mpv, mpvHandle, params);
    doneCurrent(bridge.get());
    if (created < 0 || !bridge->mpv) {
        char message[96];
        std::snprintf(message, sizeof(message), "mpv_render_context_create failed (%d)", created);
        setError(message);
        CGLDestroyContext(bridge->gl);
        bridge->gl = nullptr;
        return nullptr;
    }
    GvVideoBridge *raw = bridge.get();
    g_mpv.setUpdateCallback(bridge->mpv, frameReady, raw);

    // Teardown, in C++ and on the render thread, which is the entire reason
    // mpv's context lives on this side of the boundary. Both signals: only one
    // of them is reliable, and which one depends on how the app is quit.
    // Nothing is connected to sceneGraphAboutToStop. Freeing there is what
    // made the renderer sample a dead texture (nodes still exist at that
    // point), and merely flagging there froze playback, because nothing
    // reliably signals the resume.
    bridge->invalidated = QObject::connect(window, &QQuickWindow::sceneGraphInvalidated, window,
                                           [raw]() { sceneGraphGone(raw); },
                                           Qt::DirectConnection);

    // The window can be destroyed without the scene graph ever announcing
    // anything -- and then mpv, which knows nothing about any of this, keeps
    // asking a half-destroyed window to repaint. That crashed in Qt's own
    // destructors. This hook only sets flags, which is all that is safe on
    // the GUI thread: whatever is left over is handed back with the process.
    bridge->windowGone = QObject::connect(window, &QObject::destroyed, [raw]() {
        // The window is gone for good: nothing more will be drawn, and mpv
        // must stop asking. Flags only -- freeing from this thread is what
        // must not happen.
        raw->torn = true;
        raw->window = nullptr;
    });
    // Quitting is the earliest reliable warning, and it arrives before the
    // QML engine is dismantled. Stopping here means no repaint is requested
    // and no frame is drawn while Qt destroys the scene -- which is the state
    // the software path has always torn down in, and it does not crash.
    // Flag only: freeing anything from this thread is what must not happen.
    if (QCoreApplication *app = QCoreApplication::instance()) {
        // Quitting: take everything down while Qt is still whole. Leaving the
        // GL context alive into the interpreter's teardown made the QML
        // engine's destructor crash about half the time (measured; a
        // software-rendered control never did). The lock is what makes this
        // safe from the GUI thread -- a frame cannot be in flight.
        QObject::connect(app, &QCoreApplication::aboutToQuit, app, [raw]() {
            std::lock_guard<std::mutex> guard(raw->lock);
            if (raw->torn)
                return;
            raw->torn = true;
            raw->alive->store(false);
            if (raw->gl) {
                makeCurrent(raw);
                if (raw->mpv) {
                    g_mpv.setUpdateCallback(raw->mpv, nullptr, nullptr);
                    g_mpv.free(raw->mpv);
                    raw->mpv = nullptr;
                }
                releaseSurface(raw->current);
                for (Surface &surface : raw->retired)
                    releaseSurface(surface);
                raw->retired.clear();
                doneCurrent(raw);
                CGLDestroyContext(raw->gl);
                raw->gl = nullptr;
            }
        });
    }
    // Ownership passes to the caller, which holds it as an opaque handle and
    // returns it through gv_video_bridge_destroy.
    return bridge.release();
}

GV_API int gv_video_bridge_set_size(GvVideoBridge *bridge, int width, int height)
{
    setError("");
    if (!bridge || bridge->torn || width <= 0 || height <= 0) {
        setError("bad arguments");
        return 0;
    }
    if (bridge->current.width == width && bridge->current.height == height)
        return 1;

    makeCurrent(bridge);
    if (bridge->current.surface) {
        // Retire, do not release: the renderer may still be holding the old
        // texture, and Qt offers no way to ask.
        bridge->retired.push_back(std::move(bridge->current));
    }
    bool ok = false;
    for (const SurfaceFormat &format : kSurfaceFormats) {
        Surface fresh;
        if (buildSurface(bridge, width, height, format, fresh)) {
            bridge->current = std::move(fresh);
            ok = true;
            break;
        }
        // A half-built surface is ours alone -- nothing has seen it, so it can
        // be released here and now, and the next format tried.
        releaseSurface(fresh);
    }
    doneCurrent(bridge);
    return ok ? 1 : 0;
}

GV_API void gv_video_bridge_set_item(GvVideoBridge *bridge, void *itemPtr)
{
    auto *item = static_cast<QObject *>(itemPtr);
    if (!bridge || bridge->item == item)
        return;
    QObject::disconnect(bridge->itemGone);
    bridge->item = item;
    if (!item)
        return;
    // The player page is destroyed and rebuilt for every playback, so the
    // item this points at is routinely outlived by the bridge.
    bridge->itemGone = QObject::connect(item, &QObject::destroyed, [bridge]() {
        bridge->item = nullptr;
    });
}

GV_API int gv_video_bridge_render(GvVideoBridge *bridge)
{
    if (!bridge) { setError("no bridge"); return 0; }
    std::lock_guard<std::mutex> guard(bridge->lock);
    if (bridge->torn) { setError("torn"); return 0; }
    if (!bridge->mpv) { setError("no mpv context"); return 0; }
    if (!bridge->current.fbo) { setError("no fbo"); return 0; }
    if (bridge->qtDirty) {
        if (!rewrapForCurrentSceneGraph(bridge)) {
            setError("rewrap for new scene graph failed");
            return 0;
        }
        bridge->qtDirty = false;
    }

    makeCurrent(bridge);
    MpvOpenGLFBO fbo = {int(bridge->current.fbo), bridge->current.width, bridge->current.height,
                        int(bridge->current.format->glInternal)};
    // No flip: the GL framebuffer's bottom-left origin and the way Metal
    // samples the IOSurface already agree.
    int flipY = 0;
    // Never sleep until the frame's presentation time: this is Qt's render
    // thread and the whole UI would wait with it.
    int blockForTargetTime = 0;
    MpvRenderParam params[] = {
        {MPV_RENDER_PARAM_OPENGL_FBO, &fbo},
        {MPV_RENDER_PARAM_FLIP_Y, &flipY},
        {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &blockForTargetTime},
        {MPV_RENDER_PARAM_INVALID, nullptr},
    };
    const int rendered = g_mpv.render(bridge->mpv, params);
    doneCurrent(bridge);
    return rendered >= 0 ? 1 : 0;
}

GV_API void gv_video_bridge_destroy(GvVideoBridge *bridge)
{
    if (!bridge)
        return;
    teardown(bridge);
    delete bridge;
}

GV_API int gv_video_bridge_stale(GvVideoBridge *bridge)
{
    // Only true once the WINDOW is gone. A replaced scene graph is repaired in
    // place instead, because mpv's render context cannot be rebuilt: libmpv
    // allows one per core and the core outlives all of this.
    return !bridge || bridge->torn ? 1 : 0;
}

GV_API void *gv_video_bridge_texture(GvVideoBridge *bridge)
{
    if (!bridge || bridge->torn || bridge->qtDirty)
        return nullptr;
    return bridge->current.sceneTexture;
}

GV_API const char *gv_video_bridge_format(GvVideoBridge *bridge)
{
    if (!bridge || !bridge->current.format)
        return "";
    return bridge->current.format->name;
}

} // extern "C"
