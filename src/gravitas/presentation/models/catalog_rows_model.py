"""Qt list model exposing catalog rows, each with its own poster model, to QML."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.application.browse_catalog import CatalogRow
from gravitas.presentation.models.poster_grid_model import PosterGridModel

_ROOT_INDEX = QModelIndex()


class CatalogRowsModel(QAbstractListModel):
    TitleRole = Qt.ItemDataRole.UserRole + 1
    AddonIdRole = Qt.ItemDataRole.UserRole + 2
    TypeRole = Qt.ItemDataRole.UserRole + 3
    CatalogIdRole = Qt.ItemDataRole.UserRole + 4
    PostersRole = Qt.ItemDataRole.UserRole + 5

    def __init__(self) -> None:
        super().__init__()
        # (title, addon_id, type, catalog_id, poster_model). Holding the PosterGridModel
        # here keeps a Python reference alive so QML can bind it as an inner
        # ListView model without it being garbage-collected.
        self._rows: list[tuple[str, str, str, str, PosterGridModel]] = []

    def set_rows(self, rows: list[CatalogRow]) -> None:
        self.beginResetModel()
        built: list[tuple[str, str, str, str, PosterGridModel]] = []
        for row in rows:
            poster_model = PosterGridModel()
            poster_model.set_items(row.items)
            built.append((row.title, row.addon_id, row.type, row.catalog_id, poster_model))
        self._rows = built
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        title, addon_id, type_, catalog_id, posters = self._rows[index.row()]
        match role:
            case CatalogRowsModel.TitleRole:
                return title
            case CatalogRowsModel.AddonIdRole:
                return addon_id
            case CatalogRowsModel.TypeRole:
                return type_
            case CatalogRowsModel.CatalogIdRole:
                return catalog_id
            case CatalogRowsModel.PostersRole:
                return posters
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            CatalogRowsModel.TitleRole: QByteArray(b"title"),
            CatalogRowsModel.AddonIdRole: QByteArray(b"addonId"),
            CatalogRowsModel.TypeRole: QByteArray(b"type"),
            CatalogRowsModel.CatalogIdRole: QByteArray(b"catalogId"),
            CatalogRowsModel.PostersRole: QByteArray(b"posters"),
        }
