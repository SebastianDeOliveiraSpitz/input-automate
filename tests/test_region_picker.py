"""Tests for the region picker dialog (issue #18, Layer 1).

Headless: constructs the dialog with a fake frame or an injected grabber so no
real screen grab / display is needed. Runs under QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QDialog

from input_automation.gui.region_picker import PixelPickerDialog, RegionPickerDialog


def _frame() -> np.ndarray:
    return np.full((100, 100, 3), 255, dtype=np.uint8)


def _fire(dialog: RegionPickerDialog, kind: QEvent.Type, pos: QPoint) -> None:
    event = QMouseEvent(
        kind,
        QPointF(pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    {
        QEvent.Type.MouseButtonPress: dialog.mousePressEvent,
        QEvent.Type.MouseMove: dialog.mouseMoveEvent,
        QEvent.Type.MouseButtonRelease: dialog.mouseReleaseEvent,
    }[kind](event)


def test_picker_constructs_offscreen_with_fake_frame(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = RegionPickerDialog(frame=_frame())
    qtbot.addWidget(dialog)
    assert dialog.region() is None


def test_picker_uses_injected_grabber_instead_of_real_grab(
    qtbot,
) -> None:  # type: ignore[no-untyped-def]
    grabbed = {"called": False}

    def grabber() -> np.ndarray:
        grabbed["called"] = True
        return np.full((50, 50, 3), 128, dtype=np.uint8)

    dialog = RegionPickerDialog(grabber=grabber)
    qtbot.addWidget(dialog)
    assert grabbed["called"]
    assert dialog.region() is None


def test_drag_reports_region_in_frame_pixels(qtbot) -> None:  # type: ignore[no-untyped-def]
    """A drag over a 2x-scaled 100x100 frame reports image-pixel coords."""
    dialog = RegionPickerDialog(frame=_frame())
    qtbot.addWidget(dialog)
    dialog.resize(200, 200)  # 1 widget pixel = 0.5 frame pixel
    _fire(dialog, QEvent.Type.MouseButtonPress, QPoint(20, 20))
    _fire(dialog, QEvent.Type.MouseMove, QPoint(60, 100))
    _fire(dialog, QEvent.Type.MouseButtonRelease, QPoint(60, 100))
    region = dialog.region()
    assert region is not None
    # widget (20,20)->(10,10), widget (60,100)->(30,50) in frame pixels
    assert region == QRect(10, 10, 20, 40)


def test_accept_without_selection_is_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = RegionPickerDialog(frame=_frame())
    qtbot.addWidget(dialog)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_reject_clears_selection(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = RegionPickerDialog(frame=_frame())
    qtbot.addWidget(dialog)
    dialog.resize(200, 200)
    _fire(dialog, QEvent.Type.MouseButtonPress, QPoint(20, 20))
    _fire(dialog, QEvent.Type.MouseButtonRelease, QPoint(60, 60))
    assert dialog.region() is not None
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize("size", [(1, 1), (100, 100)])
def test_picker_handles_tiny_and_full_frame(
    qtbot, size: tuple[int, int]
) -> None:  # type: ignore[no-untyped-def]
    dialog = RegionPickerDialog(frame=np.zeros((*size, 3), dtype=np.uint8))
    qtbot.addWidget(dialog)
    assert dialog.region() is None


# -- pixel picker ----------------------------------------------------------


def _colored_frame() -> np.ndarray:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[30, 40] = (10, 20, 30)  # BGR at x=40, y=30
    return frame


def test_pixel_picker_constructs_offscreen_with_fake_frame(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = PixelPickerDialog(frame=_colored_frame())
    qtbot.addWidget(dialog)
    assert dialog.pixel() is None


def test_pixel_picker_uses_injected_grabber_instead_of_real_grab(qtbot) -> None:  # type: ignore[no-untyped-def]
    grabbed = {"called": False}

    def grabber() -> np.ndarray:
        grabbed["called"] = True
        return _colored_frame()

    dialog = PixelPickerDialog(grabber=grabber)
    qtbot.addWidget(dialog)
    assert grabbed["called"]
    assert dialog.pixel() is None


def test_pixel_picker_reports_clicked_pixel(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = PixelPickerDialog(frame=_colored_frame())
    qtbot.addWidget(dialog)
    dialog.resize(200, 200)  # 1 widget pixel = 0.5 frame pixel

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(80, 60),  # widget (80, 60) -> frame (40, 30)
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    dialog.mousePressEvent(event)

    assert dialog.pixel() == (40, 30, 10, 20, 30)


def test_pixel_picker_accept_requires_click(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = PixelPickerDialog(frame=_colored_frame())
    qtbot.addWidget(dialog)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.pixel() is None
