/*
 * One Vulkan device for Qt, libplacebo and FFmpeg's decoder: see
 * shared_device.c for why it is made here and what it promises Qt.
 */
#ifndef GRAVITAS_SHARED_DEVICE_H
#define GRAVITAS_SHARED_DEVICE_H

#include <stddef.h>
#include <stdint.h>

#include <libavutil/buffer.h>
#include <libplacebo/vulkan.h>

/* Creates the device on `instance`, with its video decode queues, every
 * feature the GPU supports but robustness, and the extensions Qt and FFmpeg
 * may use. NULL on failure (libplacebo logs why through `log`). */
pl_vulkan gv_shared_device_create(pl_log log, VkInstance instance,
                                  PFN_vkGetInstanceProcAddr get_proc_addr);

/* FFmpeg's view of the device, for hardware decoding into it. NULL, with
 * the reason in `why`, when the device has no decode queue or no compute
 * queue apart from the graphics one, or FFmpeg refuses it. */
AVBufferRef *gv_shared_device_decoder(pl_vulkan vk, char *why, size_t why_size);

/* The VkVideoCodecOperationFlagsKHR every decode queue of the device
 * offers together. */
uint32_t gv_shared_device_decode_operations(pl_vulkan vk);

#endif
