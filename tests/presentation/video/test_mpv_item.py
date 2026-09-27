"""The mpv render callback outlives the video item, so it must survive it.

QML deletes MpvVideoItem's C++ object when the player page is popped, but the
mpv core lives on in PlayerController -- its render thread keeps firing
update_cb until the renderer is torn down on the render thread, some frames
later. Every one of those calls lands on a deleted item.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from shiboken6 import delete, isValid

from gravitas.presentation.video.mpv_item import MpvVideoItem


def test_schedule_update_repaints_a_live_item(qapp: object) -> None:
    item = MpvVideoItem()
    repaints: list[int] = []
    item.update = lambda: repaints.append(1)  # type: ignore[method-assign]

    item.scheduleUpdate()
    # Queued: nothing happens until the GUI thread's event loop turns.
    assert repaints == []
    QCoreApplication.processEvents()

    assert repaints == [1]


def test_schedule_update_after_item_deleted_does_not_raise(qapp: object) -> None:
    item = MpvVideoItem()
    delete(item)
    assert not isValid(item)

    # mpv's render thread calls this bound method; it must no-op rather than
    # raise "Internal C++ object (MpvVideoItem) already deleted".
    item.scheduleUpdate()
    QCoreApplication.processEvents()


def test_pending_repaint_drops_when_item_deleted_before_delivery(qapp: object) -> None:
    """The real race: mpv schedules, QML deletes, then the event is delivered."""
    item = MpvVideoItem()
    item.scheduleUpdate()

    delete(item)
    QCoreApplication.processEvents()
