from gravitas.application.segments import classify
from gravitas.domain.models import Segments


def test_the_measured_release() -> None:
    """Ted S1E7, FLUX WEB-DL, as mpv listed it."""
    chapters = [(0.0, "Chapter 01"), (6.01, "Intro"), (26.86, "Chapter 02"), (2492.11, "Credits")]
    assert classify(chapters, 2559.224) == Segments(
        intro=(6.01, 26.86), recap=None, credits_start=2492.11
    )


def test_unnamed_chapters_say_nothing() -> None:
    chapters = [(0.0, "Chapter 1"), (95.0, "Chapter 2"), (2400.0, "Chapter 3")]
    assert classify(chapters, 2600.0) == Segments()


def test_no_chapters_or_no_duration() -> None:
    assert classify([], 2600.0) == Segments()
    assert classify([(0.0, "Intro")], 0.0) == Segments()


def test_recap_and_opening_credits() -> None:
    chapters = [
        (0.0, "Previously On"),
        (48.0, "Cold Open"),
        (190.0, "Opening Credits"),
        (250.0, "Episode"),
        (2500.0, "End Credits"),
        (2560.0, "Next Episode Preview"),
    ]
    segments = classify(chapters, 2600.0)
    assert segments.recap == (0.0, 48.0)
    assert segments.intro == (190.0, 250.0)
    # The first of the closing chapters, not the preview after it.
    assert segments.credits_start == 2500.0


def test_position_settles_an_ambiguous_title() -> None:
    """A title "Credits" at the start is the opening, at the end the end credits."""
    chapters = [(0.0, "Prologue"), (60.0, "Credits"), (120.0, "Story"), (2450.0, "Credits")]
    segments = classify(chapters, 2600.0)
    assert segments.intro == (60.0, 120.0)
    assert segments.credits_start == 2450.0
    # Other languages the same way.
    german = classify([(0.0, "Vorspann"), (40.0, "Teil 1"), (2480.0, "Abspann")], 2600.0)
    assert german.intro == (0.0, 40.0)
    assert german.credits_start == 2480.0
    french = classify([(0.0, "Générique"), (50.0, "Partie"), (2470.0, "Générique")], 2600.0)
    assert french == Segments(intro=(0.0, 50.0), credits_start=2470.0)


def test_implausible_chapters_are_not_believed() -> None:
    # An "intro" twenty minutes long is a mislabelled act, not something to skip.
    assert classify([(0.0, "Intro"), (1200.0, "Rest")], 2600.0).intro is None
    # "Credits" in the first half is never where the episode ends.
    early_credits = [(0.0, "A"), (400.0, "End Credits"), (500.0, "B")]
    assert classify(early_credits, 2600.0).credits_start is None
    # Word boundaries: "Operation" is not "op".
    assert classify([(0.0, "Operation"), (60.0, "B")], 2600.0).intro is None


def test_sections_for_the_measured_release() -> None:
    from gravitas.application.segments import Section, sections

    chapters = [(0.0, "Chapter 01"), (6.01, "Intro"), (26.86, "Chapter 02"), (2492.11, "Credits")]
    segments = classify(chapters, 2559.224)
    assert sections(chapters, segments, 2559.224) == [
        Section(0.0, 6.01, "Chapter 1", ""),
        Section(6.01, 26.86, "Intro", "intro"),
        Section(26.86, 2492.11, "Chapter 2", ""),
        Section(2492.11, 2559.224, "Credits", "credits"),
    ]


def test_sections_from_a_source_when_the_file_has_no_chapters() -> None:
    from gravitas.application.segments import Section, sections

    found = Segments(intro=(229.5, 246.5), credits_start=3434.0)
    assert sections([], found, 3500.0) == [
        Section(0.0, 229.5, "Episode", ""),
        Section(229.5, 246.5, "Intro", "intro"),
        Section(246.5, 3434.0, "Episode", ""),
        Section(3434.0, 3500.0, "Credits", "credits"),
    ]


def test_sections_keep_meaningful_chapter_titles() -> None:
    from gravitas.application.segments import Section, sections

    chapters = [
        (0.0, "Previously On"),
        (48.0, "Cold Open"),
        (190.0, "Opening Credits"),
        (250.0, "The Heist"),
        (2500.0, "End Credits"),
        (2560.0, "Next Episode Preview"),
    ]
    result = sections(chapters, classify(chapters, 2600.0), 2600.0)
    assert [(s.title, s.kind) for s in result] == [
        ("Recap", "recap"),
        ("Cold Open", ""),
        ("Intro", "intro"),
        ("The Heist", ""),
        ("Credits", "credits"),
        ("Next Episode Preview", "credits"),
    ]
    assert result[-1] == Section(2560.0, 2600.0, "Next Episode Preview", "credits")


def test_nothing_to_split_is_one_bar() -> None:
    from gravitas.application.segments import sections

    assert sections([], Segments(), 2600.0) == []
    assert sections([(0.0, "Chapter 1")], Segments(), 2600.0) == []
    assert sections([(0.0, "A"), (100.0, "B")], Segments(), 0.0) == []


def test_slivers_merge_into_their_neighbour() -> None:
    from gravitas.application.segments import sections

    # A chapter boundary a fraction of a second from the intro's edge.
    chapters = [(0.0, "Chapter 1"), (60.0, "Intro"), (90.4, "Chapter 2")]
    found = Segments(intro=(60.0, 90.0))
    result = sections(chapters, found, 2600.0)
    assert [round(s.end - s.start, 1) for s in result] == [60.0, 30.4, 2509.6]


def test_timestamp_titles_name_nothing() -> None:
    """Many muxers title an unnamed chapter with its own start time."""
    from gravitas.application.segments import sections

    chapters = [(0.0, "00:00:00.000"), (33.408, "00:00:33.408"), (700.0, "00:11:40")]
    found = Segments(intro=(0.0, 33.5), credits_start=1293.2)
    result = sections(chapters, found, 1327.0)
    # Timestamps carry no number of their own: the chapter's place counts.
    assert [(s.title, s.kind) for s in result] == [
        ("Intro", "intro"),
        ("Chapter 2", ""),
        ("Chapter 3", ""),
        ("Credits", "credits"),
    ]
