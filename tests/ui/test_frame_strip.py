"""Tests for the frame strip (T016, REQ-REV-01)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QMenu

from screen_copier.models import (
    CaptureMode,
    Frame,
    FrameQuality,
    FrameResult,
    FrameStatus,
)
from screen_copier.ui.frame_strip import (
    LABEL_DISCARD,
    LABEL_RERECOGNIZE,
    LABEL_RETRY,
    THUMB_SIZE,
    FrameListModel,
    FrameStrip,
    FrameViewerDialog,
)

IMAGE_SIZES = {1: (320, 200), 2: (400, 240), 3: (360, 180)}


class StubController(QObject):
    """Has the controller's frame signals and records slot calls."""

    frame_added = Signal(object)
    frame_updated = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, int]] = []

    def discard_frame(self, seq: int) -> None:
        self.calls.append(("discard_frame", seq))

    def retry_frame(self, seq: int) -> None:
        self.calls.append(("retry_frame", seq))

    def rerecognize_frame(self, seq: int) -> None:
        self.calls.append(("rerecognize_frame", seq))


def make_frame(seq: int, status: FrameStatus, *, blurry: bool = False) -> Frame:
    result = None
    if status is FrameStatus.RECOGNIZED:
        result = FrameResult(has_line_numbers=False, word_wrap_detected=False, lines=[])
    return Frame(
        seq=seq,
        captured_at=datetime(2026, 11, 13, tzinfo=UTC),
        mode=CaptureMode.MANUAL,
        image_file=Frame.image_file_for(seq),
        quality=FrameQuality(sharpness=10.0 if blurry else 200.0, blurry=blurry),
        status=status,
        error="boom" if status is FrameStatus.FAILED else None,
        result=result,
    )


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    frames = tmp_path / "frames"
    frames.mkdir()
    for seq, (w, h) in IMAGE_SIZES.items():
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(30 * seq, 80, 120))
        assert img.save(str(frames / f"{seq}.jpg"), "JPG")
    return tmp_path


@pytest.fixture
def controller() -> StubController:
    return StubController()


@pytest.fixture
def strip(qtbot, controller: StubController, session_dir: Path):
    widget = FrameStrip(controller, session_dir)
    qtbot.addWidget(widget)
    widget.resize(800, widget.height())
    widget.show()
    qtbot.waitExposed(widget)
    yield widget
    widget.wait_for_thumbnails()
    if widget.viewer is not None:
        widget.viewer.wait()


@pytest.fixture
def three(strip: FrameStrip, controller: StubController, qtbot) -> FrameStrip:
    # Emitted out of order on purpose: the strip must sort by seq.
    controller.frame_added.emit(make_frame(3, FrameStatus.FAILED))
    controller.frame_added.emit(make_frame(1, FrameStatus.QUEUED))
    controller.frame_added.emit(make_frame(2, FrameStatus.RECOGNIZED))
    return strip


def data(strip: FrameStrip, seq: int, role: int):
    return strip.index_of(seq).data(role)


def menu_texts(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def action(menu: QMenu, text: str):
    return next(a for a in menu.actions() if a.text() == text)


# -------------------------------------------------------------------------- AC 1


def test_three_frames_in_seq_order_with_badges(three: FrameStrip) -> None:
    model = three.frame_model
    assert model.rowCount() == 3
    seqs = [model.index(r).data(FrameListModel.SeqRole) for r in range(3)]
    assert seqs == [1, 2, 3]
    texts = [model.index(r).data(FrameListModel.BadgeTextRole) for r in range(3)]
    assert texts == ["Queued", "Recognized", "Failed"]

    colors = set()
    shapes = set()
    for r in range(3):
        badges = model.index(r).data(FrameListModel.BadgesRole)
        assert len(badges) == 1
        badge = badges[0]
        assert isinstance(badge.icon, QIcon)
        assert not badge.icon.isNull()
        assert isinstance(badge.color, QColor) and badge.color.isValid()
        colors.add(badge.color.name())
        shapes.add(badge.shape)
    # Each status is distinguishable by color and by icon shape (REQ-NFR-08).
    assert len(colors) == 3
    assert len(shapes) == 3


def test_strip_is_horizontal_icon_list(strip: FrameStrip) -> None:
    from PySide6.QtWidgets import QListView

    assert strip.viewMode() is QListView.ViewMode.IconMode
    assert strip.flow() is QListView.Flow.LeftToRight
    assert strip.frame_model.rowCount() == 0  # empty state


def test_thumbnails_load_off_the_gui_thread(three: FrameStrip, qtbot) -> None:
    assert three.wait_for_thumbnails()
    qtbot.waitUntil(
        lambda: all(data(three, s, FrameListModel.ThumbnailRole) is not None for s in (1, 2, 3))
    )
    pix = data(three, 2, FrameListModel.ThumbnailRole)
    assert pix.width() <= THUMB_SIZE.width() and pix.height() <= THUMB_SIZE.height()
    three.grab()  # the delegate paints without errors


def test_missing_image_leaves_placeholder(strip: FrameStrip, controller, qtbot) -> None:
    controller.frame_added.emit(make_frame(9, FrameStatus.QUEUED))
    assert strip.wait_for_thumbnails()
    qtbot.wait(20)
    assert data(strip, 9, FrameListModel.ThumbnailRole) is None
    strip.grab()


# -------------------------------------------------------------------------- AC 2 + AC 8


def test_context_menu_retry_only_for_failed(three: FrameStrip) -> None:
    failed = three.build_context_menu(3)
    assert LABEL_RETRY in menu_texts(failed)

    recognized = three.build_context_menu(2)
    texts = menu_texts(recognized)
    assert "Retry" not in texts
    assert "Discard" in texts
    assert "Recognize again" in texts
    assert action(recognized, LABEL_RERECOGNIZE).isEnabled()

    queued = three.build_context_menu(1)
    assert "Retry" not in menu_texts(queued)


def test_context_menu_actions_call_controller(three: FrameStrip, controller) -> None:
    action(three.build_context_menu(3), LABEL_RETRY).trigger()
    action(three.build_context_menu(2), LABEL_RERECOGNIZE).trigger()
    assert controller.calls == [("retry_frame", 3), ("rerecognize_frame", 2)]
    action(three.build_context_menu(1), LABEL_DISCARD).trigger()
    assert controller.calls[-1] == ("discard_frame", 1)


def test_context_menu_event_opens_menu_on_item(three: FrameStrip, qtbot, monkeypatch) -> None:
    shown: list[list[str]] = []
    monkeypatch.setattr(three, "_exec_menu", lambda menu, _pos: shown.append(menu_texts(menu)))
    from PySide6.QtGui import QContextMenuEvent

    rect = three.visualRect(three.index_of(3))
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, rect.center(), three.viewport().mapToGlobal(rect.center())
    )
    three.contextMenuEvent(event)
    assert shown == [[LABEL_DISCARD, LABEL_RETRY, LABEL_RERECOGNIZE]]
    assert three.selected_seq() == 3


# -------------------------------------------------------------------------- AC 3


def test_delete_key_discards_selected_frame(three: FrameStrip, controller, qtbot) -> None:
    three.select_seq(2)
    three.setFocus()
    qtbot.keyClick(three, Qt.Key.Key_Delete)
    assert controller.calls == [("discard_frame", 2)]


def test_delete_without_selection_does_nothing(three: FrameStrip, controller, qtbot) -> None:
    three.clearSelection()
    qtbot.keyClick(three, Qt.Key.Key_Delete)
    assert controller.calls == []


# -------------------------------------------------------------------------- AC 4


def test_double_click_opens_full_size_viewer(three: FrameStrip, qtbot) -> None:
    rect = three.visualRect(three.index_of(2))
    # A real double click is press/release then a double-click event; QTest sends only the latter.
    qtbot.mouseClick(three.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qtbot.mouseDClick(three.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    viewer = three.viewer
    assert isinstance(viewer, FrameViewerDialog)
    assert viewer.seq == 2
    assert viewer.isVisible()
    assert viewer.wait()
    w, h = IMAGE_SIZES[2]
    qtbot.waitUntil(lambda: viewer.image_size().width() == w)
    assert viewer.image_size().height() == h
    viewer.close()


# -------------------------------------------------------------------------- AC 5


def test_recognizing_shows_spinner_and_discarded_is_dimmed(
    three: FrameStrip, controller, qtbot
) -> None:
    assert data(three, 1, FrameListModel.SpinnerRole) is False
    assert not three.frame_model.spinner_running()

    controller.frame_updated.emit(make_frame(1, FrameStatus.RECOGNIZING))
    assert three.frame_model.rowCount() == 3
    assert data(three, 1, FrameListModel.SpinnerRole) is True
    badge = data(three, 1, FrameListModel.BadgesRole)[0]
    assert badge.shape == "spinner"
    assert badge.text == "Recognizing"
    assert three.frame_model.spinner_running()
    assert data(three, 1, FrameListModel.DimmedRole) is False
    three.grab()

    controller.frame_updated.emit(make_frame(1, FrameStatus.DISCARDED))
    assert data(three, 1, FrameListModel.SpinnerRole) is False
    assert data(three, 1, FrameListModel.DimmedRole) is True
    assert data(three, 1, FrameListModel.BadgeTextRole) == "Discarded"
    assert not three.frame_model.spinner_running()
    three.grab()


# -------------------------------------------------------------------------- AC 6


def test_blurry_frame_also_shows_blurry_badge(strip: FrameStrip, controller) -> None:
    controller.frame_added.emit(make_frame(1, FrameStatus.QUEUED, blurry=True))
    badges = data(strip, 1, FrameListModel.BadgesRole)
    assert [b.text for b in badges] == ["Queued", "Blurry"]
    assert not badges[1].icon.isNull()
    assert badges[1].color != badges[0].color
    assert "Blurry" in data(strip, 1, FrameListModel.BadgeTextRole)


# -------------------------------------------------------------------------- AC 7


def test_click_emits_frame_selected(three: FrameStrip, qtbot) -> None:
    rect = three.visualRect(three.index_of(3))
    with qtbot.waitSignal(three.frame_selected, timeout=1000) as blocker:
        qtbot.mouseClick(three.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert blocker.args == [3]

    rect = three.visualRect(three.index_of(1))
    with qtbot.waitSignal(three.frame_selected, timeout=1000) as blocker:
        qtbot.mouseClick(three.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert blocker.args == [1]


def test_set_session_dir_clears(three: FrameStrip, tmp_path: Path) -> None:
    three.set_session_dir(tmp_path / "other")
    assert three.frame_model.rowCount() == 0
