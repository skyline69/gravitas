// The native engine's zero-copy video on Vulkan (Linux, Windows) -- see
// gravitas_native_vk.h.

#include "gravitas_native_vk.h"

// Prototypes on, before Qt's headers turn them off (see the same note in
// gravitas_video_bridge_vk.cpp).
#include <vulkan/vulkan.h>

#include <QtGui/QVulkanInstance>
#include <QtGui/rhi/qrhi.h>
#include <QtGui/rhi/qrhi_platform.h>
#include <QtQuick/QQuickGraphicsConfiguration>
#include <QtQuick/QQuickGraphicsDevice>
#include <QtQuick/QQuickWindow>
#include <QtQuick/QSGRendererInterface>
#include <QtQuick/QSGTexture>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace {

thread_local std::string g_error;

// The instance for the window and the engine's shared device (see
// gv_native_vk_instance). Deliberately leaked: the device made on it lives
// until the process ends, and so must it.
QVulkanInstance *g_instance = nullptr;

void set_error(const char *message) { g_error = message ? message : ""; }

// Qt's messages from one thread, held back for a scope. QVulkanInstance says
// why it failed as qWarning()s with no category -- "Failed to create Vulkan
// instance: -9" twice on a machine without a driver -- and a probe that
// expects to fail there should report that as its answer, not as warnings.
// Messages from every other thread go on to the handler that was installed.
class HeldMessages {
public:
    HeldMessages()
    {
        s_thread.store(std::this_thread::get_id());
        s_previous = qInstallMessageHandler(&HeldMessages::handle);
    }
    ~HeldMessages() { stop(); }
    HeldMessages(const HeldMessages &) = delete;
    HeldMessages &operator=(const HeldMessages &) = delete;

    // What was held, joined; emptied.
    std::string take()
    {
        std::string said;
        said.swap(s_held);
        return said;
    }

    // Stops holding, and hands what was held on as the warnings they were.
    void release()
    {
        stop();
        const std::string said = take();
        if (!said.empty())
            qWarning("%s", said.c_str());
    }

private:
    void stop()
    {
        if (!m_holding)
            return;
        m_holding = false;
        qInstallMessageHandler(s_previous);
        s_thread.store(std::thread::id());
    }

    static void handle(QtMsgType type, const QMessageLogContext &context, const QString &message)
    {
        if (std::this_thread::get_id() == s_thread.load()) {
            if (type != QtDebugMsg && type != QtInfoMsg)
                s_held += (s_held.empty() ? "" : "; ") + message.toStdString();
            return;
        }
        if (s_previous) {
            s_previous(type, context, message);
        } else {
            std::fprintf(stderr, "%s\n", qPrintable(qFormatLogMessage(type, context, message)));
        }
    }

    bool m_holding = true;
    static inline std::atomic<std::thread::id> s_thread{};
    static inline QtMessageHandler s_previous = nullptr;
    static inline std::string s_held;  // written by s_thread only
};

// An image the engine rendered, as the scene graph samples it.
struct Wrapped {
    VkImage image = VK_NULL_HANDLE;
    int width = 0;
    int height = 0;
    QSGTexture *texture = nullptr;  // owns its QRhiTexture
};

}  // namespace

struct GvNativeVk {
    QQuickWindow *window = nullptr;
    QRhi *rhi = nullptr;
    GvNativeVkDevice device{};

    // The feature chain Qt enabled, rebuilt the way Qt builds it (see
    // create()). Addresses are handed out, so this struct never moves.
    VkPhysicalDeviceFeatures2 features{};
    VkPhysicalDeviceVulkan11Features features11{};
    VkPhysicalDeviceVulkan12Features features12{};
    VkPhysicalDeviceVulkan13Features features13{};

    std::vector<Wrapped> textures;
    void (*onInvalidate)(void *) = nullptr;
    void *invalidateContext = nullptr;
    QMetaObject::Connection invalidated;
    std::atomic<bool> torn{false};
};

namespace {

uint32_t api_version(const QVersionNumber &version)
{
    return VK_MAKE_API_VERSION(0, version.majorVersion(), version.minorVersion(), 0);
}

// The features Qt enabled on its device. QRhiVulkan enables every feature the
// device reports -- 1.0 through 1.3, in the Vulkan11/12/13 structs as far as
// the API version allows -- except robustBufferAccess and robustImageAccess
// (qrhivulkan.cpp, "Enable all features that are reported as supported,
// except robustness"). Querying the same chain and clearing the same two gives
// libplacebo exactly the device it is really working with.
bool query_features(GvNativeVk *bridge, QVulkanInstance *inst, uint32_t version)
{
    auto getFeatures2 = reinterpret_cast<PFN_vkGetPhysicalDeviceFeatures2>(
        inst->getInstanceProcAddr("vkGetPhysicalDeviceFeatures2"));
    if (!getFeatures2) {
        set_error("vkGetPhysicalDeviceFeatures2 unavailable (Vulkan 1.1 required)");
        return false;
    }
    bridge->features = {};
    bridge->features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
    bridge->features11 = {};
    bridge->features11.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES;
    bridge->features12 = {};
    bridge->features12.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES;
    bridge->features13 = {};
    bridge->features13.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES;
    if (version < VK_API_VERSION_1_2) {
        // libplacebo needs 1.2; Qt asks for 1.3 or newer when the driver has it.
        set_error("Qt's Vulkan device is older than 1.2");
        return false;
    }
    bridge->features.pNext = &bridge->features11;
    bridge->features11.pNext = &bridge->features12;
    if (version >= VK_API_VERSION_1_3)
        bridge->features12.pNext = &bridge->features13;
    getFeatures2(reinterpret_cast<VkPhysicalDevice>(bridge->device.physical_device), &bridge->features);
    bridge->features.features.robustBufferAccess = VK_FALSE;
    bridge->features13.robustImageAccess = VK_FALSE;
    return true;
}

void tear_down(GvNativeVk *bridge)
{
    if (bridge->torn.exchange(true))
        return;
    QObject::disconnect(bridge->invalidated);
    // The engine first: its images must go while the device still exists,
    // and nothing may sample them once the textures below are gone.
    if (bridge->onInvalidate)
        bridge->onInvalidate(bridge->invalidateContext);
    bridge->onInvalidate = nullptr;
    for (Wrapped &w : bridge->textures)
        delete w.texture;
    bridge->textures.clear();
}

}  // namespace

extern "C" {

const char *gv_native_vk_error(void) { return g_error.c_str(); }

int gv_native_vk_abi(void) { return GV_NATIVE_VK_ABI; }

const char *gv_native_vk_qt_version(void) { return QT_VERSION_STR; }

int gv_native_vk_instance(GvNativeVkInstance *out)
{
    if (!out) {
        set_error("null output");
        return 0;
    }
    if (!g_instance) {
        // Everything Qt says while the instance is made -- a missing loader
        // or driver is reported by the first query -- is the answer to this
        // call, not a warning of its own.
        HeldMessages held;
        const auto fail = [&held](const char *why) {
            const std::string said = held.take();
            set_error((why + (said.empty() ? std::string() : ": " + said)).c_str());
            return 0;
        };
        auto instance = std::make_unique<QVulkanInstance>();
        // FFmpeg's Vulkan decoder wants a 1.3 instance, and the device is
        // used at 1.3 at most (see create() below).
        if (instance->supportedApiVersion() < QVersionNumber(1, 3))
            return fail("the Vulkan loader does not offer version 1.3");
        instance->setApiVersion(QVersionNumber(1, 3));
        // What Qt's own instance would enable (its surface extensions are
        // added by QVulkanInstance::create itself).
        QByteArrayList extensions = QQuickGraphicsConfiguration::preferredInstanceExtensions();
        // libplacebo enables VK_{EXT,KHR}_swapchain_maintenance1 on the
        // device wherever the GPU has them, and they require these on the
        // instance (caught by the validation layer).
        for (const QByteArray &name : {QByteArrayLiteral("VK_KHR_get_surface_capabilities2"),
                                       QByteArrayLiteral("VK_EXT_surface_maintenance1"),
                                       QByteArrayLiteral("VK_KHR_surface_maintenance1")}) {
            if (instance->supportedExtensions().contains(name) && !extensions.contains(name))
                extensions.append(name);
        }
        if (qEnvironmentVariableIntValue("QSG_RHI_DEBUG_LAYER")) {
            instance->setLayers({QByteArrayLiteral("VK_LAYER_KHRONOS_validation")});
            extensions.append(QByteArrayLiteral("VK_EXT_debug_utils"));
        }
        instance->setExtensions(extensions);
        if (!instance->create())
            return fail("QVulkanInstance::create failed");
        held.release();
        g_instance = instance.release();
    }
    out->instance = reinterpret_cast<uint64_t>(g_instance->vkInstance());
    out->get_instance_proc_addr =
        reinterpret_cast<uint64_t>(g_instance->getInstanceProcAddr("vkGetInstanceProcAddr"));
    return out->get_instance_proc_addr ? 1 : 0;
}

int gv_native_vk_gpu(void)
{
    GvNativeVkInstance handles{};
    if (!gv_native_vk_instance(&handles))
        return 0;
    auto enumerate = reinterpret_cast<PFN_vkEnumeratePhysicalDevices>(
        g_instance->getInstanceProcAddr("vkEnumeratePhysicalDevices"));
    auto getProperties = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties>(
        g_instance->getInstanceProcAddr("vkGetPhysicalDeviceProperties"));
    if (!enumerate || !getProperties) {
        set_error("Vulkan loader entry points unavailable");
        return 0;
    }
    uint32_t count = 0;
    if (enumerate(g_instance->vkInstance(), &count, nullptr) != VK_SUCCESS || count == 0) {
        set_error("no Vulkan device");
        return 0;
    }
    std::vector<VkPhysicalDevice> devices(count);
    if (enumerate(g_instance->vkInstance(), &count, devices.data()) < VK_SUCCESS) {
        set_error("vkEnumeratePhysicalDevices failed");
        return 0;
    }
    for (VkPhysicalDevice device : devices) {
        VkPhysicalDeviceProperties properties{};
        getProperties(device, &properties);
        if (properties.deviceType != VK_PHYSICAL_DEVICE_TYPE_CPU &&
            properties.apiVersion >= VK_API_VERSION_1_2)
            return 1;
    }
    set_error("no Vulkan 1.2 GPU (only CPU rasterisers or older devices)");
    return 0;
}

int gv_native_vk_adopt(void *window, uint64_t physical_device, uint64_t device,
                       uint32_t queue_family, uint32_t queue_index)
{
    auto *win = static_cast<QQuickWindow *>(window);
    if (!win || !g_instance || !physical_device || !device) {
        set_error("no window, no instance, or no device");
        return 0;
    }
    if (win->rendererInterface() && win->rendererInterface()->getResource(
                                        win, QSGRendererInterface::RhiResource)) {
        set_error("the scene graph has already started");
        return 0;
    }
    win->setVulkanInstance(g_instance);
    win->setGraphicsDevice(QQuickGraphicsDevice::fromDeviceObjects(
        reinterpret_cast<VkPhysicalDevice>(physical_device), reinterpret_cast<VkDevice>(device),
        int(queue_family), int(queue_index)));
    return 1;
}

GvNativeVk *gv_native_vk_create(void *window)
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
    const auto *nh = rhi ? static_cast<const QRhiVulkanNativeHandles *>(rhi->nativeHandles()) : nullptr;
    if (!nh || !nh->inst || nh->physDev == VK_NULL_HANDLE || nh->dev == VK_NULL_HANDLE) {
        set_error("QRhi exposed no Vulkan device");
        return nullptr;
    }
    QVulkanInstance *inst = nh->inst;
    auto getInstanceProcAddr = inst->getInstanceProcAddr("vkGetInstanceProcAddr");
    auto getProperties = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties>(
        inst->getInstanceProcAddr("vkGetPhysicalDeviceProperties"));
    if (!getInstanceProcAddr || !getProperties) {
        set_error("Vulkan loader entry points unavailable");
        return nullptr;
    }

    auto bridge = std::make_unique<GvNativeVk>();
    bridge->window = win;
    bridge->rhi = rhi;
    VkPhysicalDeviceProperties properties{};
    getProperties(nh->physDev, &properties);
    // What Qt runs at -- the lower of what the instance asked for and what the
    // device supports -- but never above 1.3: Qt's feature chain stops at the
    // Vulkan13 struct, so no 1.4 feature is enabled on its device. Told 1.4,
    // libplacebo used push descriptors, a 1.4 core feature Qt never turned on
    // (caught by the validation layer, VUID-vkCmdPushDescriptorSet-None-10356).
    const uint32_t version = std::min({api_version(inst->apiVersion()), properties.apiVersion,
                                       uint32_t(VK_API_VERSION_1_3)});

    GvNativeVkDevice &device = bridge->device;
    device.instance = reinterpret_cast<uint64_t>(inst->vkInstance());
    device.get_instance_proc_addr = reinterpret_cast<uint64_t>(getInstanceProcAddr);
    device.physical_device = reinterpret_cast<uint64_t>(nh->physDev);
    device.device = reinterpret_cast<uint64_t>(nh->dev);
    device.queue_family = nh->gfxQueueFamilyIdx;
    device.queue_index = uint32_t(nh->gfxQueueIdx);
    device.api_version = version;
    if (!query_features(bridge.get(), inst, version))
        return nullptr;
    device.features = reinterpret_cast<uint64_t>(&bridge->features);

    GvNativeVk *raw = bridge.get();
    // Direct: runs on the render thread that emits it, while the device is
    // still alive, and never touches Python.
    bridge->invalidated = QObject::connect(
        win, &QQuickWindow::sceneGraphInvalidated, win, [raw]() { tear_down(raw); },
        Qt::DirectConnection);
    return bridge.release();
}

const GvNativeVkDevice *gv_native_vk_device(GvNativeVk *bridge)
{
    return bridge ? &bridge->device : nullptr;
}

void gv_native_vk_on_invalidate(GvNativeVk *bridge, void (*callback)(void *), void *context)
{
    if (!bridge)
        return;
    bridge->onInvalidate = callback;
    bridge->invalidateContext = context;
}

void *gv_native_vk_texture(GvNativeVk *bridge, uint64_t image, int width, int height)
{
    if (!bridge || bridge->torn || !image || width <= 0 || height <= 0) {
        set_error("no image, or the scene graph is gone");
        return nullptr;
    }
    const auto vkImage = reinterpret_cast<VkImage>(image);
    for (const Wrapped &w : bridge->textures) {
        if (w.image == vkImage && w.width == width && w.height == height)
            return w.texture;
    }
    QRhiTexture *rhiTexture = bridge->rhi->newTexture(QRhiTexture::RGBA8, QSize(width, height));
    QRhiTexture::NativeTexture native{quint64(image), VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
    if (!rhiTexture->createFrom(native)) {
        delete rhiTexture;
        set_error("QRhiTexture::createFrom failed");
        return nullptr;
    }
    // Takes ownership of the QRhiTexture (not of the VkImage).
    QSGTexture *texture =
        bridge->window->createTextureFromRhiTexture(rhiTexture, QQuickWindow::TextureIsOpaque);
    if (!texture) {
        delete rhiTexture;
        set_error("createTextureFromRhiTexture failed");
        return nullptr;
    }
    bridge->textures.push_back({vkImage, width, height, texture});
    return texture;
}

int gv_native_vk_stale(GvNativeVk *bridge) { return (bridge && bridge->torn) ? 1 : 0; }

void gv_native_vk_destroy(GvNativeVk *bridge)
{
    if (!bridge)
        return;
    tear_down(bridge);
    delete bridge;
}

}  // extern "C"
