// Zero-copy video for macOS: mpv renders through OpenGL into an IOSurface,
// Metal samples the same surface, Qt's scene graph draws it. No frame ever
// touches the CPU.
//
// Why this exists: Qt Quick runs on Metal on macOS (see
// infrastructure/graphics.py -- OpenGL there costs the threaded render loop),
// but libmpv's render API speaks OpenGL and nothing else. An IOSurface is the
// one buffer both APIs can address, so it is the seam between them.
//
// The ownership rules encoded below were each learned from a crash, and none
// of them is guessable from the API signatures. They are stated where they
// bite.

#include "gravitas_video_bridge.h"

#include <QtGui/rhi/qrhi.h>
#include <QtGui/rhi/qrhi_platform.h>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGRendererInterface>
#include <QtQuick/QSGTexture>

#import <IOSurface/IOSurfaceObjC.h>
#import <Metal/Metal.h>

#include <OpenGL/CGLIOSurface.h>
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>

#include <cstdio>
#include <string>
#include <vector>

namespace {

// Reported through gv_video_bridge_error(). Thread-local: the render thread
// and the GUI thread can both be in here during teardown.
thread_local std::string g_error;

void setError(const char *message)
{
    g_error = message ? message : "";
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

    Surface current;
    // Surfaces the renderer may still be holding. Qt gives no way to ask, so
    // they wait here until the scene graph is gone.
    std::vector<Surface> retired;
};

namespace {

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
    QRhiTexture *rhiTexture =
        bridge->rhi->newTexture(format.qt, QSize(width, height), 1);
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

GV_API GvVideoBridge *gv_video_bridge_create(void *windowPtr)
{
    setError("");
    if (!windowPtr) {
        setError("no window");
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
    return bridge;
}

GV_API int gv_video_bridge_set_size(GvVideoBridge *bridge, int width, int height)
{
    setError("");
    if (!bridge || width <= 0 || height <= 0) {
        setError("bad arguments");
        return 0;
    }
    if (bridge->current.width == width && bridge->current.height == height)
        return 1;

    CGLContextObj previous = CGLGetCurrentContext();
    CGLSetCurrentContext(bridge->gl);
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
    glFlush();
    CGLSetCurrentContext(previous);
    return ok ? 1 : 0;
}

GV_API void gv_video_bridge_destroy(GvVideoBridge *bridge)
{
    if (!bridge)
        return;
    if (bridge->gl) {
        CGLContextObj previous = CGLGetCurrentContext();
        CGLSetCurrentContext(bridge->gl);
        releaseSurface(bridge->current);
        for (Surface &surface : bridge->retired)
            releaseSurface(surface);
        glFlush();
        CGLSetCurrentContext(previous);
        CGLDestroyContext(bridge->gl);
        bridge->gl = nullptr;
    }
    bridge->retired.clear();
    delete bridge;
}

GV_API int gv_video_bridge_begin(GvVideoBridge *bridge)
{
    if (!bridge || !bridge->gl) {
        setError("bridge not usable");
        return 0;
    }
    bridge->previousContext = CGLGetCurrentContext();
    CGLSetCurrentContext(bridge->gl);
    return 1;
}

GV_API void gv_video_bridge_end(GvVideoBridge *bridge)
{
    if (!bridge || !bridge->gl)
        return;
    // Metal reads the surface when Qt's command buffer runs, and the two APIs
    // share no implicit ordering: without this the GL work may still be queued
    // and the frame tears or repeats.
    glFlush();
    CGLSetCurrentContext(bridge->previousContext);
    bridge->previousContext = nullptr;
}

GV_API unsigned int gv_video_bridge_fbo(GvVideoBridge *bridge)
{
    return bridge ? bridge->current.fbo : 0;
}

GV_API const char *gv_video_bridge_format(GvVideoBridge *bridge)
{
    if (!bridge || !bridge->current.format)
        return "";
    return bridge->current.format->name;
}

GV_API unsigned int gv_video_bridge_gl_internal_format(GvVideoBridge *bridge)
{
    if (!bridge || !bridge->current.format)
        return 0;
    return bridge->current.format->glInternal;
}

GV_API void *gv_video_bridge_texture(GvVideoBridge *bridge)
{
    return bridge ? bridge->current.sceneTexture : nullptr;
}

} // extern "C"
