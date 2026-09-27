/*
 * libplacebo's FFmpeg helpers (pl_map_avframe_ex and friends) are written as
 * static inline functions in <libplacebo/utils/libav.h>, meant to be compiled
 * into the caller. Rust cannot inline C, so this file is that caller: with
 * PL_LIBAV_API defined empty, the helpers become ordinary exported functions
 * that the bindings link against.
 */
#define PL_LIBAV_API
#define PL_LIBAV_IMPLEMENTATION 1
#include <libplacebo/utils/libav.h>
