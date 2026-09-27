"""The languages a viewer can prefer for audio and subtitles.

A preference is stored as one code (the ISO 639-1 code where the language has
one), but a file's tracks are tagged however its muxer felt like: `ger`, `deu`
and `de` are all German, and a Matroska track may carry an IETF tag such as
`pt-BR` besides. Newer mpv builds match across those spellings on their own,
older ones compare the strings literally -- so `track_codes()` hands the player
every spelling, in priority order, and the matching never depends on the build.

`country` picks the flag the menu shows (an ISO 3166 code; the images ship
with the app, see scripts/vendor_flags.py). It is a picture, not a claim about
where a language is spoken, and a language that belongs to no one country has
None and gets a globe.
"""

from __future__ import annotations

from dataclasses import dataclass

# The subtitle preference that means "no subtitles". Not a language code, so it
# can never collide with one.
SUBTITLES_OFF = "off"


@dataclass(frozen=True, slots=True)
class Language:
    code: str
    name: str
    country: str | None
    # Every other tag a track in this language may carry, most specific
    # first. The code itself always leads (see track_codes).
    aliases: tuple[str, ...] = ()


def _lang(code: str, name: str, country: str | None, *aliases: str) -> Language:
    return Language(code, name, country, aliases)


# Alphabetical by English name, which is the order the menu shows.
LANGUAGES: tuple[Language, ...] = (
    _lang("af", "Afrikaans", "ZA", "afr"),
    _lang("sq", "Albanian", "AL", "alb", "sqi"),
    _lang("am", "Amharic", "ET", "amh"),
    _lang("ar", "Arabic", "SA", "ara"),
    _lang("hy", "Armenian", "AM", "arm", "hye"),
    _lang("az", "Azerbaijani", "AZ", "aze"),
    _lang("eu", "Basque", None, "baq", "eus"),
    _lang("be", "Belarusian", "BY", "bel"),
    _lang("bn", "Bengali", "BD", "ben"),
    _lang("bs", "Bosnian", "BA", "bos"),
    _lang("bg", "Bulgarian", "BG", "bul"),
    _lang("my", "Burmese", "MM", "bur", "mya"),
    _lang("yue", "Cantonese", "HK", "zh-HK"),
    _lang("ca", "Catalan", None, "cat"),
    _lang("zh", "Chinese", "CN", "chi", "zho"),
    _lang("hr", "Croatian", "HR", "hrv", "scr"),
    _lang("cs", "Czech", "CZ", "cze", "ces"),
    _lang("da", "Danish", "DK", "dan"),
    _lang("nl", "Dutch", "NL", "dut", "nld"),
    _lang("en", "English", "GB", "eng"),
    _lang("eo", "Esperanto", None, "epo"),
    _lang("et", "Estonian", "EE", "est"),
    _lang("tl", "Filipino", "PH", "fil", "tgl"),
    _lang("fi", "Finnish", "FI", "fin"),
    _lang("fr", "French", "FR", "fre", "fra"),
    _lang("gl", "Galician", None, "glg"),
    _lang("ka", "Georgian", "GE", "geo", "kat"),
    _lang("de", "German", "DE", "ger", "deu"),
    _lang("el", "Greek", "GR", "gre", "ell"),
    _lang("gu", "Gujarati", "IN", "guj"),
    _lang("ha", "Hausa", "NG", "hau"),
    _lang("he", "Hebrew", "IL", "heb", "iw"),
    _lang("hi", "Hindi", "IN", "hin"),
    _lang("hu", "Hungarian", "HU", "hun"),
    _lang("is", "Icelandic", "IS", "ice", "isl"),
    _lang("id", "Indonesian", "ID", "ind"),
    _lang("ga", "Irish", "IE", "gle"),
    _lang("it", "Italian", "IT", "ita"),
    _lang("ja", "Japanese", "JP", "jpn"),
    _lang("kn", "Kannada", "IN", "kan"),
    _lang("kk", "Kazakh", "KZ", "kaz"),
    _lang("km", "Khmer", "KH", "khm"),
    _lang("ko", "Korean", "KR", "kor"),
    _lang("ku", "Kurdish", None, "kur"),
    _lang("lo", "Lao", "LA", "lao"),
    _lang("la", "Latin", None, "lat"),
    _lang("es-419", "Latin American Spanish", "MX", "es-MX", "es", "spa"),
    _lang("lv", "Latvian", "LV", "lav"),
    _lang("lt", "Lithuanian", "LT", "lit"),
    _lang("lb", "Luxembourgish", "LU", "ltz"),
    _lang("mk", "Macedonian", "MK", "mac", "mkd"),
    _lang("ms", "Malay", "MY", "may", "msa"),
    _lang("ml", "Malayalam", "IN", "mal"),
    _lang("mt", "Maltese", "MT", "mlt"),
    _lang("mr", "Marathi", "IN", "mar"),
    _lang("mn", "Mongolian", "MN", "mon"),
    _lang("ne", "Nepali", "NP", "nep"),
    _lang("no", "Norwegian", "NO", "nor", "nb", "nob", "nn", "nno"),
    _lang("ps", "Pashto", "AF", "pus"),
    _lang("fa", "Persian", "IR", "per", "fas"),
    _lang("pl", "Polish", "PL", "pol"),
    _lang("pt", "Portuguese", "PT", "por"),
    _lang("pt-BR", "Portuguese (Brazil)", "BR", "pt", "por", "pob"),
    _lang("pa", "Punjabi", "IN", "pan"),
    _lang("ro", "Romanian", "RO", "rum", "ron"),
    _lang("ru", "Russian", "RU", "rus"),
    _lang("sr", "Serbian", "RS", "srp", "scc"),
    _lang("si", "Sinhala", "LK", "sin"),
    _lang("sk", "Slovak", "SK", "slo", "slk"),
    _lang("sl", "Slovenian", "SI", "slv"),
    _lang("so", "Somali", "SO", "som"),
    _lang("es", "Spanish", "ES", "spa"),
    _lang("sw", "Swahili", "KE", "swa"),
    _lang("sv", "Swedish", "SE", "swe"),
    _lang("ta", "Tamil", "IN", "tam"),
    _lang("te", "Telugu", "IN", "tel"),
    _lang("th", "Thai", "TH", "tha"),
    _lang("tr", "Turkish", "TR", "tur"),
    _lang("uk", "Ukrainian", "UA", "ukr"),
    _lang("ur", "Urdu", "PK", "urd"),
    _lang("uz", "Uzbek", "UZ", "uzb"),
    _lang("vi", "Vietnamese", "VN", "vie"),
    _lang("cy", "Welsh", None, "wel", "cym"),
    _lang("yo", "Yoruba", "NG", "yor"),
    _lang("zu", "Zulu", "ZA", "zul"),
)

_BY_CODE = {language.code.lower(): language for language in LANGUAGES}


def find(code: str) -> Language | None:
    """The language stored under `code`, or None for an unknown one."""
    return _BY_CODE.get(code.strip().lower())


def by_tag(tag: str) -> Language | None:
    """The language a track or subtitle file is TAGGED with ("eng", "ger",
    "pt-BR", OpenSubtitles' "pob"): matched against every code and alias,
    most specific first, so "pob" is Brazilian Portuguese rather than any."""
    wanted = tag.strip().lower()
    if not wanted:
        return None
    exact = find(wanted)
    if exact is not None:
        return exact
    # Base languages before regional ones: "spa" is Spanish, even though
    # Latin American Spanish lists it as a fallback and sorts first.
    for language in sorted(LANGUAGES, key=lambda entry: "-" in entry.code):
        if wanted in (alias.lower() for alias in language.aliases):
            return language
    return None


def track_codes(code: str) -> tuple[str, ...]:
    """Every tag a track in this language may carry, in priority order, for
    the player to match against. Empty for no preference, for SUBTITLES_OFF
    and for a code this build does not know."""
    language = find(code)
    if language is None:
        return ()
    return (language.code, *language.aliases)


def normalized(code: str, *, subtitles: bool = False) -> str:
    """`code` as it should be stored: a known code, SUBTITLES_OFF (subtitles
    only), or "" for no preference. Anything else -- a hand-edited settings
    file, a language a later version dropped -- is no preference rather than
    an error."""
    if subtitles and code.strip().lower() == SUBTITLES_OFF:
        return SUBTITLES_OFF
    language = find(code)
    return language.code if language is not None else ""
