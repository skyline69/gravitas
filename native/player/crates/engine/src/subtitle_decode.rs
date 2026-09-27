//! Turning subtitle packets into events: the file's own tracks, and files
//! loaded beside it.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use ffmpeg_next::format::context::Input;
use ffmpeg_next::{Dictionary, Packet, Rational, codec, ffi, media};

use crate::error::{Error, Result};
use crate::open;
use crate::queue::Pop;
use crate::session::SessionShared;
use crate::subtitles::{self, BitmapEvent, BitmapFit, BitmapRect, Subtitles, TrackKey};

/// How long a text event lasts when neither the packet nor the decoder says.
const DEFAULT_TEXT_DURATION_S: f64 = 5.0;

/// A decoded AVSubtitle, freed when it goes -- ffmpeg-next's own wrapper
/// never frees its rects.
struct Decoded(ffmpeg_next::Subtitle);

impl Drop for Decoded {
    fn drop(&mut self) {
        // SAFETY: the subtitle was filled by avcodec_decode_subtitle2 (or is
        // still zeroed), which is what avsubtitle_free expects.
        unsafe { ffi::avsubtitle_free(self.0.as_mut_ptr()) };
    }
}

/// What kind of subtitle a codec produces.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Text { styled: bool },
    Bitmap(BitmapFit),
}

fn kind_of(id: ffi::AVCodecID) -> Option<Kind> {
    // SAFETY: avcodec_descriptor_get returns a static descriptor or null.
    let props = unsafe {
        let descriptor = ffi::avcodec_descriptor_get(id);
        if descriptor.is_null() {
            return None;
        }
        (*descriptor).props
    };
    if props & ffi::AV_CODEC_PROP_BITMAP_SUB != 0 {
        let fit = if id == ffi::AVCodecID::AV_CODEC_ID_HDMV_PGS_SUBTITLE {
            BitmapFit::Crop
        } else {
            BitmapFit::Stretch
        };
        Some(Kind::Bitmap(fit))
    } else if props & ffi::AV_CODEC_PROP_TEXT_SUB != 0 {
        let styled = matches!(
            id,
            ffi::AVCodecID::AV_CODEC_ID_ASS | ffi::AVCodecID::AV_CODEC_ID_SSA
        );
        Some(Kind::Text { styled })
    } else {
        None
    }
}

/// A decoder for one subtitle track.
pub(crate) struct SubtitleDecoder {
    key: TrackKey,
    decoder: codec::decoder::Subtitle,
    time_base: Rational,
    kind: Kind,
    /// The picture image subtitles are placed on until the decoder names its
    /// own: the video's size.
    video: (u32, u32),
}

impl std::fmt::Debug for SubtitleDecoder {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SubtitleDecoder")
            .field("key", &self.key)
            .field("kind", &self.kind)
            .finish_non_exhaustive()
    }
}

impl SubtitleDecoder {
    /// Opens a decoder for stream `index` of `input` and registers its track
    /// with `subtitles`. None for a codec FFmpeg cannot decode.
    pub(crate) fn open(
        input: &Input,
        index: usize,
        key: TrackKey,
        subtitles: &Subtitles,
        video: (u32, u32),
    ) -> Option<Self> {
        let stream = input.stream(index)?;
        let parameters = stream.parameters();
        let kind = kind_of(parameters.id().into())?;
        let decoder = match codec::Context::from_parameters(parameters)
            .and_then(|c| c.decoder().subtitle())
        {
            Ok(decoder) => decoder,
            Err(error) => {
                log::info!("no decoder for subtitle stream {index}: {error}");
                return None;
            }
        };
        let decoder = Self {
            key,
            decoder,
            time_base: stream.time_base(),
            kind,
            video,
        };
        decoder.register(subtitles);
        Some(decoder)
    }

    fn register(&self, subtitles: &Subtitles) {
        match self.kind {
            Kind::Text { styled } => {
                // SAFETY: an opened decoder context; the header it made (if
                // any) is subtitle_header_size bytes and lives as long as the
                // context.
                let header = unsafe {
                    let context = &*self.decoder.as_ptr();
                    subtitles::header_bytes(context.subtitle_header, context.subtitle_header_size)
                };
                subtitles.add_text_track(self.key, header, styled);
            }
            Kind::Bitmap(fit) => subtitles.add_bitmap_track(self.key, fit),
        }
    }

    /// The picture image subtitles are placed on. Read after every packet,
    /// never once at open: a PGS decoder learns its canvas from the first
    /// display set, and before it Matroska says nothing.
    fn canvas(&self) -> (u32, u32) {
        // SAFETY: an opened decoder context.
        let context = unsafe { &*self.decoder.as_ptr() };
        match (u32::try_from(context.width), u32::try_from(context.height)) {
            (Ok(width), Ok(height)) if width > 0 && height > 0 => (width, height),
            _ => self.video,
        }
    }

    /// Decodes one packet and adds what it held to `subtitles`.
    pub(crate) fn decode(&mut self, packet: &Packet, subtitles: &Subtitles) {
        let mut decoded = Decoded(ffmpeg_next::Subtitle::new());
        match self.decoder.decode(packet, &mut decoded.0) {
            Ok(true) => {}
            Ok(false) => return,
            Err(error) => {
                log::debug!("subtitle packet not decoded: {error}");
                return;
            }
        }
        let packet_start = packet.pts().map(|ts| ts as f64 * f64::from(self.time_base));
        let packet_duration =
            (packet.duration() > 0).then(|| packet.duration() as f64 * f64::from(self.time_base));
        // SAFETY: a subtitle the decoder just filled.
        let raw = unsafe { &*decoded.0.as_ptr() };
        let base = packet_start.unwrap_or_else(|| raw.pts as f64 / f64::from(ffi::AV_TIME_BASE));
        let start = base + f64::from(raw.start_display_time) / 1000.0;
        let end = match raw.end_display_time {
            0 | u32::MAX => None,
            end if end > raw.start_display_time => Some(base + f64::from(end) / 1000.0),
            _ => None,
        };
        match self.kind {
            Kind::Text { .. } => {
                let duration = packet_duration
                    .or_else(|| end.map(|end| end - start))
                    .unwrap_or(DEFAULT_TEXT_DURATION_S);
                for line in text_lines(raw) {
                    subtitles.add_text_event(self.key, &line, start, duration);
                }
            }
            Kind::Bitmap(_) => {
                let event = BitmapEvent {
                    start,
                    end: end.unwrap_or(f64::INFINITY),
                    canvas: self.canvas(),
                    rects: bitmap_rects(raw),
                };
                subtitles.add_bitmap_event(self.key, event);
            }
        }
    }
}

/// The ASS dialogue lines of a decoded text subtitle.
fn text_lines(raw: &ffi::AVSubtitle) -> Vec<String> {
    rects(raw)
        .filter_map(|rect| {
            // SAFETY: the rect's strings are NUL-terminated or null.
            unsafe {
                match rect.type_ {
                    ffi::AVSubtitleType::SUBTITLE_ASS => {
                        subtitles::c_text(rect.ass).map(str::to_owned)
                    }
                    ffi::AVSubtitleType::SUBTITLE_TEXT => subtitles::c_text(rect.text)
                        .map(|text| format!("0,0,Default,,0,0,0,,{}", text.replace('\n', "\\N"))),
                    _ => None,
                }
            }
        })
        .collect()
}

/// The palettised bitmaps of a decoded image subtitle, copied out.
fn bitmap_rects(raw: &ffi::AVSubtitle) -> Vec<BitmapRect> {
    rects(raw)
        .filter(|rect| rect.type_ == ffi::AVSubtitleType::SUBTITLE_BITMAP)
        .filter_map(|rect| {
            let width = usize::try_from(rect.w).ok().filter(|w| *w > 0)?;
            let height = usize::try_from(rect.h).ok().filter(|h| *h > 0)?;
            let line = usize::try_from(rect.linesize[0]).ok()?;
            let colors = usize::try_from(rect.nb_colors).ok()?.min(256);
            if rect.data[0].is_null() || rect.data[1].is_null() || line < width {
                return None;
            }
            let mut indices = Vec::with_capacity(width * height);
            for row in 0..height {
                // SAFETY: data[0] holds `height` rows of `linesize[0]` bytes.
                let source =
                    unsafe { std::slice::from_raw_parts(rect.data[0].add(row * line), width) };
                indices.extend_from_slice(source);
            }
            // SAFETY: data[1] holds the palette, nb_colors 32-bit entries;
            // read unaligned, since nothing promises its alignment.
            let palette: Vec<u32> = (0..colors)
                .map(|i| unsafe { rect.data[1].add(i * 4).cast::<u32>().read_unaligned() })
                .collect();
            Some(BitmapRect {
                x: rect.x,
                y: rect.y,
                width,
                height,
                indices,
                palette,
            })
        })
        .collect()
}

fn rects(raw: &ffi::AVSubtitle) -> impl Iterator<Item = &ffi::AVSubtitleRect> {
    let count = if raw.rects.is_null() {
        0
    } else {
        raw.num_rects as usize
    };
    (0..count).filter_map(move |i| {
        // SAFETY: `rects` holds `num_rects` pointers to valid rects.
        let rect = unsafe { *raw.rects.add(i) };
        // SAFETY: non-null rects are valid for the subtitle's lifetime.
        (!rect.is_null()).then(|| unsafe { &*rect })
    })
}

/// Decodes subtitle packets until the session stops.
pub(crate) fn run(
    mut decoders: HashMap<usize, SubtitleDecoder>,
    session: &Arc<SessionShared>,
    subtitles: &Subtitles,
) {
    while !session.stopping() {
        match session.subtitle_packets.pop(Duration::from_millis(50)) {
            None | Some(Pop::Eof(_)) => {}
            Some(Pop::Closed) => return,
            Some(Pop::Packet(packet, _)) => {
                if let Some(decoder) = decoders.get_mut(&packet.stream()) {
                    decoder.decode(&packet, subtitles);
                }
            }
        }
    }
}

/// Reads a whole subtitle file (an addon's SubRip or WebVTT) into a new
/// track `key`. Blocks while the file downloads.
pub(crate) fn load_file(
    url: &str,
    key: TrackKey,
    subtitles: &Subtitles,
    timeout: Duration,
) -> Result<()> {
    let mut options = Dictionary::new();
    options.set("rw_timeout", &timeout.as_micros().to_string());
    let mut input = open::open(url, options, || false, None)?;
    let index = input
        .streams()
        .find(|s| s.parameters().medium() == media::Type::Subtitle)
        .map(|s| s.index())
        .ok_or(Error::NothingToPlay)?;
    let mut decoder = SubtitleDecoder::open(&input, index, key, subtitles, (0, 0))
        .ok_or(Error::Ffmpeg(ffmpeg_next::Error::DecoderNotFound))?;
    let mut packet = Packet::empty();
    loop {
        match packet.read(&mut input) {
            Ok(()) if packet.stream() == index => decoder.decode(&packet, subtitles),
            Ok(()) => {}
            Err(ffmpeg_next::Error::Eof) => return Ok(()),
            Err(error) => return Err(error.into()),
        }
    }
}

/// Registers every font the file carries (Matroska attachments) with libass.
pub(crate) fn load_fonts(input: &Input, subtitles: &Subtitles) {
    let mut added = 0;
    for stream in input.streams() {
        if stream.parameters().medium() != media::Type::Attachment {
            continue;
        }
        let name = stream
            .metadata()
            .get("filename")
            .unwrap_or_default()
            .to_owned();
        let mime = stream
            .metadata()
            .get("mimetype")
            .unwrap_or_default()
            .to_ascii_lowercase();
        let lower = name.to_ascii_lowercase();
        let is_font = mime.contains("font")
            || [".ttf", ".otf", ".ttc"]
                .iter()
                .any(|ext| lower.ends_with(ext));
        if !is_font {
            continue;
        }
        // SAFETY: an attachment's data is its codec parameters' extradata.
        let data = unsafe {
            let parameters = &*stream.parameters().as_ptr();
            if parameters.extradata.is_null() || parameters.extradata_size <= 0 {
                continue;
            }
            std::slice::from_raw_parts(parameters.extradata, parameters.extradata_size as usize)
        };
        subtitles.add_font(&name, data);
        added += 1;
    }
    if added > 0 {
        subtitles.fonts_added();
        log::info!("{added} fonts from the file available to its subtitles");
    }
}
