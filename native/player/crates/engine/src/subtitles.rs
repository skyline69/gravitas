//! Subtitles: every track's events, and drawing the selected one onto a frame.
//!
//! Text subtitles (SubRip, ASS/SSA, WebVTT, ...) reach libass as ASS events,
//! which is what FFmpeg's decoders turn every text format into; libass lays
//! them out and hands back alpha bitmaps. Image subtitles (PGS, VobSub, DVB)
//! are kept as FFmpeg decodes them -- palette indices and a palette, a quarter
//! of the memory of RGBA -- and scaled onto the frame when shown.
//!
//! Every subtitle track of the file is decoded as it is read, not just the
//! selected one: their packets are tiny, and it makes switching between them
//! instant rather than a re-read of the stream.
//!
//! libass objects are not thread-safe, so everything lives behind one mutex.
//! The decoder thread adds events under it and the render thread draws under
//! it; neither holds it for longer than one packet or one frame.

use std::collections::HashSet;
use std::ffi::{CStr, CString, c_char, c_int};
use std::ptr;

use libass_sys as ass;
use parking_lot::{Mutex, MutexGuard};

use crate::render::RenderTarget;

/// How a subtitle track is identified: by its stream in the file, or by the
/// id it was given when loaded from a separate file.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum TrackKey {
    Stream(usize),
    External(u32),
}

/// The viewer's subtitle preferences, on mpv's scale: sizes are pixels at a
/// 720-line picture, so the same setting looks the same whichever engine
/// plays. Applied to text subtitles that carry no styling of their own.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SubtitleStyle {
    pub font_size: f64,
    /// 0xRRGGBB.
    pub color: u32,
    pub border_size: f64,
    /// 0-100: how opaque the box behind the text is; 0 draws no box.
    pub back_opacity: f64,
    pub bold: bool,
}

impl Default for SubtitleStyle {
    fn default() -> Self {
        Self {
            font_size: 55.0,
            color: 0x00FF_FFFF,
            border_size: 3.0,
            back_opacity: 0.0,
            bold: false,
        }
    }
}

/// mpv's default distance of subtitles from the bottom, at 720 lines.
const MARGIN_V: f64 = 22.0;
/// Image-subtitle events kept per track. A PGS event is a full-width bitmap,
/// so the queue is bounded; the read-ahead never needs more than this.
const MAX_BITMAP_EVENTS: usize = 96;
/// Image-subtitle events that ended this long before now are dropped.
const BITMAP_KEEP_BEHIND_S: f64 = 10.0;

/// One decoded image subtitle: shown from `start` to `end` (seconds).
#[derive(Debug, Clone)]
pub(crate) struct BitmapEvent {
    pub(crate) start: f64,
    pub(crate) end: f64,
    /// The picture the rects are placed on, as the decoder knew it when it
    /// decoded this event.
    pub(crate) canvas: (u32, u32),
    pub(crate) rects: Vec<BitmapRect>,
}

/// How an image track's canvas maps onto the picture.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BitmapFit {
    /// Canvas corner to picture corner. DVD and DVB canvases are the video's
    /// own storage size (or a broadcast's display definition), so their
    /// pixels have the video's shape, and the stretch is what gives it them.
    Stretch,
    /// Square pixels, the picture cut out of the middle of the canvas, and
    /// each event moved inside the picture. Blu-ray subtitles are authored on
    /// a full 1920x1080 canvas, and a release that crops a scope film's bars
    /// away (1920x800) keeps them: stretched, the lines were squashed, and
    /// placed one to one, the lower ones fell off the bottom of the picture.
    Crop,
}

/// A palettised bitmap placed on the subtitle's canvas.
#[derive(Clone)]
pub(crate) struct BitmapRect {
    pub(crate) x: i32,
    pub(crate) y: i32,
    pub(crate) width: usize,
    pub(crate) height: usize,
    /// One palette index per pixel, `width` per row.
    pub(crate) indices: Vec<u8>,
    /// 0xAARRGGBB, as FFmpeg's decoders produce it.
    pub(crate) palette: Vec<u32>,
}

impl std::fmt::Debug for BitmapRect {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("BitmapRect")
            .field("x", &self.x)
            .field("y", &self.y)
            .field("width", &self.width)
            .field("height", &self.height)
            .finish_non_exhaustive()
    }
}

/// What one subtitle track holds.
enum Content {
    /// A libass track, and the events already in it (FFmpeg's decoders number
    /// events afresh after a seek, so libass cannot tell a re-read from new).
    Text {
        track: *mut ass::ASS_Track,
        seen: HashSet<(i64, i64, u64)>,
        /// The file styled it itself (ASS/SSA); the viewer's style is not
        /// forced onto it, as mpv does not by default.
        styled: bool,
    },
    Bitmap {
        fit: BitmapFit,
        events: Vec<BitmapEvent>,
    },
}

struct Track {
    key: TrackKey,
    content: Content,
    /// Distinct events received so far: how much the track has said.
    events: usize,
}

struct State {
    library: *mut ass::ASS_Library,
    renderer: *mut ass::ASS_Renderer,
    tracks: Vec<Track>,
    selected: Option<TrackKey>,
    visible: bool,
    delay: f64,
    style: SubtitleStyle,
    /// Something changed that the picture on screen does not show yet.
    dirty: bool,
    /// The image-subtitle event last drawn, to tell when it changes.
    last_bitmap: Option<(TrackKey, f64)>,
}

// SAFETY: the libass pointers are only ever used while the mutex around the
// State is held, so no two threads touch them at once; libass keeps no
// thread-local state.
unsafe impl Send for State {}

/// All subtitle state of a player.
pub(crate) struct Subtitles {
    state: Mutex<State>,
}

impl std::fmt::Debug for Subtitles {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let state = self.state.lock();
        f.debug_struct("Subtitles")
            .field("tracks", &state.tracks.len())
            .field("selected", &state.selected)
            .field("visible", &state.visible)
            .finish_non_exhaustive()
    }
}

impl Subtitles {
    /// Starts libass. None when it cannot (then no text subtitle is drawn,
    /// and nothing else changes).
    pub(crate) fn new() -> Option<Self> {
        // SAFETY: plain constructors; each result is checked before use and
        // freed in Drop.
        unsafe {
            let library = ass::ass_library_init();
            if library.is_null() {
                return None;
            }
            // libass prints to stdout unless told otherwise.
            ass::ass_set_message_cb(library, message_callback(), ptr::null_mut());
            let renderer = ass::ass_renderer_init(library);
            if renderer.is_null() {
                ass::ass_library_done(library);
                return None;
            }
            let state = State {
                library,
                renderer,
                tracks: Vec::new(),
                selected: None,
                visible: true,
                delay: 0.0,
                style: SubtitleStyle::default(),
                dirty: false,
                last_bitmap: None,
            };
            state.configure_fonts();
            Some(Self {
                state: Mutex::new(state),
            })
        }
    }

    /// A new file: every track goes, and so does the timing correction.
    pub(crate) fn reset(&self) {
        let mut state = self.state.lock();
        state.clear_tracks();
        state.selected = None;
        state.delay = 0.0;
        state.dirty = true;
    }

    /// A font the file carries (a Matroska attachment), for its ASS styles.
    pub(crate) fn add_font(&self, name: &str, data: &[u8]) {
        let Ok(name) = CString::new(name) else {
            return;
        };
        let Ok(size) = c_int::try_from(data.len()) else {
            return;
        };
        let state = self.state.lock();
        // SAFETY: libass copies the font data before returning.
        unsafe {
            ass::ass_add_font(
                state.library,
                name.as_ptr(),
                data.as_ptr().cast::<c_char>(),
                size,
            );
        }
    }

    /// Makes fonts added since the last call available to the renderer.
    pub(crate) fn fonts_added(&self) {
        self.state.lock().configure_fonts();
    }

    /// A text track, set up from its decoder's ASS header.
    pub(crate) fn add_text_track(&self, key: TrackKey, header: &[u8], styled: bool) {
        let mut state = self.state.lock();
        // SAFETY: the library is valid; the header is copied by libass.
        let track = unsafe {
            let track = ass::ass_new_track(state.library);
            if track.is_null() {
                return;
            }
            if !header.is_empty() {
                ass::ass_process_codec_private(
                    track,
                    header.as_ptr().cast::<c_char>().cast_mut(),
                    header.len() as c_int,
                );
            }
            track
        };
        state.remove(key);
        state.tracks.push(Track {
            key,
            events: 0,
            content: Content::Text {
                track,
                seen: HashSet::new(),
                styled,
            },
        });
    }

    /// An image track whose canvas maps onto the picture by `fit`.
    pub(crate) fn add_bitmap_track(&self, key: TrackKey, fit: BitmapFit) {
        let mut state = self.state.lock();
        state.remove(key);
        state.tracks.push(Track {
            key,
            events: 0,
            content: Content::Bitmap {
                fit,
                events: Vec::new(),
            },
        });
    }

    /// One text event: an ASS dialogue line without its "Dialogue:" prefix
    /// ("ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,Effect,Text").
    pub(crate) fn add_text_event(&self, key: TrackKey, line: &str, start: f64, duration: f64) {
        let mut state = self.state.lock();
        let selected = state.selected == Some(key);
        let Some(entry) = state.tracks.iter_mut().find(|t| t.key == key) else {
            return;
        };
        let Content::Text { track, seen, .. } = &mut entry.content else {
            return;
        };
        let start_ms = (start * 1000.0).round() as i64;
        let duration_ms = (duration * 1000.0).round().max(1.0) as i64;
        if !seen.insert((start_ms, duration_ms, text_hash(dialogue_text(line)))) {
            return;
        }
        entry.events += 1;
        let track = *track;
        // SAFETY: the track belongs to this State; libass copies the line.
        unsafe {
            ass::ass_process_chunk(
                track,
                line.as_ptr().cast::<c_char>(),
                line.len() as c_int,
                start_ms,
                duration_ms,
            );
        }
        if selected {
            state.dirty = true;
        }
    }

    /// One image event. An event with no rects clears the one before it, and
    /// an event without an end lasts until the next one starts.
    pub(crate) fn add_bitmap_event(&self, key: TrackKey, event: BitmapEvent) {
        let mut state = self.state.lock();
        let Some(entry) = state.tracks.iter_mut().find(|t| t.key == key) else {
            return;
        };
        let Content::Bitmap { events, .. } = &mut entry.content else {
            return;
        };
        if let Some(previous) = events
            .iter_mut()
            .rev()
            .find(|e| e.start < event.start)
            .filter(|e| e.end > event.start)
        {
            previous.end = event.start;
        }
        if event.rects.is_empty() {
            return;
        }
        match events.iter().position(|e| e.start >= event.start) {
            Some(i) if (events[i].start - event.start).abs() < 1e-3 => events[i] = event,
            Some(i) => {
                events.insert(i, event);
                entry.events += 1;
            }
            None => {
                events.push(event);
                entry.events += 1;
            }
        }
        if events.len() > MAX_BITMAP_EVENTS {
            events.remove(0);
        }
    }

    /// How many distinct events each track has received so far.
    pub(crate) fn event_counts(&self) -> Vec<(TrackKey, usize)> {
        let state = self.state.lock();
        state.tracks.iter().map(|t| (t.key, t.events)).collect()
    }

    pub(crate) fn select(&self, key: Option<TrackKey>) {
        let mut state = self.state.lock();
        if state.selected != key {
            state.selected = key;
            state.last_bitmap = None;
            state.dirty = true;
        }
    }

    pub(crate) fn set_visible(&self, visible: bool) {
        let mut state = self.state.lock();
        if state.visible != visible {
            state.visible = visible;
            state.dirty = true;
        }
    }

    pub(crate) fn is_visible(&self) -> bool {
        self.state.lock().visible
    }

    pub(crate) fn set_delay(&self, delay: f64) {
        let mut state = self.state.lock();
        state.delay = delay;
        state.dirty = true;
    }

    pub(crate) fn delay(&self) -> f64 {
        self.state.lock().delay
    }

    pub(crate) fn set_style(&self, style: SubtitleStyle) {
        let mut state = self.state.lock();
        state.style = style;
        state.dirty = true;
    }

    /// Whether the picture needs redrawing for the subtitles alone -- a
    /// selection, delay or style change while nothing else moves.
    pub(crate) fn is_dirty(&self) -> bool {
        self.state.lock().dirty
    }

    /// What the selected track shows at media time `now` on a picture of
    /// `width` x `height` whose source is `video` pixels. Holds the lock
    /// until dropped; blend it after the frame is scaled.
    pub(crate) fn overlay(
        &self,
        now: f64,
        width: u32,
        height: u32,
        video: (u32, u32),
    ) -> Overlay<'_> {
        let mut state = self.state.lock();
        let forced = std::mem::take(&mut state.dirty);
        let now = now - state.delay;
        let pending = if state.visible {
            state.prepare(now, width, height, video)
        } else {
            Pending::Nothing { changed: false }
        };
        let changed = forced || pending.changed();
        Overlay {
            state,
            pending,
            changed,
        }
    }

    /// Drops image events long past, keeping memory bounded when nobody
    /// renders.
    pub(crate) fn prune(&self, now: f64) {
        let mut state = self.state.lock();
        for track in &mut state.tracks {
            if let Content::Bitmap { events, .. } = &mut track.content {
                events.retain(|e| e.end >= now - BITMAP_KEEP_BEHIND_S);
            }
        }
    }
}

impl Drop for Subtitles {
    fn drop(&mut self) {
        let state = self.state.get_mut();
        state.clear_tracks();
        // SAFETY: nothing else references the renderer or library now.
        unsafe {
            ass::ass_renderer_done(state.renderer);
            ass::ass_library_done(state.library);
        }
    }
}

impl State {
    fn configure_fonts(&self) {
        let family = c"sans-serif";
        // SAFETY: valid renderer; libass copies the strings.
        unsafe {
            ass::ass_set_fonts(
                self.renderer,
                ptr::null(),
                family.as_ptr(),
                ass::ASS_FONTPROVIDER_AUTODETECT as c_int,
                ptr::null(),
                1,
            );
        }
    }

    fn remove(&mut self, key: TrackKey) {
        if let Some(index) = self.tracks.iter().position(|t| t.key == key) {
            let track = self.tracks.remove(index);
            free(&track);
        }
    }

    fn clear_tracks(&mut self) {
        for track in self.tracks.drain(..) {
            free(&track);
        }
        self.last_bitmap = None;
    }

    fn prepare(&mut self, now: f64, width: u32, height: u32, video: (u32, u32)) -> Pending {
        let Some(key) = self.selected else {
            return Pending::Nothing { changed: false };
        };
        let renderer = self.renderer;
        let style = self.style;
        let Some(track) = self.tracks.iter().find(|t| t.key == key) else {
            return Pending::Nothing { changed: false };
        };
        match &track.content {
            Content::Text { track, styled, .. } => {
                let track = *track;
                // SAFETY: renderer and track are valid and only used under
                // the lock; the returned image list stays valid until the
                // next ass_render_frame on this renderer, which cannot
                // happen while the Overlay holds the lock.
                unsafe {
                    ass::ass_set_frame_size(renderer, width as c_int, height as c_int);
                    if video.0 > 0 && video.1 > 0 {
                        ass::ass_set_storage_size(renderer, video.0 as c_int, video.1 as c_int);
                    }
                    apply_style(renderer, track, (!*styled).then_some(style));
                    let mut change: c_int = 0;
                    let images = ass::ass_render_frame(
                        renderer,
                        track,
                        (now * 1000.0).round() as i64,
                        &raw mut change,
                    );
                    Pending::Text {
                        images,
                        changed: change != 0,
                    }
                }
            }
            Content::Bitmap { fit, events } => {
                let active = events.iter().position(|e| e.start <= now && now < e.end);
                let identity = active.map(|i| (key, events[i].start));
                let changed = identity != self.last_bitmap;
                self.last_bitmap = identity;
                match active {
                    Some(index) => Pending::Bitmap {
                        key,
                        index,
                        fit: *fit,
                        changed,
                    },
                    None => Pending::Nothing { changed },
                }
            }
        }
    }
}

/// libass's message levels: 0 fatal, 1 error, 2 warning; the rest is detail
/// (font selection on every render, glyph fallbacks) that belongs nowhere.
const ASS_LEVEL_WARNING: c_int = 2;

/// Receives libass's messages. Only the format string reaches the log --
/// expanding its `va_list` is not possible from stable Rust -- which names
/// the problem well enough to act on.
unsafe extern "C" fn log_message(
    level: c_int,
    format: *const c_char,
    _args: *mut std::ffi::c_void,
    _data: *mut std::ffi::c_void,
) {
    if level > ASS_LEVEL_WARNING {
        return;
    }
    // SAFETY: libass passes a valid format string.
    if let Some(text) = unsafe { c_text(format) } {
        log::warn!("libass: {}", text.trim_end());
    }
}

/// `log_message` as the callback type the bindings declare for this
/// platform. Their `va_list` parameter is a pointer (or an array that decays
/// to one) on every platform libass builds for, and the callback never reads
/// it.
fn message_callback() -> ass::ass_sys_message_cb {
    type Declared =
        unsafe extern "C" fn(c_int, *const c_char, *mut std::ffi::c_void, *mut std::ffi::c_void);
    let callback: Declared = log_message;
    // SAFETY: both signatures take four register-sized arguments in the same
    // order and return nothing; only the pointee type of the ignored third
    // argument differs.
    unsafe { std::mem::transmute::<Option<Declared>, ass::ass_sys_message_cb>(Some(callback)) }
}

fn free(track: &Track) {
    if let Content::Text { track, .. } = &track.content {
        // SAFETY: the track was created by ass_new_track and is referenced
        // nowhere else once removed from the State.
        unsafe { ass::ass_free_track(*track) };
    }
}

/// The viewer's style for a track without its own, or none.
///
/// # Safety
/// `renderer` and `track` must be valid libass objects.
unsafe fn apply_style(
    renderer: *mut ass::ASS_Renderer,
    track: *mut ass::ASS_Track,
    style: Option<SubtitleStyle>,
) {
    let Some(style) = style else {
        // SAFETY: valid renderer, per the caller.
        unsafe {
            ass::ass_set_selective_style_override_enabled(
                renderer,
                ass::ASS_OVERRIDE_DEFAULT as c_int,
            );
        }
        return;
    };
    // mpv's sizes are pixels at 720 lines; the track's own script resolution
    // is what libass measures styles in.
    // SAFETY: valid track, per the caller.
    let play_res_y = f64::from(unsafe { (*track).PlayResY }.max(1));
    let scale = play_res_y / 720.0;
    let boxed = style.back_opacity > 0.0;
    let box_alpha = (255.0 - style.back_opacity.clamp(0.0, 100.0) * 2.55).round() as u32;
    let name = c"Default";
    // Arial, as FFmpeg's own subtitle header names it: fontconfig maps it to
    // a metric-compatible Latin font everywhere. A bare "sans-serif" can
    // resolve to a script-specific face first (Noto Sans Arabic here), which
    // has digits but no Latin letters, and a line comes out in two fonts.
    let font = c"Arial";
    let mut ass_style = ass::ASS_Style {
        Name: name.as_ptr().cast_mut(),
        FontName: font.as_ptr().cast_mut(),
        FontSize: style.font_size * scale,
        PrimaryColour: (style.color & 0x00FF_FFFF) << 8,
        SecondaryColour: 0x00FF_FF00,
        OutlineColour: if boxed { box_alpha } else { 0 },
        BackColour: 0x0000_0080,
        Bold: if style.bold { -1 } else { 0 },
        ScaleX: 1.0,
        ScaleY: 1.0,
        // Box: libass draws BorderStyle 3 as an opaque box in the outline
        // colour, padded by the outline width.
        BorderStyle: if boxed { 3 } else { 1 },
        Outline: style.border_size.max(if boxed { 1.0 } else { 0.0 }) * scale,
        Alignment: 2,
        MarginL: (25.0 * scale) as c_int,
        MarginR: (25.0 * scale) as c_int,
        MarginV: (MARGIN_V * scale) as c_int,
        Encoding: 1,
        ..ass::ASS_Style::default()
    };
    // SAFETY: libass copies the style (and its strings) before returning.
    unsafe {
        ass::ass_set_selective_style_override(renderer, &raw mut ass_style);
        ass::ass_set_selective_style_override_enabled(
            renderer,
            (ass::ASS_OVERRIDE_BIT_STYLE | ass::ASS_OVERRIDE_BIT_SELECTIVE_FONT_SCALE) as c_int,
        );
    }
}

enum Pending {
    Nothing {
        changed: bool,
    },
    Text {
        images: *mut ass::ASS_Image,
        changed: bool,
    },
    Bitmap {
        key: TrackKey,
        index: usize,
        fit: BitmapFit,
        changed: bool,
    },
}

impl Pending {
    fn changed(&self) -> bool {
        match self {
            Pending::Nothing { changed }
            | Pending::Text { changed, .. }
            | Pending::Bitmap { changed, .. } => *changed,
        }
    }
}

/// The subtitles due now, ready to blend onto a frame.
pub(crate) struct Overlay<'a> {
    state: MutexGuard<'a, State>,
    pending: Pending,
    changed: bool,
}

impl std::fmt::Debug for Overlay<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Overlay")
            .field("changed", &self.changed)
            .finish_non_exhaustive()
    }
}

impl Overlay<'_> {
    /// Whether what the subtitles show differs from the last frame drawn.
    pub(crate) fn changed(&self) -> bool {
        self.changed
    }

    /// Draws the subtitles onto `target`, which holds the scaled frame.
    pub(crate) fn blend(&self, target: &mut RenderTarget<'_>) {
        self.compose(target, blend_pixel);
    }

    /// Whether anything is on screen right now.
    pub(crate) fn has_content(&self) -> bool {
        match &self.pending {
            Pending::Nothing { .. } => false,
            Pending::Text { images, .. } => !images.is_null(),
            Pending::Bitmap { .. } => true,
        }
    }

    /// Draws the subtitles alone onto a transparent picture: straight-alpha
    /// RGBA for the GPU renderer to lay over the frame.
    pub(crate) fn rasterize(&self, target: &mut RenderTarget<'_>) {
        target.pixels.fill(0);
        self.compose(target, over_pixel);
    }

    fn compose(&self, target: &mut RenderTarget<'_>, blend: Blend) {
        match &self.pending {
            Pending::Nothing { .. } => {}
            Pending::Text { images, .. } => {
                let mut image = *images;
                while !image.is_null() {
                    // SAFETY: the list is libass's and stays valid while the
                    // lock (held by self) prevents another render.
                    let current = unsafe { &*image };
                    blend_ass_image(current, target, blend);
                    image = current.next;
                }
            }
            Pending::Bitmap {
                key, index, fit, ..
            } => {
                let Some(Track {
                    content: Content::Bitmap { events, .. },
                    ..
                }) = self.state.tracks.iter().find(|t| t.key == *key)
                else {
                    return;
                };
                let Some(event) = events.get(*index) else {
                    return;
                };
                if let Some(placement) = Placement::of(event, *fit, (target.width, target.height)) {
                    for rect in &event.rects {
                        blend_bitmap(rect, placement, target, blend);
                    }
                }
            }
        }
    }
}

/// One libass bitmap: an alpha mask in one colour (0xRRGGBBAA, where AA is
/// transparency).
fn blend_ass_image(image: &ass::ASS_Image, target: &mut RenderTarget<'_>, blend: Blend) {
    let (w, h) = (image.w.max(0) as usize, image.h.max(0) as usize);
    if w == 0 || h == 0 || image.bitmap.is_null() {
        return;
    }
    let color = image.color;
    let opacity = 255 - (color & 0xFF);
    let rgb = [(color >> 24) as u8, (color >> 16) as u8, (color >> 8) as u8];
    let stride = image.stride.max(0) as usize;
    for row in 0..h {
        let y = image.dst_y + row as i32;
        if y < 0 || y >= target.height as i32 {
            continue;
        }
        // SAFETY: libass guarantees `stride * (h - 1) + w` bytes.
        let mask = unsafe { std::slice::from_raw_parts(image.bitmap.add(row * stride), w) };
        let line = y as usize * target.stride;
        for (column, &coverage) in mask.iter().enumerate() {
            if coverage == 0 {
                continue;
            }
            let x = image.dst_x + column as i32;
            if x < 0 || x >= target.width as i32 {
                continue;
            }
            let alpha = u32::from(coverage) * opacity / 255;
            blend(&mut target.pixels[line + x as usize * 4..], rgb, alpha);
        }
    }
}

/// Where an image event's canvas lands on the target: a canvas point
/// `(x, y)` is drawn at `(x * sx + dx, y * sy + dy)`.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Placement {
    sx: f64,
    sy: f64,
    dx: f64,
    dy: f64,
}

impl Placement {
    /// None when there is nothing to place.
    fn of(event: &BitmapEvent, fit: BitmapFit, target: (u32, u32)) -> Option<Self> {
        let (target_w, target_h) = (f64::from(target.0), f64::from(target.1));
        // The extent of the rects, which a canvas too small to hold them
        // (none known yet, or a decoder that got it wrong) is grown to, as
        // mpv does.
        let (mut left, mut top, mut right, mut bottom) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
        for rect in &event.rects {
            left = left.min(f64::from(rect.x));
            top = top.min(f64::from(rect.y));
            right = right.max(f64::from(rect.x) + rect.width as f64);
            bottom = bottom.max(f64::from(rect.y) + rect.height as f64);
        }
        if left > right || target_w <= 0.0 || target_h <= 0.0 {
            return None;
        }
        let canvas_w = f64::from(event.canvas.0).max(right);
        let canvas_h = f64::from(event.canvas.1).max(bottom);
        if canvas_w <= 0.0 || canvas_h <= 0.0 {
            return None;
        }
        match fit {
            BitmapFit::Stretch => Some(Self {
                sx: target_w / canvas_w,
                sy: target_h / canvas_h,
                dx: 0.0,
                dy: 0.0,
            }),
            BitmapFit::Crop => {
                let scale = (target_w / canvas_w).max(target_h / canvas_h);
                let dx = (target_w - canvas_w * scale) / 2.0;
                let dy = (target_h - canvas_h * scale) / 2.0;
                Some(Self {
                    sx: scale,
                    sy: scale,
                    dx: dx + inside(left * scale + dx, right * scale + dx, target_w),
                    dy: dy + inside(top * scale + dy, bottom * scale + dy, target_h),
                })
            }
        }
    }
}

/// How far to move the span `start..end` to bring it inside `0..size`: the
/// least that does, or centred when it cannot fit.
fn inside(start: f64, end: f64, size: f64) -> f64 {
    if end - start >= size {
        (size - start - end) / 2.0
    } else if start < 0.0 {
        -start
    } else if end > size {
        size - end
    } else {
        0.0
    }
}

/// One palettised image-subtitle rect, placed on the target.
fn blend_bitmap(
    rect: &BitmapRect,
    placement: Placement,
    target: &mut RenderTarget<'_>,
    blend: Blend,
) {
    let Placement { sx, sy, dx, dy } = placement;
    if rect.width == 0 || rect.height == 0 || sx <= 0.0 || sy <= 0.0 {
        return;
    }
    let left = (f64::from(rect.x) * sx + dx).floor() as i64;
    let top = (f64::from(rect.y) * sy + dy).floor() as i64;
    let right = ((f64::from(rect.x) + rect.width as f64) * sx + dx).ceil() as i64;
    let bottom = ((f64::from(rect.y) + rect.height as f64) * sy + dy).ceil() as i64;
    for ty in top.max(0)..bottom.min(i64::from(target.height)) {
        let src_y = (((ty as f64 + 0.5 - dy) / sy) - f64::from(rect.y)).floor() as i64;
        if src_y < 0 || src_y as usize >= rect.height {
            continue;
        }
        let line = ty as usize * target.stride;
        for tx in left.max(0)..right.min(i64::from(target.width)) {
            let src_x = (((tx as f64 + 0.5 - dx) / sx) - f64::from(rect.x)).floor() as i64;
            if src_x < 0 || src_x as usize >= rect.width {
                continue;
            }
            let index = rect.indices[src_y as usize * rect.width + src_x as usize];
            let Some(&argb) = rect.palette.get(usize::from(index)) else {
                continue;
            };
            let alpha = argb >> 24;
            if alpha == 0 {
                continue;
            }
            let rgb = [(argb >> 16) as u8, (argb >> 8) as u8, argb as u8];
            blend(&mut target.pixels[line + tx as usize * 4..], rgb, alpha);
        }
    }
}

/// Lays one colour at `alpha` (0-255) onto a pixel.
type Blend = fn(&mut [u8], [u8; 3], u32);

/// Onto an opaque picture: the frame itself.
fn blend_pixel(pixel: &mut [u8], rgb: [u8; 3], alpha: u32) {
    for (channel, source) in pixel.iter_mut().zip(rgb) {
        let dst = u32::from(*channel);
        *channel = ((u32::from(source) * alpha + dst * (255 - alpha) + 127) / 255) as u8;
    }
}

/// Onto a transparent picture, keeping straight alpha: Porter-Duff "over".
fn over_pixel(pixel: &mut [u8], rgb: [u8; 3], alpha: u32) {
    let below = u32::from(pixel[3]);
    let covered = alpha * 255 + below * (255 - alpha);
    if covered == 0 {
        return;
    }
    for (channel, source) in pixel.iter_mut().zip(rgb) {
        let mixed = u32::from(source) * alpha * 255 + u32::from(*channel) * below * (255 - alpha);
        *channel = ((mixed + covered / 2) / covered) as u8;
    }
    pixel[3] = ((covered + 127) / 255) as u8;
}

/// The text field of a dialogue line: everything after the eighth comma.
fn dialogue_text(line: &str) -> &str {
    line.splitn(9, ',').nth(8).unwrap_or(line)
}

fn text_hash(text: &str) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    text.hash(&mut hasher);
    hasher.finish()
}

/// The codec's subtitle header as bytes, if the decoder made one.
///
/// # Safety
/// `header` must point to `size` readable bytes, or be null.
pub(crate) unsafe fn header_bytes<'a>(header: *const u8, size: c_int) -> &'a [u8] {
    if header.is_null() || size <= 0 {
        return &[];
    }
    // SAFETY: per the caller.
    unsafe { std::slice::from_raw_parts(header, size as usize) }
}

/// A C string from FFmpeg, if there is one.
///
/// # Safety
/// `text` must be null or a valid NUL-terminated string.
pub(crate) unsafe fn c_text<'a>(text: *const c_char) -> Option<&'a str> {
    if text.is_null() {
        return None;
    }
    // SAFETY: per the caller.
    unsafe { CStr::from_ptr(text) }.to_str().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEADER: &str = "[Script Info]\nScriptType: v4.00+\nPlayResX: 384\nPlayResY: 288\n\n\
        [V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, \
        BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, \
        Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n\
        Style: Default,sans-serif,16,&Hffffff,&Hffffff,&H0,&H0,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,0\n\n\
        [Events]\nFormat: ReadOrder, Layer, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n";

    fn target(pixels: &mut [u8], width: u32, height: u32) -> RenderTarget<'_> {
        RenderTarget {
            pixels,
            width,
            height,
            stride: width as usize * 4,
        }
    }

    #[test]
    fn a_text_event_draws_while_it_lasts_and_not_after() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::Stream(2);
        subtitles.add_text_track(key, HEADER.as_bytes(), false);
        subtitles.add_text_event(key, "0,0,Default,,0,0,0,,HELLO WORLD", 1.0, 2.0);
        subtitles.select(Some(key));
        let mut pixels = vec![0u8; 320 * 180 * 4];

        let overlay = subtitles.overlay(1.5, 320, 180, (320, 180));
        assert!(overlay.changed());
        overlay.blend(&mut target(&mut pixels, 320, 180));
        drop(overlay);
        assert!(pixels.iter().any(|&b| b > 200), "white text was drawn");

        let mut after = vec![0u8; 320 * 180 * 4];
        let overlay = subtitles.overlay(3.5, 320, 180, (320, 180));
        assert!(overlay.changed(), "the text going away is a change");
        overlay.blend(&mut target(&mut after, 320, 180));
        assert!(after.iter().all(|&b| b == 0));
    }

    #[test]
    fn a_re_read_event_is_not_added_twice() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::Stream(0);
        subtitles.add_text_track(key, HEADER.as_bytes(), false);
        // FFmpeg numbers events afresh after a seek: same event, new ReadOrder.
        subtitles.add_text_event(key, "0,0,Default,,0,0,0,,again", 1.0, 1.0);
        subtitles.add_text_event(key, "7,0,Default,,0,0,0,,again", 1.0, 1.0);
        let state = subtitles.state.lock();
        let Some(Track {
            content: Content::Text { track, .. },
            ..
        }) = state.tracks.first()
        else {
            panic!("a text track");
        };
        // SAFETY: the track is valid while the state lives.
        assert_eq!(unsafe { (**track).n_events }, 1);
    }

    #[test]
    fn hidden_or_unselected_subtitles_draw_nothing() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::External(3);
        subtitles.add_text_track(key, HEADER.as_bytes(), false);
        subtitles.add_text_event(key, "0,0,Default,,0,0,0,,text", 0.0, 5.0);
        let mut pixels = vec![0u8; 64 * 36 * 4];
        subtitles
            .overlay(1.0, 64, 36, (64, 36))
            .blend(&mut target(&mut pixels, 64, 36));
        assert!(pixels.iter().all(|&b| b == 0), "not selected");
        subtitles.select(Some(key));
        subtitles.set_visible(false);
        subtitles
            .overlay(1.0, 64, 36, (64, 36))
            .blend(&mut target(&mut pixels, 64, 36));
        assert!(pixels.iter().all(|&b| b == 0), "hidden");
    }

    #[test]
    fn the_delay_moves_subtitles_later() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::Stream(1);
        subtitles.add_text_track(key, HEADER.as_bytes(), false);
        subtitles.add_text_event(key, "0,0,Default,,0,0,0,,late", 10.0, 1.0);
        subtitles.select(Some(key));
        subtitles.set_delay(2.0);
        let mut pixels = vec![0u8; 320 * 180 * 4];
        subtitles
            .overlay(10.5, 320, 180, (320, 180))
            .blend(&mut target(&mut pixels, 320, 180));
        assert!(
            pixels.iter().all(|&b| b == 0),
            "not yet at 10.5 with a 2s delay"
        );
        subtitles
            .overlay(12.5, 320, 180, (320, 180))
            .blend(&mut target(&mut pixels, 320, 180));
        assert!(pixels.iter().any(|&b| b > 0));
    }

    #[test]
    fn an_image_event_lasts_until_the_next_one_and_scales_to_the_target() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::Stream(4);
        subtitles.add_bitmap_track(key, BitmapFit::Stretch);
        let red = BitmapRect {
            x: 0,
            y: 50,
            width: 100,
            height: 50,
            indices: vec![1; 100 * 50],
            palette: vec![0, 0xFFFF_0000],
        };
        subtitles.add_bitmap_event(
            key,
            BitmapEvent {
                start: 1.0,
                end: f64::INFINITY,
                canvas: (100, 100),
                rects: vec![red],
            },
        );
        // A clear at 3s ends it.
        subtitles.add_bitmap_event(
            key,
            BitmapEvent {
                start: 3.0,
                end: f64::INFINITY,
                canvas: (100, 100),
                rects: Vec::new(),
            },
        );
        subtitles.select(Some(key));
        let mut pixels = vec![0u8; 200 * 200 * 4];
        subtitles
            .overlay(2.0, 200, 200, (100, 100))
            .blend(&mut target(&mut pixels, 200, 200));
        let bottom = (150 * 200 + 100) * 4;
        assert_eq!(
            &pixels[bottom..bottom + 3],
            &[255, 0, 0],
            "the lower half is red"
        );
        assert_eq!(&pixels[..3], &[0, 0, 0], "the upper half is untouched");
        let overlay = subtitles.overlay(3.5, 200, 200, (100, 100));
        assert!(overlay.changed(), "the clear is a change");
    }

    fn line_at(y: i32) -> BitmapRect {
        BitmapRect {
            x: 660,
            y,
            width: 600,
            height: 80,
            indices: vec![1; 600 * 80],
            palette: vec![0, 0xFFFF_FFFF],
        }
    }

    /// The rows of a `width`-wide RGBA picture with anything drawn in them.
    fn drawn_rows(pixels: &[u8], width: u32) -> Vec<usize> {
        pixels
            .chunks(width as usize * 4)
            .enumerate()
            .filter(|(_, row)| row.iter().any(|&b| b > 0))
            .map(|(y, _)| y)
            .collect()
    }

    #[test]
    fn blu_ray_subtitles_on_a_cropped_picture_keep_their_size_and_stay_inside() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::Stream(3);
        subtitles.add_bitmap_track(key, BitmapFit::Crop);
        // A 1080-line canvas over a scope film cropped to 800 lines: the
        // picture is the canvas's rows 140..940. One line sits inside it, the
        // next in what was the lower bar.
        let inside_picture = BitmapEvent {
            start: 0.0,
            end: 1.0,
            canvas: (1920, 1080),
            rects: vec![line_at(800)],
        };
        let in_the_bar = BitmapEvent {
            start: 1.0,
            end: 2.0,
            canvas: (1920, 1080),
            rects: vec![line_at(960)],
        };
        subtitles.add_bitmap_event(key, inside_picture);
        subtitles.add_bitmap_event(key, in_the_bar);
        subtitles.select(Some(key));

        let mut pixels = vec![0u8; 1920 * 800 * 4];
        subtitles
            .overlay(0.5, 1920, 800, (1920, 800))
            .blend(&mut target(&mut pixels, 1920, 800));
        let rows = drawn_rows(&pixels, 1920);
        assert_eq!(
            rows.first(),
            Some(&660),
            "where it was on the uncropped picture"
        );
        assert_eq!(rows.len(), 80, "not squashed");

        let mut pixels = vec![0u8; 1920 * 800 * 4];
        subtitles
            .overlay(1.5, 1920, 800, (1920, 800))
            .blend(&mut target(&mut pixels, 1920, 800));
        let rows = drawn_rows(&pixels, 1920);
        assert_eq!(rows.len(), 80, "the whole line is drawn");
        assert_eq!(rows.last(), Some(&799), "moved up to the picture's edge");
        let row = &pixels[799 * 1920 * 4..800 * 1920 * 4];
        let columns: Vec<usize> = row
            .chunks(4)
            .enumerate()
            .filter(|(_, p)| p[0] > 0)
            .map(|(x, _)| x)
            .collect();
        assert_eq!(
            (columns.first(), columns.last()),
            (Some(&660), Some(&1259)),
            "same place across"
        );
    }

    #[test]
    fn an_image_canvas_too_small_for_its_rects_grows_to_hold_them() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::Stream(3);
        subtitles.add_bitmap_track(key, BitmapFit::Crop);
        // No canvas known yet: the video's own size stands in, and the rects
        // say the real one is bigger.
        subtitles.add_bitmap_event(
            key,
            BitmapEvent {
                start: 0.0,
                end: 1.0,
                canvas: (1920, 800),
                rects: vec![line_at(960)],
            },
        );
        subtitles.select(Some(key));
        let mut pixels = vec![0u8; 1920 * 800 * 4];
        subtitles
            .overlay(0.5, 1920, 800, (1920, 800))
            .blend(&mut target(&mut pixels, 1920, 800));
        let rows = drawn_rows(&pixels, 1920);
        assert_eq!(rows.len(), 80);
        assert_eq!(rows.last(), Some(&799));
    }

    #[test]
    fn each_track_counts_what_it_has_said() {
        let subtitles = Subtitles::new().expect("libass starts");
        let (quiet, busy) = (TrackKey::Stream(3), TrackKey::Stream(5));
        subtitles.add_text_track(quiet, HEADER.as_bytes(), false);
        subtitles.add_text_track(busy, HEADER.as_bytes(), false);
        for second in 0..4 {
            let line = format!("0,0,Default,,0,0,0,,line {second}");
            subtitles.add_text_event(busy, &line, f64::from(second), 1.0);
            subtitles.add_text_event(busy, &line, f64::from(second), 1.0); // re-read
        }
        let counts = subtitles.event_counts();
        assert!(counts.contains(&(quiet, 0)));
        assert!(counts.contains(&(busy, 4)));
    }

    #[test]
    fn subtitles_alone_rasterize_onto_transparency() {
        let subtitles = Subtitles::new().expect("libass starts");
        let key = TrackKey::Stream(2);
        subtitles.add_text_track(key, HEADER.as_bytes(), false);
        subtitles.add_text_event(key, "0,0,Default,,0,0,0,,HELLO", 1.0, 2.0);
        subtitles.select(Some(key));
        let mut pixels = vec![255u8; 320 * 180 * 4];
        let overlay = subtitles.overlay(1.5, 320, 180, (320, 180));
        assert!(overlay.has_content());
        overlay.rasterize(&mut target(&mut pixels, 320, 180));
        let alphas: Vec<u8> = pixels.chunks(4).map(|p| p[3]).collect();
        assert!(alphas.contains(&0), "most of the picture stays transparent");
        assert!(alphas.iter().any(|&a| a > 200), "the text is opaque");
        // Covered pixels are white text or its black border, never tinted by
        // the transparent black they were composed over.
        assert!(
            pixels
                .chunks(4)
                .filter(|p| p[3] > 250)
                .all(|p| p[0] == p[1] && p[1] == p[2])
        );
    }

    #[test]
    fn over_keeps_straight_alpha() {
        let mut pixel = [0u8, 0, 0, 0];
        over_pixel(&mut pixel, [200, 100, 50], 128);
        assert_eq!(
            pixel,
            [200, 100, 50, 128],
            "onto nothing, a colour keeps its value"
        );
        over_pixel(&mut pixel, [0, 0, 0], 255);
        assert_eq!(pixel, [0, 0, 0, 255]);
    }

    #[test]
    fn a_dialogue_line_keeps_commas_in_its_text() {
        assert_eq!(
            dialogue_text("1,0,Default,,0,0,0,,Well, hello, there"),
            "Well, hello, there"
        );
    }
}
