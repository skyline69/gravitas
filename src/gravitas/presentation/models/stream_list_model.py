"""Qt list model exposing resolved streams to the QML source picker.

Addon stream labels are free-form strings ("4K ⚡ ⟨Web-dl⟩ ★★"); the model
splits the recognisable tokens (resolution, instant marker, bracket tags,
star rating) into their own roles so the UI can render chips instead of a
wall of symbols. Unrecognised text survives in the detail role.
"""

from __future__ import annotations

import re
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

    def __init__(self) -> None:
        super().__init__()
        self._streams: list[Stream] = []
        self._display: list[StreamDisplay] = []

    def set_streams(self, streams: list[Stream]) -> None:
        self.beginResetModel()
        self._streams = list(streams)
        self._display = [parse_stream_display(s.name, s.title) for s in self._streams]
        self.endResetModel()

    def stream_at(self, row: int) -> Stream:
        return self._streams[row]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._streams)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        stream = self._streams[index.row()]
        display = self._display[index.row()]
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
                return dict(stream.proxy_headers)
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
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            StreamListModel.NameRole: QByteArray(b"name"),
            StreamListModel.TitleRole: QByteArray(b"title"),
            StreamListModel.UrlRole: QByteArray(b"url"),
            StreamListModel.ResolutionRole: QByteArray(b"resolution"),
            StreamListModel.InstantRole: QByteArray(b"instant"),
            StreamListModel.TagsRole: QByteArray(b"tags"),
            StreamListModel.StarsRole: QByteArray(b"stars"),
            # Exposed as "extra", not "detail": delegates receive role names as
            # bare context properties, and "detail" shadows the Detail page id.
            StreamListModel.DetailRole: QByteArray(b"extra"),
            StreamListModel.SubtitleRole: QByteArray(b"subtitle"),
            StreamListModel.HeadersRole: QByteArray(b"headers"),
            StreamListModel.ExternalRole: QByteArray(b"external"),
        }
