"""Qt list model exposing resolved streams to the QML source picker.

Addon stream labels are free-form strings ("4K ⚡ ⟨Web-dl⟩ ★★"); the model
splits the recognisable tokens (resolution, instant marker, bracket tags,
star rating) into their own roles so the UI can render chips instead of a
wall of symbols. Unrecognised text survives in the detail role.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.domain.models import Stream

_ROOT_INDEX = QModelIndex()

_RES_RE = re.compile(r"\b(2160p|1440p|1080p|720p|480p|4k)\b", re.IGNORECASE)
_TAG_RE = re.compile(r"[⟨〈\[(]([^⟩〉\])]{1,24})[⟩〉\])]")


@dataclass(frozen=True, slots=True)
class StreamDisplay:
    resolution: str
    instant: bool
    tags: list[str]
    stars: int
    detail: str
    subtitle: str


def parse_stream_display(name: str, title: str) -> StreamDisplay:
    text = name or title
    resolution = ""
    match = _RES_RE.search(text)
    if match:
        raw = match.group(1).upper()
        resolution = "4K" if raw in ("4K", "2160P") else raw
    tags = [tag.strip() for tag in _TAG_RE.findall(text) if tag.strip()]
    stars = text.count("★")
    detail = _TAG_RE.sub("", _RES_RE.sub("", text))
    for junk in ("⚡", "★", "☆"):
        detail = detail.replace(junk, "")
    detail = " ".join(detail.split())
    return StreamDisplay(
        resolution=resolution,
        instant="⚡" in text,
        tags=tags,
        stars=stars,
        detail=detail,
        # The second line is only worth showing when it adds information.
        subtitle=title if title and title != name else "",
    )


@dataclass(frozen=True, slots=True)
class _Row:
    stream: Stream
    display: StreamDisplay
    # proxy_headers is a frozen tuple-of-tuples; materialised once per row
    # rather than rebuilt on every HeadersRole data() fetch.
    headers: dict[str, str]
    over_budget: bool
    oversized: bool
    reason: str
    # Which row this is across two answers for the same video: the label, plus
    # how many earlier rows carried the same one.
    key: tuple[str, str, int]


def _rows(
    streams: Sequence[Stream],
    over_budget: Sequence[bool],
    oversized: Sequence[bool],
    reasons: Sequence[str],
) -> list[_Row]:
    def _at(values: Sequence[Any], i: int, default: Any) -> Any:
        return values[i] if i < len(values) else default

    seen: dict[tuple[str, str], int] = {}
    rows: list[_Row] = []
    for i, stream in enumerate(streams):
        label = (stream.name, stream.title)
        occurrence = seen.get(label, 0)
        seen[label] = occurrence + 1
        rows.append(
            _Row(
                stream=stream,
                display=parse_stream_display(stream.name, stream.title),
                headers=dict(stream.proxy_headers),
                over_budget=bool(_at(over_budget, i, False)),
                oversized=bool(_at(oversized, i, False)),
                reason=str(_at(reasons, i, "")),
                key=(*label, occurrence),
            )
        )
    return rows


class StreamListModel(QAbstractListModel):
    NameRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    UrlRole = Qt.ItemDataRole.UserRole + 3
    ResolutionRole = Qt.ItemDataRole.UserRole + 4
    InstantRole = Qt.ItemDataRole.UserRole + 5
    TagsRole = Qt.ItemDataRole.UserRole + 6
    StarsRole = Qt.ItemDataRole.UserRole + 7
    DetailRole = Qt.ItemDataRole.UserRole + 8
    SubtitleRole = Qt.ItemDataRole.UserRole + 9
    HeadersRole = Qt.ItemDataRole.UserRole + 10
    ExternalRole = Qt.ItemDataRole.UserRole + 11
    OverBudgetRole = Qt.ItemDataRole.UserRole + 12
    OversizedRole = Qt.ItemDataRole.UserRole + 13
    RecommendedRole = Qt.ItemDataRole.UserRole + 14
    ReasonRole = Qt.ItemDataRole.UserRole + 15
    SectionRole = Qt.ItemDataRole.UserRole + 16
    FilenameRole = Qt.ItemDataRole.UserRole + 17

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[_Row] = []

    def set_streams(
        self,
        streams: list[Stream],
        over_budget: Sequence[bool] = (),
        oversized: Sequence[bool] = (),
        reasons: Sequence[str] = (),
    ) -> None:
        """`over_budget`, `oversized` and `reasons` are positional against
        `streams` and may be empty, which means nothing is marked -- the state
        when the connection sort is off or nothing has been measured yet. A
        short sequence marks what it covers rather than raising: the flags are
        a hint on a row, not a guarantee the list depends on.

        A non-empty `reasons` entry is what makes a row recommended: the
        sentence and the mark are the same fact, so they cannot disagree.

        A list that shares rows with the one on screen is applied in place --
        rows that left are removed, new ones inserted, moved ones moved, and
        the rest updated -- instead of reset. That is the fresh answer landing
        over the stored one: a reset rebuilt every row and threw away the
        hover and the scroll position under the viewer's pointer, for a list
        that usually differs by a handful of rows. Rows are matched by label,
        never by URL: an aggregator mints a new URL for every source on every
        request (measured: 0 of 93 shared between two identical requests),
        while the labels match exactly.
        """
        rows = _rows(streams, over_budget, oversized, reasons)
        if not self._rows or not rows:
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
            return
        had_sections = any(row.reason for row in self._rows)
        self._apply_in_place(rows)
        if had_sections != any(row.reason for row in self._rows):
            # A section is a fact about the whole list (see _section), so it
            # can change on rows whose own data did not.
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, 0),
                [StreamListModel.SectionRole],
            )

    def _apply_in_place(self, target: list[_Row]) -> None:
        wanted = {row.key for row in target}
        # Removals first, back to front in contiguous runs, so every index the
        # views are told about is valid at the moment they are told it.
        row = len(self._rows) - 1
        while row >= 0:
            if self._rows[row].key in wanted:
                row -= 1
                continue
            last = row
            while row >= 0 and self._rows[row].key not in wanted:
                row -= 1
            self.beginRemoveRows(_ROOT_INDEX, row + 1, last)
            del self._rows[row + 1 : last + 1]
            self.endRemoveRows()
        # Then walk the target: every surviving row is somewhere at or after
        # its target position, so each step is keep, move up, or insert.
        for position, row_data in enumerate(target):
            current = self._rows[position].key if position < len(self._rows) else None
            if current == row_data.key:
                if self._rows[position] != row_data:
                    self._rows[position] = row_data
                    index = self.index(position, 0)
                    self.dataChanged.emit(index, index)
                continue
            found = next(
                (
                    i
                    for i in range(position + 1, len(self._rows))
                    if self._rows[i].key == row_data.key
                ),
                None,
            )
            if found is None:
                self.beginInsertRows(_ROOT_INDEX, position, position)
                self._rows.insert(position, row_data)
                self.endInsertRows()
                continue
            self.beginMoveRows(_ROOT_INDEX, found, found, _ROOT_INDEX, position)
            self._rows.insert(position, self._rows.pop(found))
            self.endMoveRows()
            if self._rows[position] != row_data:
                self._rows[position] = row_data
                index = self.index(position, 0)
                self.dataChanged.emit(index, index)

    def stream_at(self, row: int) -> Stream:
        return self._rows[row].stream

    def key_at(self, row: int) -> tuple[str, str, int]:
        """Which release `row` is, as rows are matched across two answers."""
        return self._rows[row].key

    def row_of(self, key: tuple[str, str, int]) -> int:
        """The row carrying `key` now, or -1."""
        return next((i for i, row in enumerate(self._rows) if row.key == key), -1)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        entry = self._rows[index.row()]
        stream = entry.stream
        display = entry.display
        match role:
            case StreamListModel.NameRole:
                return stream.name
            case StreamListModel.TitleRole:
                return stream.title
            case StreamListModel.UrlRole:
                # playable_url, not url: a ytId stream has no url but plays
                # fine through mpv's ytdl_hook.
                return stream.playable_url
            case StreamListModel.HeadersRole:
                return entry.headers
            case StreamListModel.FilenameRole:
                return stream.filename
            case StreamListModel.ExternalRole:
                # Only for streams with nothing playable in-app; a stream
                # offering both is played, not handed to a browser.
                return stream.external_url if stream.is_external else ""
            case StreamListModel.ResolutionRole:
                return display.resolution
            case StreamListModel.InstantRole:
                return display.instant
            case StreamListModel.TagsRole:
                return display.tags
            case StreamListModel.StarsRole:
                return display.stars
            case StreamListModel.DetailRole:
                return display.detail
            case StreamListModel.SubtitleRole:
                return display.subtitle
            case StreamListModel.OverBudgetRole:
                return entry.over_budget
            case StreamListModel.OversizedRole:
                return entry.oversized
            case StreamListModel.RecommendedRole:
                return bool(entry.reason)
            case StreamListModel.ReasonRole:
                return entry.reason
            case StreamListModel.SectionRole:
                return self._section(index.row())
        return None

    def _section(self, row: int) -> str:
        """The ListView section this row belongs to, or "" when the list has no
        sections at all.

        Empty for every row when nothing is recommended: a lone "All sources"
        heading over an unmarked list is a label, not a division, and it would
        appear on exactly the machines that have learned nothing yet.
        """
        if not any(entry.reason for entry in self._rows):
            return ""
        return "Recommended" if self._rows[row].reason else "All sources"

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            StreamListModel.NameRole: QByteArray(b"name"),
            StreamListModel.TitleRole: QByteArray(b"title"),
            StreamListModel.UrlRole: QByteArray(b"url"),
            StreamListModel.ResolutionRole: QByteArray(b"resolution"),
            StreamListModel.InstantRole: QByteArray(b"instant"),
            StreamListModel.TagsRole: QByteArray(b"tags"),
            StreamListModel.StarsRole: QByteArray(b"stars"),
            # Exposed as "extra", not "detail": a delegate that took its roles
            # implicitly would read "detail" as the row's text in place of the
            # Detail page's id. Delegates now declare `required property var
            # model`, but the name stays out of harm's way.
            StreamListModel.DetailRole: QByteArray(b"extra"),
            StreamListModel.SubtitleRole: QByteArray(b"subtitle"),
            StreamListModel.HeadersRole: QByteArray(b"headers"),
            StreamListModel.FilenameRole: QByteArray(b"filename"),
            StreamListModel.ExternalRole: QByteArray(b"external"),
            StreamListModel.OverBudgetRole: QByteArray(b"overBudget"),
            StreamListModel.OversizedRole: QByteArray(b"oversized"),
            StreamListModel.RecommendedRole: QByteArray(b"recommended"),
            StreamListModel.ReasonRole: QByteArray(b"reason"),
            StreamListModel.SectionRole: QByteArray(b"section"),
        }
