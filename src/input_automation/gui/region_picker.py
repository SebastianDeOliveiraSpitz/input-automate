"""Fullscreen region picker over a captured screen frame (T15).

Standard screen-region UX on Wayland: the dialog grabs ONE frame of the
real screen (mutter screencast first, mock fallback), shows it in a
fullscreen frameless overlay, and the user drags a rectangle on it. The
overlay only appears *after* the frame was captured, so it can never
photograph itself. The result is reported in *frame* pixel coordinates
``(x, y, w, h)`` — the same convention
:class:`~input_automation.vision.watch.WatchRule` and ``analyze --region``
use.

Headless-testable: pass ``frame=`` (static image) or ``frame_loader=``
(injectable grab callable) instead of a real backend.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPaintEvent, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from input_automation.capture.factory import grab_first_frame

__all__ = ["FrameCanvas", "Region", "RegionPickerDialog"]

Region = tuple[int, int, int, int]  # (x, y, w, h) in frame pixels


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

    def set_frame(self, frame: np.ndarray, *, max_width: int | None = None) -> None:
        """Display a BGR numpy frame, scaled down to ``max_width`` if wider."""
        height, width = frame.shape[:2]
        image = QImage(
            frame.data,
            width,
            height,
            int(frame.strides[0]),
            QImage.Format.Format_BGR888,
        )
        display_width = width if max_width is None else min(width, max_width)
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
    """Pick a rectangular region on a captured screen frame.

    Lifecycle: on show, ONE frame is grabbed (``frame_loader`` or the
    ``frame`` argument) and displayed fullscreen; dragging a rectangle
    selects the region in frame pixels. Esc/Cancel aborts, OK confirms.
    """

    def __init__(
        self,
        *,
        frame: np.ndarray | None = None,
        frame_loader: Callable[[], np.ndarray] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick region")
        self.region: Region | None = None
        self._frame = frame
        self._frame_loader = frame_loader
        self._loaded = False

        # Frameless fullscreen overlay showing the captured screen.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Dialog
        )
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = FrameCanvas()
        layout.addWidget(self._canvas, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 4, 8, 4)
        self._info = QLabel("Drag a rectangle on the screen. Esc cancels.")
        self._copy = QPushButton("Copy x,y,w,h")
        self._ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        self._ok.setDefault(True)
        bar.addWidget(self._info, 1)
        bar.addWidget(self._copy)
        bar.addWidget(self._ok)
        bar.addWidget(cancel)
        layout.addLayout(bar)

        self._ok.setEnabled(False)
        self._ok.clicked.connect(self._confirm)
        cancel.clicked.connect(self.reject)
        self._copy.clicked.connect(self._copy_region)
        self._canvas.on_release = self._update_info

    # -- frame supply --------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt naming
        """Grab the frame once, then cover the screen with it."""
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self._load_frame()

    def _load_frame(self) -> None:
        """Fetch and display the frame; a failure stays visible and abortable."""
        try:
            if self._frame is not None:
                frame = self._frame
            elif self._frame_loader is not None:
                frame = self._frame_loader()
            else:
                frame = grab_first_frame().image
        except Exception as exc:  # noqa: BLE001 - shown, dialog stays open
            self._info.setText(
                f"Screen capture failed: {exc!r} — Esc cancels; retry from "
                "the main window."
            )
            return
        screen = QApplication.primaryScreen()
        max_width = screen.size().width() if screen is not None else None
        self._canvas.set_frame(frame, max_width=max_width)
        self.showFullScreen()
        self._update_info()

    # -- actions -------------------------------------------------------------

    def _update_info(self) -> None:
        region = self._canvas.selection()
        if region is None:
            self._info.setText("Drag a rectangle on the screen. Esc cancels.")
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
