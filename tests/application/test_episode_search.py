"""Finding episodes of a series by title, synopsis or number."""

from __future__ import annotations

from gravitas.application.episode_search import find_episodes
from gravitas.domain.models import Video

_VIDEOS = (
    Video(id="s:0:1", title="Behind the Scenes", season=0, episode=1),
    Video(id="s:2:3", title="Dungeons & Dealers", season=2, episode=3, overview="A weed shortage."),
    Video(id="s:1:3", title="Mrs. Robicheck", season=1, episode=3, overview="Ted has an affair."),
    Video(id="s:1:1", title="Talk Dirty to Me", season=1, episode=1),
    Video(id="s:1:2", title="Café Society", season=1, episode=2, overview="Rom-coms, again."),
    Video(id="s:2:1", title="1993", season=2, episode=1),
)


def _ids(query: str) -> list[str]:
    return [v.id for v in find_episodes(_VIDEOS, query)]


def test_a_blank_query_names_nothing() -> None:
    assert _ids("") == []
    assert _ids("   ") == []


def test_words_match_title_or_synopsis_in_any_case_and_order() -> None:
    assert _ids("dungeons") == ["s:2:3"]
    assert _ids("AFFAIR ted") == ["s:1:3"], "every word, from the synopsis"
    assert _ids("rom-coms") == ["s:1:2"]
    assert _ids("ted dungeons") == [], "all words must match one episode"


def test_accents_do_not_matter() -> None:
    assert _ids("cafe") == ["s:1:2"]


def test_numbers_name_episodes_in_page_order() -> None:
    assert _ids("3") == ["s:1:3", "s:2:3"]
    assert _ids("e3") == ["s:1:3", "s:2:3"]
    assert _ids("S2E3") == ["s:2:3"]
    assert _ids("s02 e03") == ["s:2:3"]
    assert _ids("2x03") == ["s:2:3"]
    assert _ids("e1") == ["s:1:1", "s:2:1", "s:0:1"], "Specials last"


def test_a_season_alone_lists_that_season() -> None:
    assert _ids("s1") == ["s:1:1", "s:1:2", "s:1:3"]


def test_a_number_no_episode_carries_is_read_as_a_word() -> None:
    assert _ids("1993") == ["s:2:1"]
