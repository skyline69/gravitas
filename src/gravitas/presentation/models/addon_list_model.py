"""Qt list model exposing installed addons to the Settings page."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.domain.models import AddonManifest

_ROOT_INDEX = QModelIndex()


class AddonListModel(QAbstractListModel):
    NameRole = Qt.ItemDataRole.UserRole + 1
    IdRole = Qt.ItemDataRole.UserRole + 2
    RemovableRole = Qt.ItemDataRole.UserRole + 3
    VersionRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self) -> None:
        super().__init__()
        # (name, id, removable, version)
        self._rows: list[tuple[str, str, bool, str]] = []

    def set_addons(self, manifests: list[AddonManifest], protected_ids: set[str]) -> None:
        self.beginResetModel()
        self._rows = [(m.name, m.id, m.id not in protected_ids, m.version) for m in manifests]
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
        name, addon_id, removable, version = self._rows[index.row()]
        match role:
            case AddonListModel.NameRole:
                return name
            case AddonListModel.IdRole:
                return addon_id
            case AddonListModel.RemovableRole:
                return removable
            case AddonListModel.VersionRole:
                return version
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            AddonListModel.NameRole: QByteArray(b"name"),
            AddonListModel.IdRole: QByteArray(b"addonId"),
            AddonListModel.RemovableRole: QByteArray(b"removable"),
            AddonListModel.VersionRole: QByteArray(b"version"),
        }
