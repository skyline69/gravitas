// The native engine's zero-copy video on macOS/Metal -- see
// gravitas_native_metal.h.

#include "gravitas_native_metal.h"

#include <QtGui/rhi/qrhi.h>
#include <QtGui/rhi/qrhi_platform.h>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGRendererInterface>
#include <QtQuick/QSGTexture>

#import <Metal/Metal.h>

#include <atomic>
#include <memory>
#include <string>
#include <vector>

namespace {

thread_local std::string g_error;

void set_error(const char *message) { g_error = message ? message : ""; }

// A texture the engine rendered, as the scene graph samples it. `metal`
// holds the MTLTexture alive: QRhiTexture::createFrom does not retain it,
// and the engine lets go of a texture as soon as its ring is replaced.
struct Wrapped {
    id<MTLTexture> metal = nil;
    int width = 0;
    int height = 0;
    QSGTexture *texture = nullptr;  // owns its QRhiTexture
};

QRhi *window_rhi(QQuickWindow *window)
{
    QSGRendererInterface *rif = window->rendererInterface();
    if (!rif || rif->graphicsApi() != QSGRendererInterface::Metal)
        return nullptr;
    return static_cast<QRhi *>(rif->getResource(window, QSGRendererInterface::RhiResource));
}

}  // namespace

struct GvNativeMetal {
    QQuickWindow *window = nullptr;
    QRhi *rhi = nullptr;
    id<MTLDevice> device = nil;
    id<MTLCommandQueue> queue = nil;
    std::vector<Wrapped> textures;
    QMetaObject::Connection invalidated;
    std::atomic<bool> torn{false};
};

namespace {

// Drops what belongs to the scene graph, in the order ownership demands:
// the QSGTexture (and with it the QRhiTexture) first, then the MTLTexture it
// pointed at without a reference. Runs on the render thread when the scene
// graph is invalidated -- its nodes are gone by then, which is what makes
// this safe there and unsafe at sceneGraphAboutToStop (see
// gravitas_video_bridge.mm).
void tear_down(GvNativeMetal *bridge)
{
    if (bridge->torn.exchange(true))
        return;
    QObject::disconnect(bridge->invalidated);
    for (Wrapped &w : bridge->textures) {
        delete w.texture;
        w.metal = nil;
    }
    bridge->textures.clear();
}

// A scene graph replaced without sceneGraphInvalidated: its wrappers belong
// to a QRhi that no longer exists, so they are neither used nor freed -- a
// renderer may still hold them -- only kept, with their MTLTextures, for the
// rest of the process. Never seen; the check costs a pointer comparison.
std::vector<Wrapped> g_abandoned;

void abandon(GvNativeMetal *bridge)
{
    if (bridge->torn.exchange(true))
        return;
    QObject::disconnect(bridge->invalidated);
    for (Wrapped &w : bridge->textures)
        g_abandoned.push_back(std::move(w));
    bridge->textures.clear();
}

}  // namespace

extern "C" {

const char *gv_native_mtl_error(void) { return g_error.c_str(); }

GvNativeMetal *gv_native_mtl_create(void *window)
{
    auto *win = static_cast<QQuickWindow *>(window);
    if (!win) {
        set_error("null window");
        return nullptr;
    }
    QRhi *rhi = window_rhi(win);
    if (!rhi) {
        set_error("scene graph is not on the Metal RHI");
        return nullptr;
    }
    const auto *nh = static_cast<const QRhiMetalNativeHandles *>(rhi->nativeHandles());
    if (!nh || !nh->dev || !nh->cmdQueue) {
        set_error("QRhi exposed no Metal device or command queue");
        return nullptr;
    }
    auto bridge = std::make_unique<GvNativeMetal>();
    bridge->window = win;
    bridge->rhi = rhi;
    bridge->device = (id<MTLDevice>)nh->dev;
    bridge->queue = (id<MTLCommandQueue>)nh->cmdQueue;
    GvNativeMetal *raw = bridge.get();
    // Direct: runs on the render thread that emits it, and never touches
    // Python.
    bridge->invalidated = QObject::connect(
        win, &QQuickWindow::sceneGraphInvalidated, win, [raw]() { tear_down(raw); },
        Qt::DirectConnection);
    return bridge.release();
}

int gv_native_mtl_device(GvNativeMetal *bridge, GvNativeMetalDevice *out)
{
    if (!bridge || !out || bridge->torn) {
        set_error("no bridge, or the scene graph is gone");
        return 0;
    }
    out->device = reinterpret_cast<uint64_t>((__bridge void *)bridge->device);
    out->queue = reinterpret_cast<uint64_t>((__bridge void *)bridge->queue);
    return 1;
}

void *gv_native_mtl_texture(GvNativeMetal *bridge, uint64_t texture, int width, int height)
{
    if (!bridge || bridge->torn || !texture || width <= 0 || height <= 0) {
        set_error("no texture, or the scene graph is gone");
        return nullptr;
    }
    // A scene graph replaced without sceneGraphInvalidated would leave these
    // wrappers pointing into a dead QRhi. Say so rather than draw with them;
    // the caller replaces the bridge (and leaves the old wrappers be).
    if (window_rhi(bridge->window) != bridge->rhi) {
        abandon(bridge);
        set_error("the scene graph was replaced");
        return nullptr;
    }
    auto metal = (__bridge id<MTLTexture>)reinterpret_cast<void *>(texture);
    for (const Wrapped &w : bridge->textures) {
        if (w.metal == metal && w.width == width && w.height == height)
            return w.texture;
    }
    QRhiTexture *rhiTexture = bridge->rhi->newTexture(QRhiTexture::RGB10A2, QSize(width, height));
    if (!rhiTexture->createFrom({quint64(texture), 0})) {
        delete rhiTexture;
        set_error("QRhiTexture::createFrom failed");
        return nullptr;
    }
    // Takes ownership of the QRhiTexture (not of the MTLTexture).
    QSGTexture *wrapped =
        bridge->window->createTextureFromRhiTexture(rhiTexture, QQuickWindow::TextureIsOpaque);
    if (!wrapped) {
        delete rhiTexture;
        set_error("createTextureFromRhiTexture failed");
        return nullptr;
    }
    // The strong reference is what keeps the texture alive for Qt.
    bridge->textures.push_back({metal, width, height, wrapped});
    return wrapped;
}

int gv_native_mtl_stale(GvNativeMetal *bridge) { return (!bridge || bridge->torn) ? 1 : 0; }

void gv_native_mtl_destroy(GvNativeMetal *bridge)
{
    if (!bridge)
        return;
    tear_down(bridge);
    delete bridge;
}

}  // extern "C"
