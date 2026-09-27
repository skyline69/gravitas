//! The colour science behind the Metal renderer, on the CPU: what each
//! decoded frame's tags mean, turned into the handful of matrices and curve
//! parameters the fragment shader evaluates.
//!
//! Signals are decoded to R'G'B' with the frame's matrix and range,
//! linearised relative to SDR reference white (203 nits, BT.2408), converted
//! between primaries in linear light with Bradford adaptation, and -- for
//! HDR -- tone-mapped with BT.2390's EETF on the intensity of BT.2100 ICtCp,
//! then encoded for an sRGB display. Everything here comes from published
//! standards (BT.601/709/2020/2100, BT.1886, BT.2390, ST 2084, ARIB STD-B67,
//! IEC 61966-2-1, ETSI GS CCM 001 for Dolby Vision) or is this module's own;
//! SDR is the same arithmetic libplacebo does, so SDR pictures match the
//! Linux renderer's to a fraction of a code step, and HDR is close but not
//! identical (libplacebo's curve and gamut mapping are its own designs).

use ffmpeg_next::ffi;

/// SDR reference white, in nits: 1.0 in the shader's linear light.
pub(crate) const SDR_WHITE: f64 = 203.0;
/// The contrast assumed of an SDR display without better knowledge.
const SDR_CONTRAST: f64 = 1000.0;
/// "Absolute" black for HDR: not zero, which would break the curves.
const HDR_BLACK: f64 = 1e-6;
/// The peak of an HLG reference display.
const HLG_PEAK: f64 = 1000.0;

// ST 2084 (PQ).
const PQ_M1: f64 = 2610.0 / 4096.0 / 4.0;
const PQ_M2: f64 = 2523.0 / 4096.0 * 128.0;
const PQ_C1: f64 = 3424.0 / 4096.0;
const PQ_C2: f64 = 2413.0 / 4096.0 * 32.0;
const PQ_C3: f64 = 2392.0 / 4096.0 * 32.0;

pub(crate) type Mat3 = [[f64; 3]; 3];

const IDENTITY: Mat3 = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];

pub(crate) fn mul(a: &Mat3, b: &Mat3) -> Mat3 {
    let mut out = [[0.0; 3]; 3];
    for (i, row) in out.iter_mut().enumerate() {
        for (j, cell) in row.iter_mut().enumerate() {
            *cell = (0..3).map(|k| a[i][k] * b[k][j]).sum();
        }
    }
    out
}

pub(crate) fn apply(m: &Mat3, v: [f64; 3]) -> [f64; 3] {
    [0, 1, 2].map(|i| m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2])
}

/// The inverse, by cofactors (the matrices here are all well conditioned).
pub(crate) fn invert(m: &Mat3) -> Mat3 {
    let cofactor =
        |r0: usize, r1: usize, c0: usize, c1: usize| m[r0][c0] * m[r1][c1] - m[r0][c1] * m[r1][c0];
    let adjugate = [
        [
            cofactor(1, 2, 1, 2),
            -cofactor(0, 2, 1, 2),
            cofactor(0, 1, 1, 2),
        ],
        [
            -cofactor(1, 2, 0, 2),
            cofactor(0, 2, 0, 2),
            -cofactor(0, 1, 0, 2),
        ],
        [
            cofactor(1, 2, 0, 1),
            -cofactor(0, 2, 0, 1),
            cofactor(0, 1, 0, 1),
        ],
    ];
    let det: f64 = (0..3).map(|j| m[0][j] * adjugate[j][0]).sum();
    adjugate.map(|row| row.map(|v| v / det))
}

/// A CIE 1931 chromaticity.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Xy(f64, f64);

const D65: Xy = Xy(0.3127, 0.3290);
const ILLUMINANT_C: Xy = Xy(0.310, 0.316);
const DCI_WHITE: Xy = Xy(0.314, 0.351);

/// A set of RGB primaries and their white point.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Primaries {
    red: Xy,
    green: Xy,
    blue: Xy,
    white: Xy,
}

pub(crate) const BT709: Primaries = Primaries {
    red: Xy(0.64, 0.33),
    green: Xy(0.30, 0.60),
    blue: Xy(0.15, 0.06),
    white: D65,
};
pub(crate) const BT2020: Primaries = Primaries {
    red: Xy(0.708, 0.292),
    green: Xy(0.170, 0.797),
    blue: Xy(0.131, 0.046),
    white: D65,
};
const BT601_625: Primaries = Primaries {
    red: Xy(0.64, 0.33),
    green: Xy(0.29, 0.60),
    blue: Xy(0.15, 0.06),
    white: D65,
};
const BT601_525: Primaries = Primaries {
    red: Xy(0.630, 0.340),
    green: Xy(0.310, 0.595),
    blue: Xy(0.155, 0.070),
    white: D65,
};
const BT470M: Primaries = Primaries {
    red: Xy(0.67, 0.33),
    green: Xy(0.21, 0.71),
    blue: Xy(0.14, 0.08),
    white: ILLUMINANT_C,
};
const FILM_C: Primaries = Primaries {
    red: Xy(0.681, 0.319),
    green: Xy(0.243, 0.692),
    blue: Xy(0.145, 0.049),
    white: ILLUMINANT_C,
};
const DCI_P3: Primaries = Primaries {
    red: Xy(0.680, 0.320),
    green: Xy(0.265, 0.690),
    blue: Xy(0.150, 0.060),
    white: DCI_WHITE,
};
const DISPLAY_P3: Primaries = Primaries {
    white: D65,
    ..DCI_P3
};
const EBU_3213: Primaries = Primaries {
    red: Xy(0.630, 0.340),
    green: Xy(0.295, 0.605),
    blue: Xy(0.155, 0.077),
    white: D65,
};
const CIE_1931: Primaries = Primaries {
    red: Xy(1.0, 0.0),
    green: Xy(0.0, 1.0),
    blue: Xy(0.0, 0.0),
    white: Xy(1.0 / 3.0, 1.0 / 3.0),
};

fn xyz(c: Xy) -> [f64; 3] {
    [c.0 / c.1, 1.0, (1.0 - c.0 - c.1) / c.1]
}

/// RGB (these primaries) to CIE XYZ, white at Y = 1.
fn rgb_to_xyz(p: &Primaries) -> Mat3 {
    let columns = [xyz(p.red), xyz(p.green), xyz(p.blue)];
    let unscaled = [0, 1, 2].map(|i| columns.map(|column| column[i]));
    // Each primary's weight, so that R = G = B = 1 is the white point.
    let weights = apply(&invert(&unscaled), xyz(p.white));
    unscaled.map(|row| {
        [
            row[0] * weights[0],
            row[1] * weights[1],
            row[2] * weights[2],
        ]
    })
}

/// Bradford's cone response (Lam, 1985).
const BRADFORD: Mat3 = [
    [0.8951, 0.2664, -0.1614],
    [-0.7502, 1.7135, 0.0367],
    [0.0389, -0.0685, 1.0296],
];

/// Bradford chromatic adaptation from one white to another, in XYZ.
fn adaptation(from: Xy, to: Xy) -> Mat3 {
    if from == to {
        return IDENTITY;
    }
    let (a, b) = (apply(&BRADFORD, xyz(from)), apply(&BRADFORD, xyz(to)));
    let scale = [
        [b[0] / a[0], 0.0, 0.0],
        [0.0, b[1] / a[1], 0.0],
        [0.0, 0.0, b[2] / a[2]],
    ];
    mul(&invert(&BRADFORD), &mul(&scale, &BRADFORD))
}

/// Linear RGB in `from` primaries to linear RGB in `to`, the white point
/// adapted (relative colorimetric).
pub(crate) fn rgb_to_rgb(from: &Primaries, to: &Primaries) -> Mat3 {
    if from == to {
        return IDENTITY;
    }
    let xyz = mul(&adaptation(from.white, to.white), &rgb_to_xyz(from));
    mul(&invert(&rgb_to_xyz(to)), &xyz)
}

/// Linear BT.2020 RGB to BT.2100's LMS (ICtCp's cone space).
const BT2100_LMS: Mat3 = [
    [1688.0 / 4096.0, 2146.0 / 4096.0, 262.0 / 4096.0],
    [683.0 / 4096.0, 2951.0 / 4096.0, 462.0 / 4096.0],
    [99.0 / 4096.0, 309.0 / 4096.0, 3688.0 / 4096.0],
];

/// PQ-encoded L'M'S' to ICtCp (BT.2100).
pub(crate) const LMS_TO_ICTCP: Mat3 = [
    [0.5, 0.5, 0.0],
    [6610.0 / 4096.0, -13613.0 / 4096.0, 7003.0 / 4096.0],
    [17933.0 / 4096.0, -17390.0 / 4096.0, -543.0 / 4096.0],
];

/// Linear RGB in `p` to BT.2100 LMS.
fn rgb_to_lms(p: &Primaries) -> Mat3 {
    mul(&BT2100_LMS, &rgb_to_rgb(p, &BT2020))
}

/// Dolby Vision's reshaped signal ends in LMS cone space; the RPU's own
/// matrix takes it to Hunt-Pointer-Estevez cones (normalised to D65, as in
/// Ebner & Fairchild's IPT), and this takes those to linear BT.2020 RGB.
pub(crate) fn dovi_lms_to_rgb() -> Mat3 {
    const HPE: Mat3 = [
        [0.4002, 0.7076, -0.0808],
        [-0.2263, 1.1653, 0.0457],
        [0.0, 0.0, 0.9182],
    ];
    invert(&mul(&HPE, &rgb_to_xyz(&BT2020)))
}

/// Nits to PQ's [0, 1] signal.
pub(crate) fn pq_from_nits(nits: f64) -> f64 {
    let y = (nits / 10000.0).max(0.0).powf(PQ_M1);
    ((PQ_C1 + PQ_C2 * y) / (1.0 + PQ_C3 * y)).powf(PQ_M2)
}

/// PQ's [0, 1] signal to nits.
#[cfg(test)]
pub(crate) fn nits_from_pq(pq: f64) -> f64 {
    let e = pq.max(0.0).powf(1.0 / PQ_M2);
    10000.0 * ((e - PQ_C1).max(0.0) / (PQ_C2 - PQ_C3 * e)).powf(1.0 / PQ_M1)
}

/// How the shader samples the frame's planes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum Layout {
    /// One texture per component (Y, Cb, Cr -- or G, B, R).
    Planar,
    /// Luma, then interleaved chroma (NV12, P010).
    SemiPlanar,
    /// Every component in one texture (RGB(A), in the texture's order).
    Packed,
}

/// The source's transfer function, as the shader linearises it.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[repr(u32)]
pub(crate) enum Transfer {
    Bt1886 = 1,
    Srgb = 2,
    /// A pure power law; the exponent is a parameter.
    Gamma = 3,
    Pq = 4,
    Hlg = 5,
    Linear = 6,
}

impl Transfer {
    fn from_av(trc: ffi::AVColorTransferCharacteristic) -> Self {
        use ffi::AVColorTransferCharacteristic as T;
        match trc {
            T::AVCOL_TRC_IEC61966_2_1 => Self::Srgb,
            T::AVCOL_TRC_GAMMA22 | T::AVCOL_TRC_GAMMA28 => Self::Gamma,
            T::AVCOL_TRC_LINEAR => Self::Linear,
            T::AVCOL_TRC_SMPTE2084 => Self::Pq,
            T::AVCOL_TRC_ARIB_STD_B67 => Self::Hlg,
            // BT.709, SMPTE 170M/240M, BT.2020 10/12-bit and the rest name
            // an OETF; what the display does with them is BT.1886.
            _ => Self::Bt1886,
        }
    }

    fn is_hdr(self) -> bool {
        matches!(self, Self::Pq | Self::Hlg)
    }
}

/// What a frame says about its colour, as read off the `AVFrame`.
#[derive(Clone, Copy, Debug)]
pub(crate) struct FrameColour {
    pub(crate) width: u32,
    pub(crate) height: u32,
    pub(crate) space: ffi::AVColorSpace,
    pub(crate) range: ffi::AVColorRange,
    pub(crate) primaries: ffi::AVColorPrimaries,
    pub(crate) transfer: ffi::AVColorTransferCharacteristic,
    pub(crate) chroma_location: ffi::AVChromaLocation,
    /// Content bits per component (10 for P010 and yuv420p10).
    pub(crate) depth: u32,
    /// Bits the texture stores each component in (8 or 16).
    pub(crate) texture_bits: u32,
    /// How far up the stored word the content sits (6 for P010).
    pub(crate) shift: u32,
    /// Chroma subsampling, as log2 (1, 1 for 4:2:0).
    pub(crate) chroma_shift: (u32, u32),
    /// The planes hold R, G, B rather than Y, Cb, Cr. Textures are bound in
    /// component order whatever the plane order (GBRP's R is plane 2), so
    /// the decode is then the identity.
    pub(crate) rgb: bool,
}

/// Static HDR metadata (`frame_colour.h`).
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct HdrMetadata {
    pub(crate) max_luma: f32,
    pub(crate) min_luma: f32,
    pub(crate) max_cll: f32,
    pub(crate) max_fall: f32,
}

/// One Dolby Vision reshaping curve (`frame_colour.h`).
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct DoviCurve {
    pub(crate) num_pivots: i32,
    pub(crate) pivots: [f32; 9],
    pub(crate) method: [i32; 8],
    pub(crate) poly: [[f32; 3]; 8],
    pub(crate) mmr_order: [i32; 8],
    pub(crate) mmr_constant: [f32; 8],
    pub(crate) mmr: [[[f32; 7]; 3]; 8],
}

/// A frame's Dolby Vision metadata (`frame_colour.h`).
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct Dovi {
    pub(crate) nonlinear: [f32; 9],
    pub(crate) nonlinear_offset: [f32; 3],
    pub(crate) linear: [f32; 9],
    pub(crate) source_min_pq: f32,
    pub(crate) source_max_pq: f32,
    pub(crate) l1_avg_pq: f32,
    pub(crate) l1_max_pq: f32,
    pub(crate) bl_bit_depth: i32,
    pub(crate) comp: [DoviCurve; 3],
}

unsafe extern "C" {
    fn gv_frame_hdr_metadata(frame: *const ffi::AVFrame, out: *mut HdrMetadata) -> i32;
    fn gv_frame_dovi(frame: *const ffi::AVFrame, out: *mut Dovi) -> i32;
}

/// Attaches `dovi` to `frame` as FFmpeg's parsed Dolby Vision metadata.
#[cfg(test)]
pub(crate) fn set_dovi(frame: &mut ffmpeg_next::frame::Video, dovi: &Dovi) -> bool {
    unsafe extern "C" {
        fn gv_frame_set_dovi(frame: *mut ffi::AVFrame, dovi: *const Dovi) -> i32;
    }
    // SAFETY: a valid frame; `dovi` is the struct the C side reads.
    unsafe { gv_frame_set_dovi(frame.as_mut_ptr(), dovi) != 0 }
}

/// The frame's static HDR metadata, if it carries any.
pub(crate) fn hdr_metadata(frame: &ffmpeg_next::frame::Video) -> Option<HdrMetadata> {
    let mut out = HdrMetadata::default();
    // SAFETY: a valid frame; `out` is the struct the C side fills.
    (unsafe { gv_frame_hdr_metadata(frame.as_ptr(), &raw mut out) } != 0).then_some(out)
}

/// The frame's Dolby Vision metadata, if it carries metadata the renderer
/// applies.
pub(crate) fn dovi(frame: &ffmpeg_next::frame::Video) -> Option<Box<Dovi>> {
    let mut out = Box::<Dovi>::default();
    // SAFETY: a valid frame; `out` is the struct the C side fills.
    (unsafe { gv_frame_dovi(frame.as_ptr(), &raw mut *out) } != 0).then_some(out)
}

/// What HDR is tone-mapped between, in PQ signal units. The source's peak
/// is measured on the GPU from each frame (see `shaders.metal`), except
/// where Dolby Vision's level 1 metadata states it per scene.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct ToneTarget {
    pub(crate) input_min: f64,
    pub(crate) output_min: f64,
    pub(crate) output_max: f64,
    /// The scene's peak from dynamic metadata.
    pub(crate) metadata: Option<f64>,
}

/// Everything the shader needs for one frame's colour, and which variant of
/// it to run.
#[derive(Clone, Debug)]
pub(crate) struct Plan {
    /// Normalises a texture sample to the content's own code range.
    pub(crate) sample_scale: f64,
    /// Signal (after `sample_scale`, and reshaping for Dolby Vision) to
    /// R'G'B': `rgb = decode * signal + decode_offset`.
    pub(crate) decode: Mat3,
    pub(crate) decode_offset: [f64; 3],
    /// Where to sample chroma relative to luma, in chroma texels.
    pub(crate) chroma_offset: (f64, f64),
    pub(crate) transfer: Transfer,
    /// Transfer parameters: BT.1886's (a, b), the gamma exponent, or HLG's
    /// (1 - b, b, peak, system gamma - 1).
    pub(crate) transfer_params: [f64; 4],
    /// The source's luma weights (HLG's OOTF, and brightness measurement).
    pub(crate) luma: [f64; 3],
    /// SDR curves are black-lifted: `linear = scale * x + black`.
    pub(crate) linear_scale: (f64, f64),
    /// Linear source RGB to linear BT.709 when no tone mapping runs.
    pub(crate) to_output: Mat3,
    /// With tone mapping: source RGB to BT.2100 LMS, and LMS to output RGB.
    pub(crate) to_lms: Mat3,
    pub(crate) from_lms: Mat3,
    /// Colours may fall outside BT.709 (wider source primaries, or HDR):
    /// compress them in rather than clip.
    pub(crate) compress_gamut: bool,
    pub(crate) tone: Option<ToneTarget>,
    /// Dolby Vision reshaping and its LMS step, when the frame has an RPU.
    pub(crate) dovi: Option<Box<Dovi>>,
    /// The Dolby Vision LMS matrix (fixed LMS->RGB times the RPU's).
    pub(crate) dovi_linear: Mat3,
    /// Output black, in the output's linear light (SDR display contrast).
    pub(crate) output_black: f64,
}

/// A 3x3 matrix given row-major as nine floats.
fn matrix9(m: &[f32; 9]) -> Mat3 {
    [0, 1, 2].map(|i| [0, 1, 2].map(|j| f64::from(m[i * 3 + j])))
}

impl Plan {
    /// The plan for `frame`, rendered for an SDR sRGB display.
    pub(crate) fn new(
        frame: &FrameColour,
        hdr: Option<HdrMetadata>,
        dovi: Option<Box<Dovi>>,
    ) -> Self {
        let depth = frame.depth.clamp(1, 16);
        let code_max = f64::from((1u32 << depth) - 1);
        let texture_max = f64::from((1u32 << frame.texture_bits) - 1);
        let sample_scale = texture_max / (f64::from(1u32 << frame.shift) * code_max);

        let mut space = frame.space;
        if space == ffi::AVColorSpace::AVCOL_SPC_UNSPECIFIED
            || space == ffi::AVColorSpace::AVCOL_SPC_RESERVED
        {
            space = if frame.width >= 1280 || frame.height > 576 {
                ffi::AVColorSpace::AVCOL_SPC_BT709
            } else {
                ffi::AVColorSpace::AVCOL_SPC_SMPTE170M
            };
        }
        let mut primaries = primaries_from_av(frame.primaries, frame.height);
        let mut transfer = Transfer::from_av(frame.transfer);
        let decode;
        let decode_offset;
        let dovi_linear;
        if let Some(dv) = dovi.as_deref() {
            // The RPU's matrix carries its own levels; only its offsets are
            // applied, scaled like libplacebo's black point.
            let scale = f64::from(1u32 << depth) / code_max;
            decode = matrix9(&dv.nonlinear);
            let black = dv.nonlinear_offset.map(|o| f64::from(o) * scale);
            decode_offset = apply(&decode, black).map(|v| -v);
            dovi_linear = mul(&dovi_lms_to_rgb(), &matrix9(&dv.linear));
            primaries = BT2020;
            transfer = Transfer::Pq;
        } else {
            (decode, decode_offset) = ycbcr_decode(frame, space, depth);
            dovi_linear = IDENTITY;
        }

        let chroma_offset = chroma_offset(frame);

        // The source's luminance range, in nits.
        let (src_min, src_max) = source_luminance(transfer, hdr);
        let output_black = 1.0 / SDR_CONTRAST;

        let mut transfer_params = [0.0; 4];
        let mut linear_scale = (1.0, 0.0);
        let luma = rgb_to_xyz(&primaries)[1];
        match transfer {
            Transfer::Bt1886 => {
                // SDR black lifted to the display's contrast (1000:1).
                let lb = output_black.powf(1.0 / 2.4);
                let lw = 1.0_f64;
                transfer_params[0] = (lw - lb).powf(2.4);
                transfer_params[1] = lb / (lw - lb);
            }
            Transfer::Srgb | Transfer::Linear => {
                linear_scale = (1.0 - output_black, output_black);
            }
            Transfer::Gamma => {
                transfer_params[0] =
                    if frame.transfer == ffi::AVColorTransferCharacteristic::AVCOL_TRC_GAMMA28 {
                        2.8
                    } else {
                        2.2
                    };
                linear_scale = (1.0 - output_black, output_black);
            }
            Transfer::Pq => {}
            Transfer::Hlg => {
                let peak = src_max / SDR_WHITE;
                let reference = HLG_PEAK / SDR_WHITE;
                let gamma = 1.2 * 1.111_f64.powf((peak / reference).log2());
                let b = (3.0 * (src_min / src_max).powf(1.0 / gamma)).sqrt();
                transfer_params = [1.0 - b, b, peak, gamma - 1.0];
            }
        }

        let tone = transfer.is_hdr().then(|| ToneTarget {
            input_min: pq_from_nits(src_min),
            output_min: pq_from_nits(SDR_WHITE * output_black),
            output_max: pq_from_nits(SDR_WHITE),
            metadata: dovi
                .as_deref()
                .filter(|dv| dv.l1_max_pq > 0.0)
                .map(|dv| f64::from(dv.l1_max_pq)),
        });
        let compress_gamut = tone.is_some() || primaries != BT709;
        Self {
            sample_scale,
            decode,
            decode_offset,
            chroma_offset,
            transfer,
            transfer_params,
            luma,
            linear_scale,
            to_output: rgb_to_rgb(&primaries, &BT709),
            to_lms: rgb_to_lms(&primaries),
            from_lms: invert(&rgb_to_lms(&BT709)),
            compress_gamut,
            tone,
            dovi,
            dovi_linear,
            output_black,
        }
    }
}

fn primaries_from_av(primaries: ffi::AVColorPrimaries, height: u32) -> Primaries {
    use ffi::AVColorPrimaries as P;
    match primaries {
        P::AVCOL_PRI_BT709 => BT709,
        P::AVCOL_PRI_BT470M => BT470M,
        P::AVCOL_PRI_BT470BG => BT601_625,
        P::AVCOL_PRI_SMPTE170M | P::AVCOL_PRI_SMPTE240M => BT601_525,
        P::AVCOL_PRI_FILM => FILM_C,
        P::AVCOL_PRI_BT2020 => BT2020,
        P::AVCOL_PRI_SMPTE428 => CIE_1931,
        P::AVCOL_PRI_SMPTE431 => DCI_P3,
        P::AVCOL_PRI_SMPTE432 => DISPLAY_P3,
        P::AVCOL_PRI_JEDEC_P22 => EBU_3213,
        // Untagged: what the resolution suggests.
        _ if height == 576 => BT601_625,
        _ if height == 480 || height == 486 => BT601_525,
        _ => BT709,
    }
}

/// The YCbCr decoding transform for `space`, with the frame's range folded
/// in: from the normalised signal to R'G'B'.
pub(crate) fn ycbcr_decode(
    frame: &FrameColour,
    space: ffi::AVColorSpace,
    depth: u32,
) -> (Mat3, [f64; 3]) {
    use ffi::AVColorSpace as S;
    let luma = |kr: f64, kb: f64| -> Mat3 {
        let kg = 1.0 - kr - kb;
        [
            [1.0, 0.0, 2.0 * (1.0 - kr)],
            [
                1.0,
                -2.0 * kb * (1.0 - kb) / kg,
                -2.0 * kr * (1.0 - kr) / kg,
            ],
            [1.0, 2.0 * (1.0 - kb), 0.0],
        ]
    };
    let rgb = frame.rgb;
    let m = match space {
        _ if rgb => IDENTITY,
        S::AVCOL_SPC_RGB => IDENTITY,
        S::AVCOL_SPC_BT470BG | S::AVCOL_SPC_SMPTE170M => luma(0.2990, 0.1140),
        S::AVCOL_SPC_SMPTE240M => luma(0.2122, 0.0865),
        S::AVCOL_SPC_BT2020_NCL | S::AVCOL_SPC_BT2020_CL => luma(0.2627, 0.0593),
        S::AVCOL_SPC_YCGCO => [[1.0, -1.0, 1.0], [1.0, 1.0, 0.0], [1.0, -1.0, -1.0]],
        S::AVCOL_SPC_FCC => luma(0.30, 0.11),
        _ => luma(0.2126, 0.0722),
    };
    let ycbcr = !rgb && space != S::AVCOL_SPC_RGB;
    let full = frame.range == ffi::AVColorRange::AVCOL_RANGE_JPEG
        || (frame.range != ffi::AVColorRange::AVCOL_RANGE_MPEG && !ycbcr);
    let scale = f64::from(1u32 << depth) / f64::from((1u32 << depth) - 1);
    let (ymin, ymax, cmid, cmax) = if full {
        (0.0, 1.0, 128.0 / 256.0 * scale, 1.0)
    } else {
        (
            16.0 / 256.0 * scale,
            235.0 / 256.0 * scale,
            128.0 / 256.0 * scale,
            240.0 / 256.0 * scale,
        )
    };
    let ymul = 1.0 / (ymax - ymin);
    let cmul = 0.5 / (cmax - cmid);
    let (mul, black) = if ycbcr {
        ([ymul, cmul, cmul], [ymin, cmid, cmid])
    } else {
        ([ymul; 3], [ymin; 3])
    };
    let mut out = m;
    let mut offset = [0.0; 3];
    for i in 0..3 {
        for j in 0..3 {
            out[i][j] *= mul[j];
            offset[i] -= out[i][j] * black[j];
        }
    }
    (out, offset)
}

/// Where chroma sits relative to luma, in chroma texels, for each
/// subsampled axis: FFmpeg's chroma location, left (co-sited horizontally,
/// centred vertically) when unknown, as MPEG-2 onwards default to.
fn chroma_offset(frame: &FrameColour) -> (f64, f64) {
    use ffi::AVChromaLocation as L;
    // Chroma sample position within a 2x2 luma block, in luma pixels.
    let (x, y) = match frame.chroma_location {
        L::AVCHROMA_LOC_CENTER => (0.5, 0.5),
        L::AVCHROMA_LOC_TOPLEFT => (0.0, 0.0),
        L::AVCHROMA_LOC_TOP => (0.5, 0.0),
        L::AVCHROMA_LOC_BOTTOMLEFT => (0.0, 1.0),
        L::AVCHROMA_LOC_BOTTOM => (0.5, 1.0),
        _ => (0.0, 0.5),
    };
    // A luma pixel's centre maps onto chroma texels at (x_luma - p) / 2; the
    // difference from plain proportional sampling is (1/4 - p/2) texels.
    let axis = |shift: u32, p: f64| if shift == 1 { 0.25 - p / 2.0 } else { 0.0 };
    (axis(frame.chroma_shift.0, x), axis(frame.chroma_shift.1, y))
}

/// The source's (black, peak) in nits: what the transfer's own curve is
/// scaled by. The peak the tone curve starts from is measured instead.
fn source_luminance(transfer: Transfer, hdr: Option<HdrMetadata>) -> (f64, f64) {
    match transfer {
        Transfer::Pq => {
            let peak = hdr
                .map(|h| f64::from(h.max_luma))
                .filter(|&p| p > 0.0)
                .unwrap_or(10000.0);
            (HDR_BLACK, peak.clamp(HDR_BLACK, 10000.0))
        }
        // HLG's curve is scaled by the display's peak: the mastering
        // display's when stated, else a reference display's.
        Transfer::Hlg => (
            HDR_BLACK,
            hdr.map(|h| f64::from(h.max_luma))
                .filter(|&p| p > 0.0)
                .unwrap_or(HLG_PEAK),
        ),
        _ => (SDR_WHITE / SDR_CONTRAST, SDR_WHITE),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64, tolerance: f64) -> bool {
        (a - b).abs() <= tolerance
    }

    #[test]
    fn pq_round_trips_and_hits_its_anchors() {
        assert!(close(pq_from_nits(10000.0), 1.0, 1e-9));
        assert!(close(pq_from_nits(100.0), 0.508, 1e-3));
        for nits in [0.01, 1.0, 203.0, 1000.0, 4000.0] {
            assert!(close(nits_from_pq(pq_from_nits(nits)), nits, nits * 1e-9));
        }
    }

    #[test]
    fn bt709_to_xyz_matches_the_standard() {
        let m = rgb_to_xyz(&BT709);
        // Y row: the luma coefficients.
        assert!(close(m[1][0], 0.2126, 1e-4));
        assert!(close(m[1][1], 0.7152, 1e-4));
        assert!(close(m[1][2], 0.0722, 1e-4));
    }

    #[test]
    fn bt2020_to_bt709_keeps_white_and_matches_bt2087() {
        let m = rgb_to_rgb(&BT2020, &BT709);
        let white = apply(&m, [1.0, 1.0, 1.0]);
        assert!(white.iter().all(|&v| close(v, 1.0, 1e-9)));
        // BT.2087's matrix, first row.
        assert!(close(m[0][0], 1.6605, 1e-3));
        assert!(close(m[0][1], -0.5876, 1e-3));
        assert!(close(m[0][2], -0.0728, 1e-3));
    }

    fn frame(depth: u32, range: ffi::AVColorRange) -> FrameColour {
        FrameColour {
            width: 1920,
            height: 1080,
            space: ffi::AVColorSpace::AVCOL_SPC_BT709,
            range,
            primaries: ffi::AVColorPrimaries::AVCOL_PRI_BT709,
            transfer: ffi::AVColorTransferCharacteristic::AVCOL_TRC_BT709,
            chroma_location: ffi::AVChromaLocation::AVCHROMA_LOC_LEFT,
            depth,
            texture_bits: if depth > 8 { 16 } else { 8 },
            shift: 0,
            chroma_shift: (1, 1),
            rgb: false,
        }
    }

    fn decode(plan: &Plan, codes: [f64; 3], depth: u32) -> [f64; 3] {
        let max = f64::from((1u32 << depth) - 1);
        let signal = codes.map(|c| c / max);
        let rgb = apply(&plan.decode, signal);
        [0, 1, 2].map(|i| rgb[i] + plan.decode_offset[i])
    }

    #[test]
    fn limited_range_black_and_white_land_on_zero_and_one() {
        for depth in [8, 10] {
            let plan = Plan::new(
                &frame(depth, ffi::AVColorRange::AVCOL_RANGE_MPEG),
                None,
                None,
            );
            let step = f64::from(1u32 << (depth - 8));
            let white = decode(&plan, [235.0 * step, 128.0 * step, 128.0 * step], depth);
            let black = decode(&plan, [16.0 * step, 128.0 * step, 128.0 * step], depth);
            assert!(white.iter().all(|&v| close(v, 1.0, 1e-9)), "{white:?}");
            assert!(black.iter().all(|&v| close(v, 0.0, 1e-9)), "{black:?}");
        }
    }

    #[test]
    fn bt709_red_decodes_to_red() {
        // Pure red, BT.709 limited range, 8-bit: Y 63, Cb 102, Cr 240.
        let plan = Plan::new(&frame(8, ffi::AVColorRange::AVCOL_RANGE_MPEG), None, None);
        let red = decode(&plan, [63.0, 102.0, 240.0], 8);
        assert!(close(red[0], 1.0, 0.01), "{red:?}");
        assert!(close(red[1], 0.0, 0.01), "{red:?}");
        assert!(close(red[2], 0.0, 0.01), "{red:?}");
    }

    #[test]
    fn msb_aligned_samples_are_rescaled() {
        // P010: ten bits at the top of a sixteen-bit word.
        let mut p010 = frame(10, ffi::AVColorRange::AVCOL_RANGE_MPEG);
        p010.shift = 6;
        let plan = Plan::new(&p010, None, None);
        let stored = f64::from(940u32 << 6) / 65535.0;
        assert!(close(stored * plan.sample_scale, 940.0 / 1023.0, 1e-9));
        // yuv420p10: the same code at the bottom of the word.
        let plan = Plan::new(&frame(10, ffi::AVColorRange::AVCOL_RANGE_MPEG), None, None);
        assert!(close(
            940.0 / 65535.0 * plan.sample_scale,
            940.0 / 1023.0,
            1e-9
        ));
    }

    #[test]
    fn left_sited_chroma_is_shifted_a_quarter_texel() {
        let plan = Plan::new(&frame(8, ffi::AVColorRange::AVCOL_RANGE_MPEG), None, None);
        assert_eq!(plan.chroma_offset, (0.25, 0.0));
    }

    #[test]
    fn sdr_needs_no_tone_mapping_and_hdr10_does() {
        let sdr = Plan::new(&frame(8, ffi::AVColorRange::AVCOL_RANGE_MPEG), None, None);
        assert!(sdr.tone.is_none());
        let mut hdr = frame(10, ffi::AVColorRange::AVCOL_RANGE_MPEG);
        hdr.transfer = ffi::AVColorTransferCharacteristic::AVCOL_TRC_SMPTE2084;
        hdr.primaries = ffi::AVColorPrimaries::AVCOL_PRI_BT2020;
        hdr.space = ffi::AVColorSpace::AVCOL_SPC_BT2020_NCL;
        let tone = Plan::new(&hdr, None, None)
            .tone
            .expect("HDR10 is tone-mapped");
        // Onto SDR white, from a measured peak: no metadata to go on.
        assert!(close(tone.output_max, pq_from_nits(SDR_WHITE), 1e-12));
        assert!(tone.metadata.is_none());
    }
}

#[cfg(test)]
mod dovi_tests {
    use super::*;

    #[test]
    fn the_dolby_vision_lms_matrix_follows_from_hpe_and_bt2020() {
        // Dolby's published LMS -> BT.2020 matrix, first row.
        let m = dovi_lms_to_rgb();
        assert!((m[0][0] - 3.0644).abs() < 1e-3, "{m:?}");
        assert!((m[0][1] + 2.1660).abs() < 1e-3, "{m:?}");
        assert!((m[0][2] - 0.1016).abs() < 1e-3, "{m:?}");
    }

    #[test]
    fn ictcp_of_white_is_achromatic() {
        let lms = apply(&rgb_to_lms(&BT709), [1.0, 1.0, 1.0]);
        let pq = lms.map(|v| pq_from_nits(v * SDR_WHITE));
        let ictcp = apply(&LMS_TO_ICTCP, pq);
        assert!(ictcp[1].abs() < 1e-3 && ictcp[2].abs() < 1e-3, "{ictcp:?}");
    }
}
