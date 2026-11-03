"""Interactive visual region picker over live frames (T15).

The user drags a rectangle on the live frame; the picker reports the
rectangle in *frame* pixel coordinates ``(x, y, w, h)`` — the same
coordinate convention :class:`~input_automation.vision.watch.WatchRule`
and the ``analyze --region`` CLI option use.

Headless-testable: frames come from an injectable ``get_frame`` callable
(supply a static image in tests, or a
:class:`~input_automation.capture.base.CaptureBackend` for live use).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from input_automation.capture.base import CaptureBackend

__all__ = ["Region", "RegionPickerDialog"]

Region = tuple[int, int, int, int]  # (x, y, w, h) in frame pixels

_MAX_DISPLAY_WIDTH = 800  # px; frames are scaled down to fit, if wider
_REFRESH_MS = 150


class FrameCanvas(QLabel):
    """Shows one frame and tracks the user's rubber-band selection."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(False)
        self._scale: float = 1.0
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self.on_release: Callable[[], None] | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    # -- frame handling ------------------------------------------------------

    def set_frame(self, frame: np.ndarray) -> None:
        """Display a BGR numpy frame, scaled down if it is very wide."""
        height, width = frame.shape[:2]
        image = QImage(
            frame.data,
            width,
            height,
            int(frame.strides[0]),
            QImage.Format.Format_BGR888,
        )
        display_width = min(width, _MAX_DISPLAY_WIDTH)
        self._scale = display_width / width
        self.setPixmap(
            QPixmap.fromImage(image).scaledToWidth(
                display_width, Qt.TransformationMode.SmoothTransformation
            )
        )
        self.setFixedSize(self.pixmap().size())
        self.update()

    def widget_to_frame(self, point: QPoint) -> QPoint:
        """Map a widget-local point to frame pixel coordinates."""
        return QPoint(int(point.x() / self._scale), int(point.y() / self._scale))

    # -- selection -----------------------------------------------------------

    def selection(self) -> Region | None:
        """The current selection in frame coordinates, or None."""
        if self._origin is None or self._current is None:
            return None
        a, b = self.widget_to_frame(self._origin), self.widget_to_frame(self._current)
        x, x2 = sorted((a.x(), b.x()))
        y, y2 = sorted((a.y(), b.y()))
        if x2 - x < 1 or y2 - y < 1:
            return None
        return (x, y, x2 - x, y2 - y)

    def clear_selection(self) -> None:
        self._origin = self._current = None
        self.update()

    # -- mouse ---------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = self._current = event.position().toPoint()
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._origin is not None and event.button() == Qt.MouseButton.LeftButton:
            self._current = event.position().toPoint()
            self.update()
        if self.on_release is not None:
            self.on_release()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._origin is None or self._current is None:
            return
        painter = QPainter(self)
        pen = painter.pen()
        pen.setColor(Qt.GlobalColor.red)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(QRect(self._origin, self._current))
        painter.end()


class RegionPickerDialog(QDialog):
    """Pick a rectangular region on live frames; result in frame pixels."""

    def __init__(
        self,
        *,
        backend: CaptureBackend | None = None,
        get_frame: Callable[[], np.ndarray | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick region")
        self.resize(860, 640)
        self.region: Region | None = None
        self._backend = backend
        self._get_frame = get_frame

        layout = QVBoxLayout(self)
        self._canvas = FrameCanvas()
        layout.addWidget(self._canvas)
        self._info = QLabel("Drag a rectangle on the frame.")
        layout.addWidget(self._info)
        buttons = QHBoxLayout()
        self._copy = QPushButton("Copy x,y,w,h")
        self._ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        self._ok.setDefault(True)
        buttons.addWidget(self._copy)
        buttons.addStretch(1)
        buttons.addWidget(self._ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._confirm)
        cancel.clicked.connect(self.reject)
        self._copy.clicked.connect(self._copy_region)
        self._canvas.on_release = self._update_info

        if backend is not None:
            backend.start()
            self._timer = QTimer(self)
            self._timer.setInterval(_REFRESH_MS)
            self._timer.timeout.connect(self._refresh_frame)
            self._timer.start()

    # -- frame supply --------------------------------------------------------

    def _refresh_frame(self) -> None:
        """Grab the latest frame (live backends) and show it."""
        frame = self._next_frame()
        if frame is not None:
            self._canvas.set_frame(frame)
            self._update_info()

    def _next_frame(self) -> np.ndarray | None:
        if self._get_frame is not None:
            return self._get_frame()
        if self._backend is not None:
            try:
                return self._backend.grab(timeout=0.5).image
            except Exception:  # noqa: BLE001 - picker keeps retrying
                return None
        return None

    def set_static_frame(self, frame: np.ndarray) -> None:
        """Show one fixed frame (used by tests and offline picking)."""
        self._canvas.set_frame(frame)
        self._update_info()

    # -- actions -------------------------------------------------------------

    def _update_info(self) -> None:
        region = self._canvas.selection()
        if region is None:
            self._info.setText("Drag a rectangle on the frame.")
            self._ok.setEnabled(False)
        else:
            x, y, w, h = region
            self._info.setText(f"Region: {x},{y},{w},{h}")
            self._ok.setEnabled(True)

    def _confirm(self) -> None:
        self.region = self._canvas.selection()
        if self.region is not None:
            self.accept()

    def _copy_region(self) -> None:
        region = self._canvas.selection()
        if region is None:
            return
        x, y, w, h = region
        QApplication.clipboard().setText(f"{x},{y},{w},{h}")
        self._info.setText(f"Copied {x},{y},{w},{h} to clipboard.")

    def accept(self) -> None:
        self._shutdown()
        super().accept()

    def reject(self) -> None:
        self._shutdown()
        super().reject()

    def _shutdown(self) -> None:
        if self._backend is not None:
            self._backend.stop()
            self._backend = None
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
