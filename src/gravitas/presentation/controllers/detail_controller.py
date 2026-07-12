"""QObject bridge: load meta and resolve streams for a selected item."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.get_detail import GetDetail
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import AddonManifest, MediaType
from gravitas.presentation.models.stream_list_model import StreamListModel


class DetailController(QObject):
    errorOccurred = Signal(str)
    titleChanged = Signal(str)
    descriptionChanged = Signal(str)

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
        self._manifest: AddonManifest | None = None

    def bind_manifest(self, manifest: AddonManifest) -> None:
        self._manifest = manifest

    @asyncSlot(str, str)  # type: ignore[untyped-decorator]
    async def load(self, type: str, item_id: str) -> None:
        if self._manifest is None:
            self.errorOccurred.emit("no addon installed")
            return
        media_type: MediaType = "series" if type == "series" else "movie"
        try:
            meta = await self._get_detail(self._manifest, media_type, item_id)
            self.titleChanged.emit(meta.name)
            self.descriptionChanged.emit(meta.description or "")
            streams = await self._resolve_stream(self._manifest, media_type, item_id)
            self._stream_model.set_streams(streams)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
