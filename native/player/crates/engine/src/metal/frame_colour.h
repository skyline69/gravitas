/*
 * A decoded frame's colour metadata, flattened for the Metal renderer.
 *
 * FFmpeg keeps HDR and Dolby Vision metadata as side data whose structs
 * (mastering_display_metadata.h, dovi_meta.h) ffmpeg-sys-next does not bind,
 * and whose accessors are static inline. Compiled against the installed
 * headers, this reads them into plain floats the Rust side declares once.
 * Layouts here and in colour.rs must match.
 */
#ifndef GRAVITAS_FRAME_COLOUR_H
#define GRAVITAS_FRAME_COLOUR_H

#include <stdint.h>

struct AVFrame;

/* Static HDR metadata. Zero means "not signalled". */
typedef struct GvHdrMetadata {
    float max_luma;       /* mastering display peak, nits */
    float min_luma;       /* mastering display black, nits */
    float max_cll;        /* content light level, nits */
    float max_fall;
} GvHdrMetadata;

/* One component's reshaping curve (see ETSI GS CCM 001, and FFmpeg's
 * AVDOVIReshapingCurve): pieces between pivots, each a polynomial of the
 * component itself or an MMR of all three. Pivots and coefficients are
 * normalised: pivots to the base layer's code range, coefficients by
 * 2^coef_log2_denom. */
typedef struct GvDoviCurve {
    int32_t num_pivots;          /* 0 (no reshaping), else [2, 9] */
    float pivots[9];
    int32_t method[8];           /* 0 polynomial, 1 MMR */
    float poly[8][3];            /* x^0, x^1, x^2 */
    int32_t mmr_order[8];        /* [1, 3] */
    float mmr_constant[8];
    float mmr[8][3][7];
} GvDoviCurve;

typedef struct GvDovi {
    float nonlinear[9];          /* ycc_to_rgb, row-major */
    float nonlinear_offset[3];
    float linear[9];             /* rgb_to_lms, row-major */
    float source_min_pq;         /* [0, 1] PQ */
    float source_max_pq;
    float l1_avg_pq;             /* 0 without a level 1 block */
    float l1_max_pq;
    int32_t bl_bit_depth;
    GvDoviCurve comp[3];
} GvDovi;

/* 1 when the frame carries mastering or content light metadata. */
int gv_frame_hdr_metadata(const struct AVFrame *frame, GvHdrMetadata *out);

/* 1 when the frame carries Dolby Vision metadata the renderer can apply:
 * parsed by FFmpeg, and with no enhancement layer residual to add (profile
 * 7's full enhancement layer is not decoded anywhere here). */
int gv_frame_dovi(const struct AVFrame *frame, GvDovi *out);

/* Attaches `dovi` to `frame` as FFmpeg's parsed Dolby Vision metadata, for
 * the tests: the inverse of gv_frame_dovi, with coefficients in 2^-23 fixed
 * point and matrices as rationals. 1 on success. */
int gv_frame_set_dovi(struct AVFrame *frame, const GvDovi *dovi);

#endif
