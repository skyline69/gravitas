"""QObject bridge: load meta + streams for a selected item; expose meta to QML."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.get_detail import GetDetail
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.errors import GravitasError, NoStreams
from gravitas.domain.models import MediaType, MetaDetail
from gravitas.presentation.models.stream_list_model import StreamListModel


class DetailController(QObject):
    errorOccurred = Signal(str)
    metaChanged = Signal()

    def __init__(
        self,
        get_detail: GetDetail,
        resolve_stream: ResolveStream,
        stream_model: StreamListModel,
    ) -> None:
        super().__init__()
        self._get_detail = get_detail
        self._resolve_stream = resolve_stream
        self._stream_model = stream_model
        self._meta: MetaDetail | None = None
        # bumped on every load(); an in-flight load whose token no longer
        # matches is stale (a newer selection started) and must not apply its
        # result — otherwise a slow earlier load clobbers a newer one.
        self._seq = 0

    @Property(bool, notify=metaChanged)
    def hasMeta(self) -> bool:
        return self._meta is not None

    @Property(str, notify=metaChanged)
    def title(self) -> str:
        return self._meta.name if self._meta else ""

    @Property(str, notify=metaChanged)
    def description(self) -> str:
        return (self._meta.description or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def poster(self) -> str:
        return (self._meta.poster or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def background(self) -> str:
        return (self._meta.background or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def logo(self) -> str:
        return (self._meta.logo or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def year(self) -> str:
        return (self._meta.year or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def runtime(self) -> str:
        return (self._meta.runtime or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def imdbRating(self) -> str:
        return (self._meta.imdb_rating or "") if self._meta else ""

    def _genres(self) -> list[str]:
        return list(self._meta.genres) if self._meta else []

    def _cast(self) -> list[str]:
        return list(self._meta.cast) if self._meta else []

    def _directors(self) -> list[str]:
        return list(self._meta.directors) if self._meta else []

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def genres(self) -> list[str]:
        return self._genres()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def cast(self) -> list[str]:
        return self._cast()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def directors(self) -> list[str]:
        return self._directors()

    @asyncSlot(str, str)  # type: ignore[untyped-decorator]
    async def load(self, type: str, item_id: str) -> None:
        media_type: MediaType = "series" if type == "series" else "movie"
        self._seq += 1
        token = self._seq
        # reset immediately so the new page never flashes the previous item's
        # meta/streams while this load is in flight
        self._meta = None
        self.metaChanged.emit()
        self._stream_model.set_streams([])
        try:
            meta = await self._get_detail(media_type, item_id)
            if token != self._seq:
                return  # a newer load started; drop this stale result
            self._meta = meta
            self.metaChanged.emit()
            streams = await self._resolve_stream(media_type, item_id)
            if token != self._seq:
                return
            self._stream_model.set_streams(streams)
        except NoStreams:
            # a normal empty state (no stream addon configured, or none for
            # this title) — leave Sources empty, don't raise a red error toast
            pass
        except GravitasError as exc:
            if token == self._seq:
                self.errorOccurred.emit(str(exc))
