"""Where an episode's intro, recap and end credits are, read off its chapters.

Many releases carry Matroska chapters, and the streaming-service WEB-DLs most
sources are cut from name the ones that matter -- measured on a FLUX release of
Ted S1E7: "Chapter 01" 0:00, "Intro" 0:06, "Chapter 02" 0:26, "Credits" 41:32.
The player reads them for free (mpv has the list as soon as the file opens), so
this is exact where it applies and silent where it does not.

It only ever reads TITLES. "Chapter 02" says nothing about what is in it, and a
guess that skips the viewer past the start of an episode, or puts "next
episode" over its last scene, is worse than no button at all. Position is used
only to settle a title that is ambiguous on its own: "Credits" or "Générique"
near the start is the opening, near the end it is the end credits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gravitas.domain.models import Segments

# A chapter this long is not an intro, whatever it is called: a mislabelled
# first act would be skipped wholesale.
MAX_INTRO_S = 240.0
MAX_RECAP_S = 240.0

_RECAP = re.compile(r"\b(recap|previously|zuvor|anteriormente|précédemment)\b")
_INTRO = re.compile(
    r"\b(intro|introduction|opening|op|title sequence|main titles?|theme( song)?|vorspann)\b"
)
_CREDITS = re.compile(
    r"\b(credits?|ending|ed|outro|closing|end titles?|abspann|cr[ée]ditos|g[ée]n[ée]rique)\b"
)
_PREVIEW = re.compile(r"\b(preview|next episode|next time|next on|vorschau|avance)\b")
# Titles that settle nothing on their own: position decides.
_EITHER_END = re.compile(r"^(credits?|g[ée]n[ée]rique|cr[ée]ditos)$")


def classify(chapters: list[tuple[float, str]], duration: float) -> Segments:
    """Read intro, recap and end-credits positions off (start, title) chapters."""
    if duration <= 0 or not chapters:
        return Segments()
    ordered = sorted(chapters)
    intro: tuple[float, float] | None = None
    recap: tuple[float, float] | None = None
    credits_start: float | None = None
    for index, (start, raw_title) in enumerate(ordered):
        title = raw_title.strip().lower()
        end = ordered[index + 1][0] if index + 1 < len(ordered) else duration
        length = end - start
        early = start < duration * 0.5
        if _RECAP.search(title):
            if recap is None and early and 0 < length <= MAX_RECAP_S:
                recap = (start, end)
            continue
        opening = _INTRO.search(title) or (_EITHER_END.match(title) and early)
        if opening and not _PREVIEW.search(title):
            if intro is None and early and 0 < length <= MAX_INTRO_S:
                intro = (start, end)
            continue
        closing = _CREDITS.search(title) or _PREVIEW.search(title)
        if closing and not early and credits_start is None:
            credits_start = start
    return Segments(intro=intro, recap=recap, credits_start=credits_start)


@dataclass(frozen=True, slots=True)
class Section:
    """One stretch of the timeline between two boundaries. `kind` is "intro",
    "recap" or "credits" for a section Segments names, else "". `title` is
    what the timeline says on hover: the kind's name, a chapter's own title
    when it says something, else "Chapter N" (its number in the file) or,
    with no chapters at all, "Episode"."""

    start: float
    end: float
    title: str
    kind: str


# Titles that name nothing: "Chapter 02", "12", and the start time itself
# ("00:00:33.408"), which is what many muxers write when a chapter has no name.
_GENERIC_TITLE = re.compile(
    r"^((chapter|kapitel|chapitre|cap[ií]tulo|part|teil)?\s*\d*"
    r"|\d{1,2}:\d{2}(:\d{2})?([.,]\d+)?)$"
)
_KIND_TITLES = {"intro": "Intro", "recap": "Recap", "credits": "Credits"}
# Shorter than this is a sliver between two nearly equal boundaries, not a
# section anyone could hover.
_MIN_SECTION_S = 1.0


def sections(
    chapters: list[tuple[float, str]], segments: Segments, duration: float
) -> list[Section]:
    """The timeline's sections: split at every chapter and at the edges of the
    intro, recap and credits, however they were learnt (chapters or a
    SegmentSource). Empty when there is nothing to split -- a bar in one piece
    is drawn as it always was."""
    if duration <= 0:
        return []
    ordered = sorted((start, title) for start, title in chapters if 0 <= start < duration)
    points = {start for start, _ in ordered} | {0.0}
    marks: list[tuple[float, str]] = []
    if segments.recap is not None:
        marks.append((segments.recap[0], "recap"))
        points |= {segments.recap[0], segments.recap[1]}
    if segments.intro is not None:
        marks.append((segments.intro[0], "intro"))
        points |= {segments.intro[0], segments.intro[1]}
    if segments.credits_start is not None:
        marks.append((segments.credits_start, "credits"))
        points.add(segments.credits_start)
    bounds = sorted(p for p in points if 0 <= p < duration)
    if len(bounds) < 2:
        return []

    def chapter_title(at: float) -> str:
        title = ""
        for start, name in ordered:
            if start > at + 0.01:
                break
            title = name.strip()
        return "" if _GENERIC_TITLE.match(title.lower()) else title

    def fallback_label(at: float) -> str:
        """What an unnamed stretch is called, so hovering the bar says why it
        is split there: the file's own chapter number, or -- with no chapters,
        when the split came from a SegmentSource -- the episode itself."""
        if not ordered:
            return "Episode"
        number, title = 0, ""
        for index, (start, name) in enumerate(ordered, start=1):
            if start > at + 0.01:
                break
            number, title = index, name.strip()
        if not number:
            return "Episode"
        # The file's own number when the title carries one ("Chapter 02" is
        # chapter 2, even with an "Intro" chapter before it); its position
        # when the title is a timestamp or empty.
        own = re.fullmatch(r"\D*?(\d{1,3})", title)
        return f"Chapter {int(own.group(1))}" if own else f"Chapter {number}"

    def kind_at(start: float, end: float) -> str:
        for mark, kind in marks:
            if kind == "credits":
                if start >= mark - 0.01:
                    return "credits"
            elif abs(mark - start) < 0.01:
                return kind
        return ""

    result: list[Section] = []
    for index, start in enumerate(bounds):
        end = bounds[index + 1] if index + 1 < len(bounds) else duration
        kind = kind_at(start, end)
        own = chapter_title(start)
        # A named chapter after the credits begin ("Preview", a post-credits
        # scene) keeps its own name; the section the credits start in, and
        # any generic chapter after it, says "Credits".
        after_credits = (
            kind == "credits"
            and segments.credits_start is not None
            and start > segments.credits_start + 0.01
        )
        title = (
            own
            if after_credits and own
            else (_KIND_TITLES.get(kind) or own or fallback_label(start))
        )
        if result and end - start < _MIN_SECTION_S:
            last = result[-1]
            result[-1] = Section(last.start, end, last.title, last.kind)
            continue
        result.append(Section(start, end, title, kind))
    return result if len(result) > 1 else []
