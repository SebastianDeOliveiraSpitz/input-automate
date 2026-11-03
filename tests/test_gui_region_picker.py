"""Tests for the region picker (T15). Runs with QT_QPA_PLATFORM=offscreen.

Mouse simulation goes through explicit QMouseEvent delivery — QTest's
synthetic mouse positions are not reliable on the offscreen platform.
The deprecated QMouseEvent overload is intentional (device-free and
deterministic); its warning is silenced for this module.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from input_automation.capture.base import CaptureError  # noqa: E402
from input_automation.capture.mock import MockCaptureBackend  # noqa: E402
from input_automation.gui.region_picker import (  # noqa: E402
    FrameCanvas,
    RegionPickerDialog,
)


def _frame() -> np.ndarray:
    """A 300x200 solid blue frame (scale factor stays 1.0)."""
    image = np.zeros((200, 300, 3), dtype=np.uint8)
    image[:, :] = (255, 0, 0)  # BGR blue
    return image


def _mouse(widget, event_type: QEvent.Type, x: int, y: int) -> None:  # type: ignore[no-untyped-def]
    buttons = (
        Qt.MouseButton.LeftButton
        if event_type != QEvent.Type.MouseButtonRelease
        else Qt.MouseButton.NoButton
    )
    event = QMouseEvent(
        event_type,
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _drag(widget, x1: int, y1: int, x2: int, y2: int) -> None:  # type: ignore[no-untyped-def]
    _mouse(widget, QEvent.Type.MouseButtonPress, x1, y1)
    _mouse(widget, QEvent.Type.MouseMove, x2, y2)
    _mouse(widget, QEvent.Type.MouseButtonRelease, x2, y2)


class TestFrameCanvas:
    def test_shows_frame_and_keeps_scale(self, qapp) -> None:  # type: ignore[no-untyped-def]
        canvas = FrameCanvas()
        canvas.set_frame(_frame())
        assert canvas.pixmap() is not None and not canvas.pixmap().isNull()
        assert canvas._scale == 1.0  # no max_width -> native size

    def test_wide_frame_is_scaled(self, qapp) -> None:  # type: ignore[no-untyped-def]
        canvas = FrameCanvas()
        frame = np.zeros((100, 1600, 3), dtype=np.uint8)
        canvas.set_frame(frame, max_width=800)
        assert canvas._scale == 800 / 1600

    def test_drag_produces_frame_coordinates(self, qapp) -> None:  # type: ignore[no-untyped-def]
        canvas = FrameCanvas()
        canvas.set_frame(_frame())
        _drag(canvas, 10, 20, 60, 90)
        assert canvas.selection() == (10, 20, 50, 70)

    def test_drag_maps_back_from_display_scale(self, qapp) -> None:  # type: ignore[no-untyped-def]
        """A 1600px frame shown at 800px; widget coords halve into frame coords."""
        canvas = FrameCanvas()
        canvas.set_frame(np.zeros((100, 1600, 3), dtype=np.uint8), max_width=800)
        _drag(canvas, 20, 10, 60, 50)  # widget coords
        assert canvas.selection() == (40, 20, 80, 80)  # frame coords

    def test_small_drag_is_no_selection(self, qapp) -> None:  # type: ignore[no-untyped-def]
        canvas = FrameCanvas()
        canvas.set_frame(_frame())
        _drag(canvas, 5, 5, 5, 5)
        assert canvas.selection() is None


class TestRegionPickerDialog:
    def test_shows_static_frame_fullscreen(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        picker = RegionPickerDialog(frame=_frame())
        qtbot.addWidget(picker)
        picker.show()  # showEvent triggers the one-shot frame load
        assert not picker._canvas.pixmap().isNull()
        assert picker.isFullScreen()

    def test_ok_disabled_then_enabled(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        picker = RegionPickerDialog(frame=_frame())
        qtbot.addWidget(picker)
        picker._load_frame()
        assert not picker._ok.isEnabled()
        _drag(picker._canvas, 5, 5, 45, 25)
        assert picker._ok.isEnabled()
        assert picker._canvas.selection() == (5, 5, 40, 20)

    def test_confirm_sets_region_and_accepts(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        picker = RegionPickerDialog(frame=_frame())
        qtbot.addWidget(picker)
        picker._load_frame()
        _drag(picker._canvas, 0, 0, 100, 50)
        picker._confirm()
        assert picker.result() == picker.DialogCode.Accepted
        assert picker.region == (0, 0, 100, 50)

    def test_copy_writes_clipboard(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        picker = RegionPickerDialog(frame=_frame())
        qtbot.addWidget(picker)
        picker._load_frame()
        _drag(picker._canvas, 7, 8, 17, 28)
        picker._copy_region()
        assert QApplication.clipboard().text() == "7,8,10,20"

    def test_capture_failure_is_visible_not_silent(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        def boom() -> np.ndarray:
            raise CaptureError("no capture backend produced a frame (...)")

        picker = RegionPickerDialog(frame_loader=boom)
        qtbot.addWidget(picker)
        picker._load_frame()
        assert "Screen capture failed" in picker._info.text()
        assert picker._canvas.pixmap() is not None
        assert picker._canvas.pixmap().isNull()  # nothing was displayed

    def test_frame_loader_grabs_once(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        calls: list[int] = []
        frame = _frame()

        def loader() -> np.ndarray:
            calls.append(1)
            return frame

        picker = RegionPickerDialog(frame_loader=loader)
        qtbot.addWidget(picker)
        picker.show()
        picker.show()  # second show must not re-grab
        assert len(calls) == 1

    def test_real_backend_frame_reaches_canvas(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        backend = MockCaptureBackend()

        def loader() -> np.ndarray:
            return backend.grab().image

        picker = RegionPickerDialog(frame_loader=loader)
        qtbot.addWidget(picker)
        picker._load_frame()
        assert not picker._canvas.pixmap().isNull()
