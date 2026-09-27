"""Reading a track list: what to call each track, and which are in a language.

Both engines describe tracks in mpv's `track-list` shape -- dicts with `id`,
`type` ("video", "audio", "sub"), `lang`, `title`, `forced`, ... -- so one
module labels them and answers the language questions for both.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from gravitas.domain import languages
from gravitas.domain.models import HdrFormat, MediaFormat

Track = Mapping[str, Any]

# ISO 639-1 and common 639-2 codes -> display names. Containers carry either.
LANG_NAMES = {
    "en": "English",
    "eng": "English",
    "de": "German",
    "ger": "German",
    "deu": "German",
    "fr": "French",
    "fre": "French",
    "fra": "French",
    "es": "Spanish",
    "spa": "Spanish",
    "it": "Italian",
    "ita": "Italian",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
    "ja": "Japanese",
    "jpn": "Japanese",
    "ko": "Korean",
    "kor": "Korean",
    "zh": "Chinese",
    "chi": "Chinese",
    "zho": "Chinese",
    "ar": "Arabic",
    "ara": "Arabic",
    "hi": "Hindi",
    "hin": "Hindi",
    "tr": "Turkish",
    "tur": "Turkish",
    "pl": "Polish",
    "pol": "Polish",
    "nl": "Dutch",
    "dut": "Dutch",
    "nld": "Dutch",
    "sv": "Swedish",
    "swe": "Swedish",
    "no": "Norwegian",
    "nor": "Norwegian",
    "da": "Danish",
    "dan": "Danish",
    "fi": "Finnish",
    "fin": "Finnish",
    "cs": "Czech",
    "cze": "Czech",
    "ces": "Czech",
    "el": "Greek",
    "gre": "Greek",
    "ell": "Greek",
    "he": "Hebrew",
    "heb": "Hebrew",
    "hu": "Hungarian",
    "hun": "Hungarian",
    "ro": "Romanian",
    "rum": "Romanian",
    "ron": "Romanian",
    "uk": "Ukrainian",
    "ukr": "Ukrainian",
    "th": "Thai",
    "tha": "Thai",
    "vi": "Vietnamese",
    "vie": "Vietnamese",
    "id": "Indonesian",
    "ind": "Indonesian",
    "fa": "Persian",
    "per": "Persian",
    "fas": "Persian",
    "bg": "Bulgarian",
    "bul": "Bulgarian",
    "hr": "Croatian",
    "hrv": "Croatian",
    "sr": "Serbian",
    "srp": "Serbian",
    "sk": "Slovak",
    "slo": "Slovak",
    "slk": "Slovak",
    "sl": "Slovenian",
    "slv": "Slovenian",
    "lt": "Lithuanian",
    "lit": "Lithuanian",
    "lv": "Latvian",
    "lav": "Latvian",
    "et": "Estonian",
    "est": "Estonian",
    "ca": "Catalan",
    "cat": "Catalan",
    "ms": "Malay",
    "may": "Malay",
    "msa": "Malay",
    "ta": "Tamil",
    "tam": "Tamil",
    "te": "Telugu",
    "tel": "Telugu",
}

CHANNEL_LABELS = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}


def lang_name(code: object) -> str:
    """'en' -> 'English', 'fr-CA' -> 'French (CA)'; unknown codes pass through."""
    if not isinstance(code, str) or not code:
        return ""
    base, _, region = code.partition("-")
    name = LANG_NAMES.get(base.lower())
    if name is None:
        return code
    return f"{name} ({region.upper()})" if region else name


# Language tags that name no language: what an untagged track reports.
UNTAGGED_LANGS = frozenset({"", "und", "unk", "mis", "zxx"})


def forced_only(track: Track) -> bool:
    """A forced track: only the lines in another language, so mostly blank."""
    return bool(track.get("forced")) or "forced" in str(track.get("title") or "").lower()


def track_label(track: Track) -> str:
    lang = lang_name(track.get("lang"))
    title = str(track.get("title") or "")
    if title and lang and lang.split(" (")[0].lower() in title.lower():
        # The title already names the language ("English (United States)") —
        # prefixing the code again is noise.
        parts = [title]
    elif title and lang:
        parts = [lang, title]
    elif title or lang:
        parts = [title or lang]
    else:
        parts = [f"Track {track['id']}"]
    if track.get("type") == "audio":
        count = track.get("demux-channel-count")
        if isinstance(count, int) and count:
            channels = CHANNEL_LABELS.get(count, f"{count}ch")
            if channels.lower() not in " ".join(parts).lower():
                parts.append(channels)
    label = " · ".join(parts)
    flags = [
        name
        for key, name in (
            ("forced", "Forced"),
            ("hearing-impaired", "SDH"),
            ("visual-impaired", "AD"),
            # A file loaded beside the video (sub-add): an addon's subtitle.
            ("external", "Online"),
        )
        if track.get(key)
    ]
    if flags:
        label += " (" + ", ".join(flags) + ")"
    return label


def labelled(tracks: Iterable[Track], kind: str) -> list[tuple[int, str]]:
    """(id, label) for every track of `kind`. Identically labelled tracks
    (same language, no distinguishing metadata) get an index, so the menu
    rows are not interchangeable."""
    found = [(int(t["id"]), track_label(t)) for t in tracks if t.get("type") == kind]
    counts = Counter(label for _, label in found)
    seen: Counter[str] = Counter()
    result: list[tuple[int, str]] = []
    for track_id, label in found:
        if counts[label] > 1:
            seen[label] += 1
            result.append((track_id, f"{label} · #{seen[label]}"))
        else:
            result.append((track_id, label))
    return result


def in_language(subs: Sequence[Track], wanted: str) -> tuple[list[Track], list[Track]]:
    """(tracks tagged with `wanted`, untagged tracks whose title names it),
    each with full tracks before forced-only ones."""
    codes = {code.lower() for code in languages.track_codes(wanted)}
    tagged = [t for t in subs if str(t.get("lang") or "").lower() in codes]
    named: list[Track] = []
    language = languages.find(wanted)
    if language is not None:
        name = language.name.split(" (")[0].lower()
        named = [
            t
            for t in subs
            if str(t.get("lang") or "").lower() in UNTAGGED_LANGS
            and name in str(t.get("title") or "").lower()
        ]
    tagged.sort(key=forced_only)
    named.sort(key=forced_only)
    return tagged, named


def has_full_track(subs: Sequence[Track], wanted: str) -> bool:
    """Whether a full (not forced-only) subtitle track is in `wanted`: tagged
    with it, or untagged and titled with its name."""
    if not wanted or wanted == languages.SUBTITLES_OFF:
        return False
    tagged, named = in_language(subs, wanted)
    return any(not forced_only(t) for t in (*tagged, *named))


def fallback_subtitle(subs: Sequence[Track], wanted: str) -> Track | None:
    """The track to show when none is tagged `wanted`: an untagged one whose
    title names the language (full before forced-only), or the only subtitle
    track there is when it is untagged. None when a track IS tagged `wanted`
    -- the engine picks that one itself -- and never a track tagged with
    another language: that is a fact, and a title only a guess."""
    if not wanted or wanted == languages.SUBTITLES_OFF:
        return None
    tagged, named = in_language(subs, wanted)
    if tagged:
        return None
    if named:
        return named[0]
    if len(subs) == 1 and str(subs[0].get("lang") or "").lower() in UNTAGGED_LANGS:
        return subs[0]
    return None


def _playing(tracks: Sequence[Track], kind: str) -> Track | None:
    """The selected track of `kind`, or its first when none is marked."""
    of_kind = [t for t in tracks if t.get("type") == kind]
    return next((t for t in of_kind if t.get("selected")), of_kind[0] if of_kind else None)


def _number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _resolution(width: int, height: int) -> str:
    """The picture's class by whichever side says more: a 2.39:1 film in 4K
    is 3840x1600, and a 4:3 one in 1080p is 1440x1080."""
    for name, min_width, min_height in (
        ("4K", 3200, 2000),
        ("1440p", 2400, 1400),
        ("1080p", 1800, 1000),
        ("720p", 1200, 700),
    ):
        if width >= min_width or height >= min_height:
            return name
    return ""


def media_format(tracks: Iterable[Track]) -> MediaFormat:
    """What the file playing is, from its selected tracks.

    The HDR format comes from the file itself: a Dolby Vision configuration
    record, HDR10+ metadata on its frames, or its transfer (`color-transfer`,
    "pq" or "hlg"). Dolby Vision wins over the HDR10 base layer a profile 8
    file also has. Immersive audio is named by FFmpeg's codec profile
    (`codec-profile`), as both engines report it."""
    listed = list(tracks)
    video = _playing(listed, "video")
    audio = _playing(listed, "audio")
    hdr: HdrFormat = ""
    profile = 0
    resolution = ""
    if video is not None:
        profile = _number(video.get("dolby-vision-profile"))
        transfer = video.get("color-transfer")
        if profile > 0:
            hdr = "Dolby Vision"
        elif transfer == "pq":
            hdr = "HDR10+" if video.get("hdr10-plus") else "HDR10"
        elif transfer == "hlg":
            hdr = "HLG"
        resolution = _resolution(_number(video.get("demux-w")), _number(video.get("demux-h")))
    immersive = ""
    channels = ""
    if audio is not None:
        codec_profile = str(audio.get("codec-profile") or "")
        if "Atmos" in codec_profile:
            immersive = "Dolby Atmos"
        elif "DTS:X" in codec_profile:
            immersive = "DTS:X"
        channels = {6: "5.1", 8: "7.1"}.get(_number(audio.get("demux-channel-count")), "")
    return MediaFormat(
        hdr=hdr,
        dolby_vision_profile=profile,
        resolution=resolution,
        immersive_audio=immersive,
        channels=channels,
    )
