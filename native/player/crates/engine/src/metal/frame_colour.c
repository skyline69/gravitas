/* See frame_colour.h. */
#include <string.h>

#include <libavutil/dovi_meta.h>
#include <libavutil/frame.h>
#include <libavutil/mastering_display_metadata.h>
#include <libavutil/buffer.h>
#include <libavutil/mem.h>
#include <libavutil/rational.h>

#include "frame_colour.h"

int gv_frame_hdr_metadata(const AVFrame *frame, GvHdrMetadata *out)
{
    memset(out, 0, sizeof(*out));
    int found = 0;
    const AVFrameSideData *sd =
        av_frame_get_side_data(frame, AV_FRAME_DATA_MASTERING_DISPLAY_METADATA);
    if (sd) {
        const AVMasteringDisplayMetadata *mdm = (const AVMasteringDisplayMetadata *) sd->data;
        if (mdm->has_luminance) {
            out->max_luma = (float) av_q2d(mdm->max_luminance);
            out->min_luma = (float) av_q2d(mdm->min_luminance);
            found = 1;
        }
    }
    sd = av_frame_get_side_data(frame, AV_FRAME_DATA_CONTENT_LIGHT_LEVEL);
    if (sd) {
        const AVContentLightMetadata *clm = (const AVContentLightMetadata *) sd->data;
        out->max_cll = (float) clm->MaxCLL;
        out->max_fall = (float) clm->MaxFALL;
        found = 1;
    }
    return found;
}

int gv_frame_dovi(const AVFrame *frame, GvDovi *out)
{
    memset(out, 0, sizeof(*out));
    const AVFrameSideData *sd = av_frame_get_side_data(frame, AV_FRAME_DATA_DOVI_METADATA);
    if (!sd)
        return 0;
    const AVDOVIMetadata *meta = (const AVDOVIMetadata *) sd->data;
    const AVDOVIRpuDataHeader *header = av_dovi_get_header(meta);
    const AVDOVIDataMapping *mapping = av_dovi_get_mapping(meta);
    const AVDOVIColorMetadata *color = av_dovi_get_color(meta);
    if (!header->disable_residual_flag)
        return 0;

    for (int i = 0; i < 9; i++) {
        out->nonlinear[i] = (float) av_q2d(color->ycc_to_rgb_matrix[i]);
        out->linear[i] = (float) av_q2d(color->rgb_to_lms_matrix[i]);
    }
    for (int i = 0; i < 3; i++)
        out->nonlinear_offset[i] = (float) av_q2d(color->ycc_to_rgb_offset[i]);
    out->source_min_pq = color->source_min_pq / 4095.0f;
    out->source_max_pq = color->source_max_pq / 4095.0f;
    const AVDOVIDmData *l1 = av_dovi_find_level(meta, 1);
    if (l1) {
        out->l1_avg_pq = l1->l1.avg_pq / 4095.0f;
        out->l1_max_pq = l1->l1.max_pq / 4095.0f;
    }
    out->bl_bit_depth = header->bl_bit_depth;

    const float pivot_scale = 1.0f / (float) ((1 << header->bl_bit_depth) - 1);
    const float coef_scale = 1.0f / (float) (1LL << header->coef_log2_denom);
    for (int c = 0; c < 3; c++) {
        const AVDOVIReshapingCurve *src = &mapping->curves[c];
        GvDoviCurve *dst = &out->comp[c];
        dst->num_pivots = src->num_pivots;
        for (int i = 0; i < src->num_pivots && i < 9; i++)
            dst->pivots[i] = pivot_scale * src->pivots[i];
        for (int i = 0; i < src->num_pivots - 1 && i < 8; i++) {
            dst->method[i] = src->mapping_idc[i] == AV_DOVI_MAPPING_MMR ? 1 : 0;
            if (dst->method[i] == 0) {
                for (int k = 0; k < 3; k++)
                    dst->poly[i][k] = k <= src->poly_order[i] ? coef_scale * src->poly_coef[i][k] : 0.0f;
            } else {
                dst->mmr_order[i] = src->mmr_order[i];
                dst->mmr_constant[i] = coef_scale * src->mmr_constant[i];
                for (int j = 0; j < src->mmr_order[i] && j < 3; j++)
                    for (int k = 0; k < 7; k++)
                        dst->mmr[i][j][k] = coef_scale * src->mmr_coef[i][j][k];
            }
        }
    }
    return 1;
}

int gv_frame_set_dovi(AVFrame *frame, const GvDovi *dovi)
{
    size_t size = 0;
    AVDOVIMetadata *meta = av_dovi_metadata_alloc(&size);
    if (!meta)
        return 0;
    AVDOVIRpuDataHeader *header = av_dovi_get_header(meta);
    AVDOVIDataMapping *mapping = av_dovi_get_mapping(meta);
    AVDOVIColorMetadata *color = av_dovi_get_color(meta);
    const int denom = 23;
    header->rpu_type = 2;
    header->vdr_rpu_profile = 1;
    header->coef_log2_denom = denom;
    header->bl_bit_depth = (uint8_t) dovi->bl_bit_depth;
    header->el_bit_depth = (uint8_t) dovi->bl_bit_depth;
    header->vdr_bit_depth = 12;
    header->disable_residual_flag = 1;
    const double pivot_scale = (double) ((1 << dovi->bl_bit_depth) - 1);
    const double coef_scale = (double) (1 << denom);
    for (int c = 0; c < 3; c++) {
        const GvDoviCurve *src = &dovi->comp[c];
        AVDOVIReshapingCurve *dst = &mapping->curves[c];
        dst->num_pivots = (uint8_t) src->num_pivots;
        for (int i = 0; i < src->num_pivots; i++)
            dst->pivots[i] = (uint16_t) (src->pivots[i] * pivot_scale + 0.5);
        for (int i = 0; i < src->num_pivots - 1; i++) {
            if (src->method[i] == 0) {
                dst->mapping_idc[i] = AV_DOVI_MAPPING_POLYNOMIAL;
                dst->poly_order[i] = 2;
                for (int k = 0; k < 3; k++)
                    dst->poly_coef[i][k] = (int64_t) (src->poly[i][k] * coef_scale);
            } else {
                dst->mapping_idc[i] = AV_DOVI_MAPPING_MMR;
                dst->mmr_order[i] = (uint8_t) src->mmr_order[i];
                dst->mmr_constant[i] = (int64_t) (src->mmr_constant[i] * coef_scale);
                for (int j = 0; j < src->mmr_order[i]; j++)
                    for (int k = 0; k < 7; k++)
                        dst->mmr_coef[i][j][k] = (int64_t) (src->mmr[i][j][k] * coef_scale);
            }
        }
    }
    for (int i = 0; i < 9; i++) {
        color->ycc_to_rgb_matrix[i] = av_d2q(dovi->nonlinear[i], 1 << 28);
        color->rgb_to_lms_matrix[i] = av_d2q(dovi->linear[i], 1 << 28);
    }
    for (int i = 0; i < 3; i++)
        color->ycc_to_rgb_offset[i] = av_d2q(dovi->nonlinear_offset[i], 1 << 28);
    color->source_min_pq = (uint16_t) (dovi->source_min_pq * 4095.0f + 0.5f);
    color->source_max_pq = (uint16_t) (dovi->source_max_pq * 4095.0f + 0.5f);

    AVBufferRef *buffer = av_buffer_create((uint8_t *) meta, size, av_buffer_default_free, NULL, 0);
    if (!buffer) {
        av_free(meta);
        return 0;
    }
    if (!av_frame_new_side_data_from_buf(frame, AV_FRAME_DATA_DOVI_METADATA, buffer)) {
        av_buffer_unref(&buffer);
        return 0;
    }
    return 1;
}
