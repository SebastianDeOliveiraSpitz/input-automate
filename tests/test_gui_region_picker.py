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
        assert canvas._scale == 1.0  # 300 px frame fits under the 800 px cap

    def test_wide_frame_is_scaled(self, qapp) -> None:  # type: ignore[no-untyped-def]
        canvas = FrameCanvas()
        frame = np.zeros((100, 1600, 3), dtype=np.uint8)
        canvas.set_frame(frame)
        assert canvas._scale == 800 / 1600

    def test_drag_produces_frame_coordinates(self, qapp) -> None:  # type: ignore[no-untyped-def]
        canvas = FrameCanvas()
        canvas.set_frame(_frame())
        _drag(canvas, 10, 20, 60, 90)
        assert canvas.selection() == (10, 20, 50, 70)

    def test_drag_maps_back_from_display_scale(self, qapp) -> None:  # type: ignore[no-untyped-def]
        """A 1600px frame shows at 800px; widget coords halve into frame coords."""
        canvas = FrameCanvas()
        canvas.set_frame(np.zeros((100, 1600, 3), dtype=np.uint8))
        _drag(canvas, 20, 10, 60, 50)  # widget coords
        assert canvas.selection() == (40, 20, 80, 80)  # frame coords

    def test_small_drag_is_no_selection(self, qapp) -> None:  # type: ignore[no-untyped-def]
        canvas = FrameCanvas()
        canvas.set_frame(_frame())
        _drag(canvas, 5, 5, 5, 5)
        assert canvas.selection() is None


class TestRegionPickerDialog:
    def test_ok_disabled_then_enabled(self, qapp) -> None:  # type: ignore[no-untyped-def]
        picker = RegionPickerDialog(get_frame=lambda: _frame())
        picker.set_static_frame(_frame())
        assert not picker._ok.isEnabled()
        _drag(picker._canvas, 5, 5, 45, 25)
        assert picker._ok.isEnabled()
        assert picker._canvas.selection() == (5, 5, 40, 20)

    def test_confirm_sets_region_and_accepts(self, qapp) -> None:  # type: ignore[no-untyped-def]
        picker = RegionPickerDialog(get_frame=lambda: _frame())
        picker.set_static_frame(_frame())
        _drag(picker._canvas, 0, 0, 100, 50)
        picker._confirm()
        assert picker.result() == picker.DialogCode.Accepted
        assert picker.region == (0, 0, 100, 50)

    def test_copy_writes_clipboard(self, qapp) -> None:  # type: ignore[no-untyped-def]
        picker = RegionPickerDialog(get_frame=lambda: _frame())
        picker.set_static_frame(_frame())
        _drag(picker._canvas, 7, 8, 17, 28)
        picker._copy_region()
        assert QApplication.clipboard().text() == "7,8,10,20"

    def test_reject_stops_backend(self, qapp) -> None:  # type: ignore[no-untyped-def]
        backend = MockCaptureBackend()
        picker = RegionPickerDialog(backend=backend)
        picker._shutdown()
        assert picker._backend is None
        picker._shutdown()  # second shutdown is a no-op

    def test_live_frames_appear(self, qapp) -> None:  # type: ignore[no-untyped-def]
        backend = MockCaptureBackend()
        picker = RegionPickerDialog(backend=backend)
        try:
            picker._refresh_frame()
            assert not picker._canvas.pixmap().isNull()
        finally:
            picker._shutdown()
