//! The tracks of an opened file, and which of them play.

use ffmpeg_next::format::context::Input;
use ffmpeg_next::format::stream::Disposition;
use ffmpeg_next::media;

/// What a track carries.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum TrackKind {
    Video,
    Audio,
    Subtitle,
}

impl TrackKind {
    /// The name mpv's `track-list` uses, which the embedder's labelling
    /// already understands.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            TrackKind::Video => "video",
            TrackKind::Audio => "audio",
            TrackKind::Subtitle => "sub",
        }
    }
}

/// One track of the opened file.
///
/// `id` counts from 1 within its kind, in stream order -- the numbering mpv
/// uses, so an id means the same track whichever engine is playing.
#[derive(Clone, Debug, PartialEq)]
#[allow(
    clippy::struct_excessive_bools,
    reason = "each flag is a separate fact the file states about the track"
)]
pub struct Track {
    pub kind: TrackKind,
    pub id: u32,
    pub stream_index: usize,
    pub language: Option<String>,
    pub title: Option<String>,
    pub codec: String,
    pub default: bool,
    pub forced: bool,
    pub hearing_impaired: bool,
    pub channels: Option<u16>,
    pub sample_rate: Option<u32>,
    pub width: Option<u32>,
    pub height: Option<u32>,
    /// Loaded from a separate file (an addon's subtitles), not the stream.
    pub external: bool,
    /// The Dolby Vision profile the stream declares (5, 7, 8...), if any.
    pub dolby_vision_profile: Option<u8>,
    /// FFmpeg's name for the codec profile ("Dolby TrueHD + Dolby Atmos",
    /// "DTS-HD MA + DTS:X", "Main 10"), as mpv's `codec-profile` reports it.
    pub codec_profile: Option<String>,
    /// The video's transfer when it is HDR: "pq" or "hlg", mpv's names.
    pub transfer: Option<&'static str>,
}

/// Every audio, video and subtitle track of `input`. Cover art (a video
/// stream holding one attached picture) is not a video track.
#[must_use]
pub fn collect(input: &Input) -> Vec<Track> {
    let mut next_id = [1u32; 3];
    let mut tracks = Vec::new();
    for stream in input.streams() {
        let parameters = stream.parameters();
        let kind = match parameters.medium() {
            media::Type::Video if !stream.disposition().contains(Disposition::ATTACHED_PIC) => {
                TrackKind::Video
            }
            media::Type::Audio => TrackKind::Audio,
            media::Type::Subtitle => TrackKind::Subtitle,
            _ => continue,
        };
        let slot = kind as usize;
        let id = next_id[slot];
        next_id[slot] += 1;
        let metadata = stream.metadata();
        let text = |key: &str| {
            metadata
                .get(key)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
        };
        // SAFETY: `parameters` wraps the stream's live AVCodecParameters,
        // which outlive this borrow of `input`.
        let raw = unsafe { &*parameters.as_ptr() };
        let positive = |value: i32| u32::try_from(value).ok().filter(|v| *v > 0);
        let disposition = stream.disposition();
        tracks.push(Track {
            kind,
            id,
            stream_index: stream.index(),
            // "und" is a tag that says there is no tag; mpv drops it too.
            language: text("language").filter(|l| !l.eq_ignore_ascii_case("und")),
            title: text("title"),
            codec: parameters.id().name().to_owned(),
            default: disposition.contains(Disposition::DEFAULT),
            forced: disposition.contains(Disposition::FORCED),
            hearing_impaired: disposition.contains(Disposition::HEARING_IMPAIRED),
            channels: (kind == TrackKind::Audio)
                .then(|| u16::try_from(raw.ch_layout.nb_channels).ok())
                .flatten(),
            sample_rate: (kind == TrackKind::Audio)
                .then(|| positive(raw.sample_rate))
                .flatten(),
            width: (kind == TrackKind::Video)
                .then(|| positive(raw.width))
                .flatten(),
            height: (kind == TrackKind::Video)
                .then(|| positive(raw.height))
                .flatten(),
            external: false,
            dolby_vision_profile: (kind == TrackKind::Video)
                .then(|| dolby_vision_profile(raw))
                .flatten(),
            codec_profile: profile_name(raw.codec_id, raw.profile),
            transfer: (kind == TrackKind::Video)
                .then(|| hdr_transfer(raw.color_trc))
                .flatten(),
        });
    }
    tracks
}

/// FFmpeg's name for a codec profile, when it has one. A stream's header
/// often leaves the profile unknown -- the engine opens files without
/// FFmpeg's full probe -- and the decoder fills it in from the bitstream:
/// Dolby Atmos on E-AC-3 and TrueHD, DTS:X (see `decode.rs`).
pub(crate) fn profile_name(codec: ffmpeg_next::ffi::AVCodecID, profile: i32) -> Option<String> {
    // SAFETY: returns a static string or null for any id and profile.
    let name = unsafe { ffmpeg_next::ffi::avcodec_profile_name(codec, profile) };
    if name.is_null() {
        return None;
    }
    // SAFETY: a static NUL-terminated string.
    let name = unsafe { std::ffi::CStr::from_ptr(name) };
    Some(name.to_string_lossy().into_owned())
}

/// The transfer an HDR stream is tagged with, in mpv's names.
fn hdr_transfer(transfer: ffmpeg_next::ffi::AVColorTransferCharacteristic) -> Option<&'static str> {
    use ffmpeg_next::ffi::AVColorTransferCharacteristic as T;
    match transfer {
        T::AVCOL_TRC_SMPTE2084 => Some("pq"),
        T::AVCOL_TRC_ARIB_STD_B67 => Some("hlg"),
        _ => None,
    }
}

/// The profile from a stream's Dolby Vision configuration record, which
/// demuxers export as coded side data. Byte 2 of the record is `dv_profile`
/// (after the major and minor version bytes).
fn dolby_vision_profile(parameters: &ffmpeg_next::ffi::AVCodecParameters) -> Option<u8> {
    use ffmpeg_next::ffi::AVPacketSideDataType::AV_PKT_DATA_DOVI_CONF;
    let count = usize::try_from(parameters.nb_coded_side_data).ok()?;
    if parameters.coded_side_data.is_null() {
        return None;
    }
    // SAFETY: coded_side_data holds nb_coded_side_data entries, each with
    // `size` readable bytes at `data`.
    let entries = unsafe { std::slice::from_raw_parts(parameters.coded_side_data, count) };
    let record = entries.iter().find(|e| e.type_ == AV_PKT_DATA_DOVI_CONF)?;
    if record.data.is_null() || record.size < 3 {
        return None;
    }
    // SAFETY: at least three bytes, checked above.
    Some(unsafe { *record.data.add(2) })
}

/// The track of `kind` to play, given the languages asked for in order of
/// preference.
///
/// Audio and video always get a track when the file has one: a language
/// match first, then the track the file marks as default, then the first.
/// Subtitles are shown only when asked for -- a language match, preferring a
/// full track over a forced-only one -- because a subtitle nobody asked for
/// is not a default anyone wants.
#[must_use]
pub fn choose<'a>(tracks: &'a [Track], kind: TrackKind, languages: &[String]) -> Option<&'a Track> {
    let of_kind: Vec<&Track> = tracks.iter().filter(|t| t.kind == kind).collect();
    for wanted in languages {
        let mut matching = of_kind.iter().copied().filter(|t| {
            t.language
                .as_deref()
                .is_some_and(|language| language.eq_ignore_ascii_case(wanted))
        });
        let found = if kind == TrackKind::Subtitle {
            let all: Vec<&Track> = matching.collect();
            all.iter()
                .copied()
                .find(|t| !t.forced)
                .or_else(|| all.first().copied())
        } else {
            matching.next()
        };
        if found.is_some() {
            return found;
        }
    }
    if kind == TrackKind::Subtitle {
        return None;
    }
    of_kind
        .iter()
        .copied()
        .find(|t| t.default)
        .or_else(|| of_kind.first().copied())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track(kind: TrackKind, id: u32, language: &str) -> Track {
        Track {
            kind,
            id,
            stream_index: id as usize,
            language: (!language.is_empty()).then(|| language.to_owned()),
            title: None,
            codec: "test".to_owned(),
            default: false,
            forced: false,
            hearing_impaired: false,
            channels: None,
            sample_rate: None,
            width: None,
            height: None,
            external: false,
            dolby_vision_profile: None,
            codec_profile: None,
            transfer: None,
        }
    }

    fn langs(values: &[&str]) -> Vec<String> {
        values.iter().map(|v| (*v).to_owned()).collect()
    }

    #[test]
    fn audio_follows_the_first_preferred_language_present() {
        let tracks = [
            track(TrackKind::Audio, 1, "rus"),
            track(TrackKind::Audio, 2, "eng"),
            track(TrackKind::Audio, 3, "ger"),
        ];
        let chosen = choose(&tracks, TrackKind::Audio, &langs(&["de", "ger", "eng"]));
        assert_eq!(chosen.map(|t| t.id), Some(3));
    }

    #[test]
    fn audio_without_a_match_takes_the_default_then_the_first() {
        let mut tracks = [
            track(TrackKind::Audio, 1, "rus"),
            track(TrackKind::Audio, 2, "ukr"),
        ];
        assert_eq!(
            choose(&tracks, TrackKind::Audio, &langs(&["eng"])).map(|t| t.id),
            Some(1)
        );
        tracks[1].default = true;
        assert_eq!(
            choose(&tracks, TrackKind::Audio, &langs(&["eng"])).map(|t| t.id),
            Some(2)
        );
    }

    #[test]
    fn subtitles_play_only_when_asked_for_and_prefer_a_full_track() {
        let mut tracks = [
            track(TrackKind::Subtitle, 1, "eng"),
            track(TrackKind::Subtitle, 2, "eng"),
        ];
        tracks[0].forced = true;
        assert_eq!(choose(&tracks, TrackKind::Subtitle, &[]), None);
        assert_eq!(choose(&tracks, TrackKind::Subtitle, &langs(&["fre"])), None);
        let chosen = choose(&tracks, TrackKind::Subtitle, &langs(&["eng"]));
        assert_eq!(chosen.map(|t| t.id), Some(2));
    }

    #[test]
    fn languages_match_regardless_of_case() {
        let tracks = [track(TrackKind::Audio, 1, "ENG")];
        assert!(choose(&tracks, TrackKind::Audio, &langs(&["eng"])).is_some());
    }
}
