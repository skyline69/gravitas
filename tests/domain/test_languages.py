from gravitas.domain import languages
from gravitas.domain.languages import LANGUAGES, SUBTITLES_OFF, normalized, track_codes


def test_codes_are_unique_and_the_menu_is_alphabetical() -> None:
    codes = [language.code.lower() for language in LANGUAGES]
    assert len(codes) == len(set(codes))
    names = [language.name for language in LANGUAGES]
    assert names == sorted(names)


def test_every_country_is_an_iso_3166_pair_or_none() -> None:
    for language in LANGUAGES:
        country = language.country
        assert country is None or (len(country) == 2 and country.isupper()), language.name
    assert languages.find("de").country == "DE"  # type: ignore[union-attr]
    assert languages.find("eo").country is None  # type: ignore[union-attr]


def test_track_codes_cover_every_spelling_a_muxer_uses() -> None:
    """Older mpv compares the strings literally, so German must also match a
    track tagged `ger` (639-2/B) and `deu` (639-2/T)."""
    assert track_codes("de") == ("de", "ger", "deu")
    # The regional variant first, then any track in the language at all.
    assert track_codes("pt-BR") == ("pt-BR", "pt", "por", "pob")


def test_no_preference_off_and_unknown_codes_match_nothing() -> None:
    assert track_codes("") == ()
    assert track_codes(SUBTITLES_OFF) == ()
    assert track_codes("klingon") == ()


def test_normalized_keeps_known_codes_and_drops_the_rest() -> None:
    assert normalized(" DE ") == "de"
    assert normalized("pt-br") == "pt-BR"
    assert normalized("klingon") == ""
    # "Off" is a subtitle choice only: audio has no such thing.
    assert normalized("off", subtitles=True) == SUBTITLES_OFF
    assert normalized("off") == ""
