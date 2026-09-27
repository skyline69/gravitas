// The Metal renderer's shaders: a decoded frame's planes in, an
// sRGB-encoded picture out. The per-frame parameters are computed on the
// CPU (colour.rs); the variants -- plane layout, transfer, Dolby Vision, tone
// mapping, subtitles -- are function constants, so each pipeline carries
// only the code its frames need.
//
// HDR takes three passes in one command buffer, so nothing waits on the CPU:
// `measure_peak` takes a histogram of the frame's brightness, `update_tone`
// reads the scene's peak off it, smooths that against earlier frames and
// derives the tone curve, and `video_fragment` draws with the curve.
//
// Tone mapping is ITU-R BT.2390's EETF on the intensity of BT.2100 ICtCp,
// with BT.2390's chroma adjustment; everything outside the standards (the
// peak estimate, its smoothing, the gamut compression) is this file's own.

#include <metal_stdlib>
using namespace metal;

constant int LAYOUT [[function_constant(0)]];     // 0 planar, 1 semi-planar, 2 packed
constant int TRANSFER [[function_constant(1)]];   // colour.rs Transfer
constant bool DOVI [[function_constant(2)]];
constant bool TONE_MAP [[function_constant(3)]];
constant bool OVERLAY [[function_constant(4)]];

constant int BT1886 = 1;
constant int SRGB = 2;
constant int GAMMA = 3;
constant int PQ = 4;
constant int HLG = 5;

constant float PQ_M1 = 2610.0 / 4096.0 / 4.0;
constant float PQ_M2 = 2523.0 / 4096.0 * 128.0;
constant float PQ_C1 = 3424.0 / 4096.0;
constant float PQ_C2 = 2413.0 / 4096.0 * 32.0;
constant float PQ_C3 = 2392.0 / 4096.0 * 32.0;
constant float HLG_A = 0.17883277;
constant float HLG_B = 0.28466892;
constant float HLG_C = 0.55991073;
constant float SDR_WHITE = 203.0;

// Brightness is measured as a histogram over PQ.
constant uint BINS = 256;

// Mirrors metal/mod.rs `Params`: every member is a float4 or an array of
// them, so the two layouts cannot drift apart on alignment.
struct Params {
    float4 decode[3];        // rows of the signal -> R'G'B' matrix, offset in .w
    float4 sampling;         // sample scale, chroma offset (x, y) in chroma texels, dither step
    float4 transfer;         // colour.rs Plan::transfer_params
    float4 luma;             // .xyz the source's luma weights
    float4 linear_scale;     // .x scale, .y black, .z output black, .w gamut compression (1)
    float4 to_output[3];     // linear source RGB -> linear BT.709, rows
    float4 to_lms[3];        // linear source RGB -> BT.2100 LMS
    float4 from_lms[3];      // BT.2100 LMS -> linear BT.709
    float4 to_ictcp[3];      // L'M'S' -> ICtCp
    float4 from_ictcp[3];
    float4 dovi_linear[3];
    float4 tone_range;       // source black, display black, display peak (PQ), reset (1 = forget earlier frames)
    float4 tone_metadata;    // use (1), scene peak (PQ): dynamic metadata instead of measuring
};

// One component's Dolby Vision reshaping.
struct DoviComponent {
    float4 coeffs[8];        // per piece: poly x^0..x^2 and 0, or MMR constant, index, -, order
    float4 mmr[48];          // per piece and order, two float4s of weights
    float4 pivots[2];        // the inner pivots, then a 1e9 sentinel
    float4 range;            // lo, hi, pieces (0 = no reshaping), -
};

struct DoviParams {
    DoviComponent comp[3];
};

// What the HDR passes share, in one buffer that lives with the renderer.
struct ToneState {
    atomic_uint histogram[BINS];  // this frame's brightness, PQ in 256 bins
    float peak;                   // the scene's peak (PQ), smoothed; 0 = none yet
    float3 unused;
    float4 curve[2];              // BT.2390: (source black, source peak, knee, display peak), (display black, -, -, -); normalised as the EETF wants them
};

// The same buffer as the fragment reads it.
struct ToneView {
    uint histogram[BINS];
    float peak;
    float3 unused;
    float4 curve[2];
};

struct Varyings {
    float4 position [[position]];
    float2 uv;
};

// One triangle covering the target; uv (0,0) is the picture's top left.
vertex Varyings video_vertex(uint vid [[vertex_id]]) {
    float2 corner = float2((vid << 1) & 2, vid & 2);
    Varyings out;
    out.position = float4(corner * 2.0 - 1.0, 0.0, 1.0);
    out.uv = float2(corner.x, 1.0 - corner.y);
    return out;
}

static float3 pq_eotf(float3 x) {  // PQ signal -> nits / 10000
    x = pow(max(x, 0.0), 1.0 / PQ_M2);
    x = max(x - PQ_C1, 0.0) / (PQ_C2 - PQ_C3 * x);
    return pow(x, 1.0 / PQ_M1);
}

static float3 pq_oetf(float3 x) {  // nits / 10000 -> PQ signal
    x = pow(max(x, 0.0), PQ_M1);
    x = (PQ_C1 + PQ_C2 * x) / (1.0 + PQ_C3 * x);
    return pow(x, PQ_M2);
}

static float pq_from_nits(float nits) {
    return pq_oetf(float3(nits / 10000.0)).x;
}

static float3x3 rows(constant float4 m[3]) {
    // Metal matrices are column-major: transpose the rows in.
    return transpose(float3x3(m[0].xyz, m[1].xyz, m[2].xyz));
}

static float reshape(constant DoviComponent &c, float3 sig, float s) {
    int pieces = int(c.range.z);
    int piece = 0;
    for (int i = 0; i < 7; i++) {
        float pivot = c.pivots[i / 4][i % 4];
        piece += (i < pieces - 1 && s >= pivot) ? 1 : 0;
    }
    float4 k = c.coeffs[piece];
    if (k.w == 0.0) {
        return (k.z * s + k.y) * s + k.x;
    }
    // Multivariate multiple regression over all three components.
    int idx = int(k.y);
    int order = int(k.w);
    float4 x = float4(sig.xxy * sig.yzz, sig.x * sig.y * sig.z);
    float out = k.x + dot(c.mmr[idx].xyz, sig) + dot(c.mmr[idx + 1], x);
    if (order >= 2) {
        float3 s2 = sig * sig;
        float4 x2 = x * x;
        out += dot(c.mmr[idx + 2].xyz, s2) + dot(c.mmr[idx + 3], x2);
        if (order >= 3) {
            out += dot(c.mmr[idx + 4].xyz, s2 * sig) + dot(c.mmr[idx + 5], x2 * x);
        }
    }
    return out;
}

// The frame at `uv` in linear light, 1.0 = SDR reference white, in the
// source's primaries (BT.2020 for Dolby Vision).
static float3 decode_linear(
    float2 uv,
    constant Params &p,
    constant DoviParams &dv,
    texture2d<float> plane0,
    texture2d<float> plane1,
    texture2d<float> plane2)
{
    constexpr sampler smooth(filter::linear, address::clamp_to_edge);

    // Sample. Chroma is offset to where its samples were sited.
    float3 sig;
    if (LAYOUT == 2) {
        sig = plane0.sample(smooth, uv).rgb;
    } else {
        float2 chroma_size = float2(plane1.get_width(), plane1.get_height());
        float2 cuv = uv + p.sampling.yz / chroma_size;
        sig.x = plane0.sample(smooth, uv).r;
        if (LAYOUT == 1) {
            sig.yz = plane1.sample(smooth, cuv).rg;
        } else {
            sig.y = plane1.sample(smooth, cuv).r;
            sig.z = plane2.sample(smooth, cuv).r;
        }
    }
    sig *= p.sampling.x;

    if (DOVI) {
        float3 clamped = clamp(sig, 0.0, 1.0);
        for (int c = 0; c < 3; c++) {
            constant DoviComponent &comp = dv.comp[c];
            if (comp.range.z > 0.0) {
                sig[c] = clamp(reshape(comp, clamped, clamped[c]), comp.range.x, comp.range.y);
            }
        }
    }

    float3 rgb = rows(p.decode) * sig + float3(p.decode[0].w, p.decode[1].w, p.decode[2].w);

    if (DOVI) {
        return rows(p.dovi_linear) * pq_eotf(rgb) * (10000.0 / SDR_WHITE);
    } else if (TRANSFER == BT1886) {
        return p.transfer.x * pow(max(rgb, 0.0) + p.transfer.y, 2.4);
    } else if (TRANSFER == SRGB) {
        rgb = max(rgb, 0.0);
        rgb = select(pow((rgb + 0.055) / 1.055, 2.4), rgb / 12.92, rgb <= 0.04045);
        return p.linear_scale.x * rgb + p.linear_scale.y;
    } else if (TRANSFER == GAMMA) {
        return p.linear_scale.x * pow(max(rgb, 0.0), p.transfer.x) + p.linear_scale.y;
    } else if (TRANSFER == PQ) {
        return pq_eotf(rgb) * (10000.0 / SDR_WHITE);
    } else if (TRANSFER == HLG) {
        rgb = p.transfer.x * max(rgb, 0.0) + p.transfer.y;
        rgb = select(exp((rgb - HLG_C) / HLG_A) + HLG_B, 4.0 * rgb * rgb, rgb <= 0.5);
        rgb *= 1.0 / 12.0;
        return rgb * p.transfer.z * pow(max(dot(p.luma.xyz, rgb), 0.0), p.transfer.w);
    }
    return p.linear_scale.x * max(rgb, 0.0) + p.linear_scale.y;
}

// --- HDR brightness measurement -----------------------------------------

// A histogram of the frame's brightness (the luminance of its linear light,
// as PQ), gathered per threadgroup and merged once per group.
kernel void measure_peak(
    constant Params &p [[buffer(0)]],
    constant DoviParams &dv [[buffer(1), function_constant(DOVI)]],
    device ToneState &state [[buffer(2)]],
    texture2d<float> plane0 [[texture(0)]],
    texture2d<float> plane1 [[texture(1)]],
    texture2d<float> plane2 [[texture(2)]],
    uint2 gid [[thread_position_in_grid]],
    uint lid [[thread_index_in_threadgroup]],
    uint2 group_size [[threads_per_threadgroup]])
{
    threadgroup atomic_uint bins[BINS];
    uint threads = group_size.x * group_size.y;
    for (uint i = lid; i < BINS; i += threads) {
        atomic_store_explicit(&bins[i], 0u, memory_order_relaxed);
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);

    uint2 size = uint2(plane0.get_width(), plane0.get_height());
    if (all(gid < size)) {
        float2 uv = (float2(gid) + 0.5) / float2(size);
        float3 rgb = decode_linear(uv, p, dv, plane0, plane1, plane2);
        float luma = dot(p.luma.xyz, rgb) * (SDR_WHITE / 10000.0);
        float pq = pq_oetf(float3(clamp(luma, 0.0, 1.0))).x;
        uint bin = min(uint(pq * float(BINS)), BINS - 1);
        atomic_fetch_add_explicit(&bins[bin], 1u, memory_order_relaxed);
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);

    for (uint i = lid; i < BINS; i += threads) {
        uint count = atomic_load_explicit(&bins[i], memory_order_relaxed);
        if (count > 0) {
            atomic_fetch_add_explicit(&state.histogram[i], count, memory_order_relaxed);
        }
    }
}

// One thread: the scene's peak from this frame's histogram, smoothed, and
// the EETF's parameters from it.
//
// The peak is where all but one pixel in ten thousand are darker: a few
// specular pixels do not set the level for the whole picture. It rises
// within a few frames (a highlight clipped for long is visible) and falls
// over about a second (a scene that dims gradually should not pump), and a
// change of more than a tenth of PQ's range is taken as a cut and adopted
// at once.
kernel void update_tone(constant Params &p [[buffer(0)]], device ToneState &state [[buffer(2)]])
{
    uint total = 0;
    for (uint i = 0; i < BINS; i++) {
        total += atomic_load_explicit(&state.histogram[i], memory_order_relaxed);
    }
    float frame_peak = 0.0;
    if (total > 0) {
        uint wanted = total - total / 10000;
        uint seen = 0;
        for (uint i = 0; i < BINS; i++) {
            seen += atomic_load_explicit(&state.histogram[i], memory_order_relaxed);
            if (seen >= wanted) {
                frame_peak = float(i + 1) / float(BINS);
                break;
            }
        }
    }
    for (uint i = 0; i < BINS; i++) {
        atomic_store_explicit(&state.histogram[i], 0u, memory_order_relaxed);
    }

    if (total > 0) {
        float delta = frame_peak - state.peak;
        if (p.tone_range.w > 0.0 || state.peak == 0.0 || abs(delta) > 0.1) {
            state.peak = frame_peak;
        } else {
            state.peak += delta * (delta > 0.0 ? 0.25 : 1.0 / 32.0);
        }
    }

    // BT.2390: source range [Lb, Lw], display range [min, max], in PQ.
    float lb = p.tone_range.x;
    float lw = p.tone_metadata.x > 0.0 ? p.tone_metadata.y : state.peak;
    // A scene no brighter than the display is shown as it is.
    lw = max(lw, p.tone_range.z);
    float range = max(lw - lb, 1e-6);
    float max_lum = (p.tone_range.z - lb) / range;
    float min_lum = (p.tone_range.y - lb) / range;
    float knee = 1.5 * max_lum - 0.5;
    state.curve[0] = float4(lb, lw, knee, max_lum);
    state.curve[1] = float4(min_lum, 0.0, 0.0, 0.0);
}

// --- Drawing ----------------------------------------------------------------

// ITU-R BT.2390's EETF on a PQ value: identity below the knee, a Hermite
// spline from the knee to the display's peak, and the display's black
// raised in.
static float eetf(const device ToneView &view, float e) {
    float4 c = view.curve[0];
    float lb = c.x, lw = c.y, knee = c.z, max_lum = c.w;
    float min_lum = view.curve[1].x;
    float e1 = clamp((e - lb) / (lw - lb), 0.0, 1.0);
    float e2 = e1;
    if (e1 > knee && knee < 1.0) {
        float t = (e1 - knee) / (1.0 - knee);
        float t2 = t * t, t3 = t2 * t;
        e2 = (2.0 * t3 - 3.0 * t2 + 1.0) * knee + (t3 - 2.0 * t2 + t) * (1.0 - knee)
            + (-2.0 * t3 + 3.0 * t2) * max_lum;
    }
    float e3 = e2 + min_lum * pow(1.0 - e2, 4.0);
    return e3 * (lw - lb) + lb;
}

// Brings a colour outside BT.709 inside it without giving up its
// saturation -- the idea of the Academy's reference gamut compression. Each
// component's distance from the largest (0 there, 1 at zero, past 1 when
// negative) is compressed smoothly from 0.9 on (tanh), so colours outside
// the gamut land just inside its edge, in order, instead of clipping onto
// it. A colour still brighter than white in some component after tone
// mapping (BT.709's red carries a fifth of white's luminance) is then
// scaled down whole. Two earlier versions pulled colours towards the grey
// of their own luminance instead, and turned bright BT.2020 reds pink and
// greens cyan.
static float3 compress_gamut(float3 rgb) {
    float high = max(max(rgb.r, rgb.g), rgb.b);
    if (high <= 0.0) return float3(0.0);
    float3 distance = (high - rgb) / high;
    const float knee = 0.9;
    float3 compressed = knee + (1.0 - knee) * tanh((distance - knee) / (1.0 - knee));
    distance = select(distance, compressed, distance > knee);
    rgb = high - distance * high;
    if (high > 1.0) rgb /= high;
    return clamp(rgb, 0.0, 1.0);
}

// A 4x4 Bayer matrix, as the fraction of a code step to add before
// quantising: banding in gradients traded for noise below the step.
static float dither(float2 position) {
    uint2 p = uint2(position) & 3;
    constexpr float bayer[16] = {0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5};
    return (bayer[p.y * 4 + p.x] + 0.5) / 16.0 - 0.5;
}

fragment float4 video_fragment(
    Varyings in [[stage_in]],
    constant Params &p [[buffer(0)]],
    constant DoviParams &dv [[buffer(1), function_constant(DOVI)]],
    const device ToneView &view [[buffer(2), function_constant(TONE_MAP)]],
    texture2d<float> plane0 [[texture(0)]],
    texture2d<float> plane1 [[texture(1)]],
    texture2d<float> plane2 [[texture(2)]],
    texture2d<float> overlay [[texture(3), function_constant(OVERLAY)]])
{
    float3 rgb = decode_linear(in.uv, p, dv, plane0, plane1, plane2);

    if (TONE_MAP) {
        // On the intensity of BT.2100 ICtCp, in PQ.
        float3 lms = rows(p.to_lms) * rgb;
        float3 ictcp = rows(p.to_ictcp) * pq_oetf(lms * (SDR_WHITE / 10000.0));
        float i_orig = ictcp.x;
        ictcp.x = eetf(view, i_orig);
        // BT.2390's chroma adjustment: scale by the smaller intensity ratio.
        if (i_orig > 0.0 && ictcp.x > 0.0) {
            ictcp.yz *= min(i_orig / ictcp.x, ictcp.x / i_orig);
        }
        lms = pq_eotf(rows(p.from_ictcp) * ictcp) * (10000.0 / SDR_WHITE);
        rgb = rows(p.from_lms) * lms;
    } else {
        rgb = rows(p.to_output) * rgb;
    }
    rgb = p.linear_scale.w > 0.0 ? compress_gamut(rgb) : clamp(rgb, 0.0, 1.0);

    // Encode for the sRGB display, black lifted as it was on the way in.
    float black = p.linear_scale.z;
    rgb = max((rgb - black) / (1.0 - black), 0.0);
    rgb = select(1.055 * pow(rgb, 1.0 / 2.4) - 0.055, rgb * 12.92, rgb <= 0.0031308);

    if (OVERLAY) {
        constexpr sampler smooth(filter::linear, address::clamp_to_edge);
        float4 sub = overlay.sample(smooth, in.uv);
        rgb = mix(rgb, sub.rgb, sub.a);
    }
    rgb += dither(in.position.xy) * p.sampling.w;
    return float4(rgb, 1.0);
}
