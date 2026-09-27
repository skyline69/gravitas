/*
 * One Vulkan device for Qt, libplacebo and FFmpeg's decoder.
 *
 * Decoding into images the renderer samples needs the decoder and the
 * renderer on the same VkDevice, and that device needs a video decode queue.
 * Qt creates its own device with a graphics queue and nothing else, so the
 * device is made here, by libplacebo, and Qt is handed it to adopt
 * (QQuickWindow::setGraphicsDevice). Two consequences shape this file:
 *
 * - Qt assumes things of an adopted device it cannot check: the optional
 *   extensions it would have enabled itself, and every feature the physical
 *   device reports (QRhiVulkan reads its caps from the physical device, not
 *   from what was enabled). So the device enables what Qt's own would --
 *   every supported feature but robustness -- and Qt's optional extensions.
 *
 * - Qt submits to its graphics queue without libplacebo's queue lock. That
 *   is safe for the renderer, which submits to the same queue on the same
 *   render thread, but not for the decoder threads. So FFmpeg gets the
 *   decode, compute and transfer families only; the graphics family is
 *   listed with nothing but VK_QUEUE_GRAPHICS_BIT, which FFmpeg's decoder
 *   never asks for, so that its images are shared with that family
 *   (VK_SHARING_MODE_CONCURRENT across every listed family) without FFmpeg
 *   ever submitting there.
 */
#include <stddef.h>
#include <stdio.h>

#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vulkan.h>
#include <libavutil/version.h>
#include <libplacebo/vulkan.h>

#include "shared_device.h"

/* The extensions QRhiVulkan enables on a device of its own when they are
 * there (qrhivulkan.cpp), and assumes on one it adopts. */
static const char *const qt_extensions[] = {
    "VK_EXT_vertex_attribute_divisor",
    "VK_KHR_create_renderpass2",
    "VK_KHR_depth_stencil_resolve",
    "VK_KHR_fragment_shading_rate",
};

/* What FFmpeg decodes with, and uses when present. FFmpeg decides by the
 * extension alone -- it enables the matching features on a device it makes
 * itself -- so each one with a feature struct has that struct switched on
 * below, and none is listed that is not handled there. (Its full optional
 * list, av_vk_get_optional_device_extensions(), would enable extensions
 * whose features nobody turns on: validation caught videoMaintenance1 and
 * videoMaintenance2 used unenabled.) */
static const char *const decode_extensions[] = {
    "VK_KHR_video_queue",
    "VK_KHR_video_decode_queue",
    "VK_KHR_video_decode_h264",
    "VK_KHR_video_decode_h265",
    "VK_KHR_video_decode_av1",
#ifdef VK_KHR_video_decode_vp9
    "VK_KHR_video_decode_vp9",        /* videoDecodeVP9 */
#endif
#ifdef VK_KHR_video_maintenance1
    "VK_KHR_video_maintenance1",      /* videoMaintenance1 */
#endif
#ifdef VK_KHR_video_maintenance2
    "VK_KHR_video_maintenance2",      /* videoMaintenance2 */
#endif
#ifdef VK_EXT_host_image_copy
    "VK_EXT_host_image_copy",         /* hostImageCopy: faster copies to memory */
#endif
    "VK_KHR_push_descriptor",
    "VK_EXT_external_memory_host",
};

/* Sets every flag of a Vulkan feature struct: after sType and pNext, each
 * is nothing but VkBool32s. */
static void enable_every_flag(void *features, size_t size)
{
    const size_t head = offsetof(VkPhysicalDeviceVulkan11Features, pNext) + sizeof(void *);
    VkBool32 *flags = (VkBool32 *) ((char *) features + head);
    for (size_t i = 0; i < (size - head) / sizeof(VkBool32); i++)
        flags[i] = VK_TRUE;
}

pl_vulkan gv_shared_device_create(pl_log log, VkInstance instance,
                                  PFN_vkGetInstanceProcAddr get_proc_addr)
{
    /* Asked for, not demanded: libplacebo enables each one the physical
     * device supports, which is what Qt does on its own device. */
    VkPhysicalDeviceVulkan13Features features13 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
    };
    VkPhysicalDeviceVulkan12Features features12 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
        .pNext = &features13,
    };
    VkPhysicalDeviceVulkan11Features features11 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES,
        .pNext = &features12,
    };
    VkPhysicalDeviceFeatures2 features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2,
        .pNext = &features11,
    };
    VkBool32 *core = (VkBool32 *) &features.features;
    for (size_t i = 0; i < sizeof(features.features) / sizeof(VkBool32); i++)
        core[i] = VK_TRUE;
    enable_every_flag(&features11, sizeof(features11));
    enable_every_flag(&features12, sizeof(features12));
    enable_every_flag(&features13, sizeof(features13));

    /* The features of decode_extensions, as far as these headers know them
     * (each is listed there under the same condition). libplacebo drops any
     * the GPU does not report, so a struct here never outlives its missing
     * extension. */
    void *extension_features = NULL;
#ifdef VK_EXT_host_image_copy
    VkPhysicalDeviceHostImageCopyFeaturesEXT host_image_copy = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_HOST_IMAGE_COPY_FEATURES_EXT,
        .pNext = extension_features,
        .hostImageCopy = VK_TRUE,
    };
    extension_features = &host_image_copy;
#endif
#ifdef VK_KHR_video_maintenance1
    VkPhysicalDeviceVideoMaintenance1FeaturesKHR maintenance1 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VIDEO_MAINTENANCE_1_FEATURES_KHR,
        .pNext = extension_features,
        .videoMaintenance1 = VK_TRUE,
    };
    extension_features = &maintenance1;
#endif
#ifdef VK_KHR_video_maintenance2
    VkPhysicalDeviceVideoMaintenance2FeaturesKHR maintenance2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VIDEO_MAINTENANCE_2_FEATURES_KHR,
        .pNext = extension_features,
        .videoMaintenance2 = VK_TRUE,
    };
    extension_features = &maintenance2;
#endif
#ifdef VK_KHR_video_decode_vp9
    VkPhysicalDeviceVideoDecodeVP9FeaturesKHR vp9 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VIDEO_DECODE_VP9_FEATURES_KHR,
        .pNext = extension_features,
        .videoDecodeVP9 = VK_TRUE,
    };
    extension_features = &vp9;
#endif
    features13.pNext = extension_features;
    /* Qt leaves robustness off for its cost; so does this device. */
    features.features.robustBufferAccess = VK_FALSE;
    features13.robustImageAccess = VK_FALSE;

    enum {
        QT_COUNT = sizeof(qt_extensions) / sizeof(qt_extensions[0]),
        DECODE_COUNT = sizeof(decode_extensions) / sizeof(decode_extensions[0]),
    };
    const char *optional[QT_COUNT + DECODE_COUNT];
    for (int i = 0; i < QT_COUNT; i++)
        optional[i] = qt_extensions[i];
    for (int i = 0; i < DECODE_COUNT; i++)
        optional[QT_COUNT + i] = decode_extensions[i];

    static const char *const required[] = {"VK_KHR_swapchain"};
    pl_vulkan vk = pl_vulkan_create(log, pl_vulkan_params(
        .instance           = instance,
        .get_proc_addr      = get_proc_addr,
        .extra_queues       = VK_QUEUE_VIDEO_DECODE_BIT_KHR,
        .extensions         = required,
        .num_extensions     = 1,
        .opt_extensions     = optional,
        .num_opt_extensions = QT_COUNT + DECODE_COUNT,
        .features           = &features,
        /* Qt enables features through the 1.3 struct and none of 1.4's
         * (see gravitas_native_vk.cpp); the renderer on Qt's side is told
         * the same version. */
        .max_api_version    = VK_API_VERSION_1_3,
    ));
    return vk;
}

#if LIBAVUTIL_VERSION_INT >= AV_VERSION_INT(59, 34, 100)
static void lock_queue(AVHWDeviceContext *context, uint32_t family, uint32_t index)
{
    pl_vulkan vk = context->user_opaque;
    vk->lock_queue(vk, family, index);
}

static void unlock_queue(AVHWDeviceContext *context, uint32_t family, uint32_t index)
{
    pl_vulkan vk = context->user_opaque;
    vk->unlock_queue(vk, family, index);
}
#endif

/* The device's queue families, with the video operations of each. At most
 * 64, like AVVulkanDeviceContext.qf. Returns how many were filled. */
static uint32_t query_families(pl_vulkan vk, VkQueueFamilyProperties2 families[64],
                               VkQueueFamilyVideoPropertiesKHR video[64])
{
    PFN_vkGetPhysicalDeviceQueueFamilyProperties2 get_families =
        (PFN_vkGetPhysicalDeviceQueueFamilyProperties2) vk->get_proc_addr(
            vk->instance, "vkGetPhysicalDeviceQueueFamilyProperties2");
    if (!get_families)
        return 0;
    uint32_t count = 0;
    get_families(vk->phys_device, &count, NULL);
    if (count > 64)
        count = 64;
    for (uint32_t i = 0; i < count; i++) {
        video[i] = (VkQueueFamilyVideoPropertiesKHR) {
            .sType = VK_STRUCTURE_TYPE_QUEUE_FAMILY_VIDEO_PROPERTIES_KHR,
        };
        families[i] = (VkQueueFamilyProperties2) {
            .sType = VK_STRUCTURE_TYPE_QUEUE_FAMILY_PROPERTIES_2,
            .pNext = &video[i],
        };
    }
    get_families(vk->phys_device, &count, families);
    return count;
}

AVBufferRef *gv_shared_device_decoder(pl_vulkan vk, char *why, size_t why_size)
{
#if LIBAVUTIL_VERSION_INT < AV_VERSION_INT(59, 34, 100)
    /* Queue families are handed over as AVVulkanDeviceContext.qf[] since
     * FFmpeg 7.1, whose Vulkan decoding is also the first worth using. */
    (void) vk;
    snprintf(why, why_size, "FFmpeg is older than 7.1");
    return NULL;
#else
    const uint32_t graphics = vk->queue_graphics.index;
    /* A queue the decoder may submit to must not be one Qt submits to. */
    if (vk->queue_compute.index == graphics) {
        snprintf(why, why_size, "no compute queue apart from the graphics queue");
        return NULL;
    }
    /* Compute queues take transfers too, when there is no family of its own. */
    const struct pl_vulkan_queue transfer =
        vk->queue_transfer.index != graphics ? vk->queue_transfer : vk->queue_compute;

    VkQueueFamilyProperties2 families[64];
    VkQueueFamilyVideoPropertiesKHR video[64];
    const uint32_t count = query_families(vk, families, video);

    AVBufferRef *reference = av_hwdevice_ctx_alloc(AV_HWDEVICE_TYPE_VULKAN);
    if (!reference) {
        snprintf(why, why_size, "out of memory");
        return NULL;
    }
    AVHWDeviceContext *context = (AVHWDeviceContext *) reference->data;
    AVVulkanDeviceContext *device = context->hwctx;
    context->user_opaque = (void *) vk;
    device->get_proc_addr = vk->get_proc_addr;
    device->inst = vk->instance;
    device->phys_dev = vk->phys_device;
    device->act_dev = vk->device;
    device->device_features = *vk->features;
    device->enabled_dev_extensions = vk->extensions;
    device->nb_enabled_dev_extensions = vk->num_extensions;
#if FF_API_VULKAN_SYNC_QUEUES
    /* libplacebo's own lock, so the decoder and libplacebo never submit to
     * one queue at once. Deprecated in favour of
     * VK_KHR_internally_synchronized_queues, as mpv notes too. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
    device->lock_queue = lock_queue;
    device->unlock_queue = unlock_queue;
#pragma GCC diagnostic pop
#endif

    device->nb_qf = 0;
    /* Listed so the decoder's images are shared with it; never chosen. */
    device->qf[device->nb_qf++] = (AVVulkanDeviceQueueFamily) {
        .idx = (int) graphics, .num = 1, .flags = VK_QUEUE_GRAPHICS_BIT,
    };
    device->qf[device->nb_qf++] = (AVVulkanDeviceQueueFamily) {
        .idx = (int) vk->queue_compute.index,
        .num = (int) vk->queue_compute.count,
        .flags = VK_QUEUE_COMPUTE_BIT,
    };
    device->qf[device->nb_qf++] = (AVVulkanDeviceQueueFamily) {
        .idx = (int) transfer.index,
        .num = (int) transfer.count,
        .flags = VK_QUEUE_TRANSFER_BIT,
    };
    int decoders = 0;
    for (uint32_t i = 0; i < count && device->nb_qf < 64; i++) {
        if (!(families[i].queueFamilyProperties.queueFlags & VK_QUEUE_VIDEO_DECODE_BIT_KHR))
            continue;
        device->qf[device->nb_qf++] = (AVVulkanDeviceQueueFamily) {
            .idx = (int) i,
            .num = (int) families[i].queueFamilyProperties.queueCount,
            .flags = VK_QUEUE_VIDEO_DECODE_BIT_KHR,
            .video_caps = video[i].videoCodecOperations,
        };
        decoders++;
    }
    if (!decoders) {
        av_buffer_unref(&reference);
        snprintf(why, why_size, "no video decode queue");
        return NULL;
    }

    int error = av_hwdevice_ctx_init(reference);
    if (error < 0) {
        av_buffer_unref(&reference);
        snprintf(why, why_size, "FFmpeg refused the device (error %d)", error);
        return NULL;
    }
    return reference;
#endif
}

uint32_t gv_shared_device_decode_operations(pl_vulkan vk)
{
    VkQueueFamilyProperties2 families[64];
    VkQueueFamilyVideoPropertiesKHR video[64];
    const uint32_t count = query_families(vk, families, video);
    uint32_t operations = 0;
    for (uint32_t i = 0; i < count; i++) {
        if (families[i].queueFamilyProperties.queueFlags & VK_QUEUE_VIDEO_DECODE_BIT_KHR)
            operations |= video[i].videoCodecOperations;
    }
    return operations;
}
