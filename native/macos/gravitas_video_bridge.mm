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

#include <cstdio>
#include <string>
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
struct Surface {
    IOSurfaceRef surface = nullptr;
    id<MTLTexture> metalTexture = nil;
    QSGTexture *sceneTexture = nullptr;
    GLuint glTexture = 0;
    GLuint fbo = 0;
    int width = 0;
    int height = 0;
    const SurfaceFormat *format = nullptr;
};

} // namespace

struct GvVideoBridge {
    QQuickWindow *window = nullptr;
    QRhi *rhi = nullptr;
    id<MTLDevice> device = nil;

    CGLContextObj gl = nullptr;
    CGLContextObj previousContext = nullptr;

    MpvRenderContext *mpv = nullptr;

    Surface current;
    // Surfaces the renderer may still be holding. Qt gives no way to ask, so
    // they wait here until the scene graph is gone.
    std::vector<Surface> retired;

    QMetaObject::Connection aboutToStop;
    QMetaObject::Connection invalidated;
    QMetaObject::Connection windowGone;
    // Two flags, not one. `stopped` means draw nothing and post nothing, and
    // can be set from any thread because it only stops work. `torn` means the
    // GPU and mpv objects are gone, and may only be set on the render thread.
    bool stopped = false;
    bool torn = false;
};

namespace {

// mpv's thread: a new frame is ready. Nothing may be rendered here -- this is
// not the render thread -- so it only asks Qt for a repaint, which arrives as
// an updatePaintNode on the render thread in the usual way.
void frameReady(void *opaque)
{
    auto *bridge = static_cast<GvVideoBridge *>(opaque);
    if (!bridge || bridge->stopped)
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
        [bridge]() {
            if (!bridge->stopped && bridge->window)
                bridge->window->update();
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

// The whole teardown, on the render thread, with nothing Python in sight.
// Idempotent: Qt may say both "about to stop" and "invalidated".
void teardown(GvVideoBridge *bridge)
{
    if (!bridge || bridge->torn)
        return;
    bridge->torn = true;
    bridge->stopped = true;
    QObject::disconnect(bridge->aboutToStop);
    QObject::disconnect(bridge->invalidated);
    QObject::disconnect(bridge->windowGone);

    if (bridge->gl) {
        makeCurrent(bridge);
        if (bridge->mpv) {
            // Detach first so no callback can be in flight, then free -- both
            // with mpv's own GL context current, which libmpv requires.
            g_mpv.setUpdateCallback(bridge->mpv, nullptr, nullptr);
            g_mpv.free(bridge->mpv);
            bridge->mpv = nullptr;
        }
        releaseSurface(bridge->current);
        for (Surface &surface : bridge->retired)
            releaseSurface(surface);
        bridge->retired.clear();
        doneCurrent(bridge);
        CGLDestroyContext(bridge->gl);
        bridge->gl = nullptr;
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

    auto *bridge = new GvVideoBridge;
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
        delete bridge;
        return nullptr;
    }
    const CGLError contextError = CGLCreateContext(pixelFormat, nullptr, &bridge->gl);
    CGLDestroyPixelFormat(pixelFormat);
    if (contextError != kCGLNoError || !bridge->gl) {
        setError("CGLCreateContext failed");
        delete bridge;
        return nullptr;
    }

    // mpv's render context, on that GL context.
    makeCurrent(bridge);
    MpvOpenGLInitParams glParams = {glSymbol, nullptr};
    char apiType[] = "opengl";
    MpvRenderParam params[] = {
        {MPV_RENDER_PARAM_API_TYPE, apiType},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &glParams},
        {MPV_RENDER_PARAM_INVALID, nullptr},
    };
    const int created = g_mpv.create(&bridge->mpv, mpvHandle, params);
    doneCurrent(bridge);
    if (created < 0 || !bridge->mpv) {
        char message[96];
        std::snprintf(message, sizeof(message), "mpv_render_context_create failed (%d)", created);
        setError(message);
        CGLDestroyContext(bridge->gl);
        bridge->gl = nullptr;
        delete bridge;
        return nullptr;
    }
    g_mpv.setUpdateCallback(bridge->mpv, frameReady, bridge);

    // Teardown, in C++ and on the render thread, which is the entire reason
    // mpv's context lives on this side of the boundary. Both signals: only one
    // of them is reliable, and which one depends on how the app is quit.
    // Measured across repeated runs, freeing here survives teardown more
    // often than waiting for sceneGraphInvalidated (which does not always
    // arrive). Neither is reliable yet -- see the note in metal_bridge.py.
    bridge->aboutToStop = QObject::connect(window, &QQuickWindow::sceneGraphAboutToStop, window,
                                           [bridge]() { teardown(bridge); },
                                           Qt::DirectConnection);
    bridge->invalidated = QObject::connect(window, &QQuickWindow::sceneGraphInvalidated, window,
                                           [bridge]() { teardown(bridge); },
                                           Qt::DirectConnection);
    // The window can be destroyed without the scene graph ever announcing
    // anything -- and then mpv, which knows nothing about any of this, keeps
    // asking a half-destroyed window to repaint. That crashed in Qt's own
    // destructors. This hook only sets flags, which is all that is safe on
    // the GUI thread: whatever is left over is handed back with the process.
    bridge->windowGone = QObject::connect(window, &QObject::destroyed, [bridge]() {
        bridge->stopped = true;
        bridge->window = nullptr;
    });
    // Quitting is the earliest reliable warning, and it arrives before the
    // QML engine is dismantled. Stopping here means no repaint is requested
    // and no frame is drawn while Qt destroys the scene -- which is the state
    // the software path has always torn down in, and it does not crash.
    // Flag only: freeing anything from this thread is what must not happen.
    if (QCoreApplication *app = QCoreApplication::instance()) {
        QObject::connect(app, &QCoreApplication::aboutToQuit, app,
                         [bridge]() { bridge->stopped = true; });
    }
    return bridge;
}

GV_API int gv_video_bridge_set_size(GvVideoBridge *bridge, int width, int height)
{
    setError("");
    if (!bridge || bridge->stopped || width <= 0 || height <= 0) {
        setError("bad arguments");
        return 0;
    }
    if (bridge->current.width == width && bridge->current.height == height)
        return 1;

    makeCurrent(bridge);
    if (bridge->current.surface) {
        // Retire, do not release: the renderer may still be holding the old
        // texture, and Qt offers no way to ask.
        bridge->retired.push_back(bridge->current);
        bridge->current = Surface{};
    }
    bool ok = false;
    for (const SurfaceFormat &format : kSurfaceFormats) {
        Surface fresh;
        if (buildSurface(bridge, width, height, format, fresh)) {
            bridge->current = fresh;
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

GV_API int gv_video_bridge_render(GvVideoBridge *bridge)
{
    if (!bridge || bridge->stopped || !bridge->mpv || !bridge->current.fbo)
        return 0;
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

GV_API void *gv_video_bridge_texture(GvVideoBridge *bridge)
{
    if (!bridge || bridge->stopped || bridge->torn)
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
