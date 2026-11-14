"""Full-screen region picker for the CAPTURE block (issue #18, Layer 1).

A modal dialog that paints one captured frame full-screen and lets the user
drag a rectangle. On confirm it reports the selection in **pixels of the
captured frame** so the CAPTURE block's REGION pin can be filled without
typing coordinates by hand.

Headless-safe: accepts a pre-captured frame (tests pass a fake frame, no real
screen grab) and never opens any input/output device. Painting uses palette
colors only (no hardcoded bright colors) so it fits the Adwaita theming
tracked in issue #20.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from input_automation.capture.factory import grab_first_frame

__all__ = ["RegionPickerDialog", "PixelPickerDialog"]


def _frame_to_qimage(frame: np.ndarray) -> QImage:
    """Convert a BGR numpy frame to an RGB QImage that owns its pixel data."""
    height, width, _channels = frame.shape
    bytes_per_line = 3 * width
    image = QImage(
        frame.data, width, height, bytes_per_line, QImage.Format.Format_RGB888
    )
    # rgbSwapped() deep-copies with R<->B swapped, so the numpy buffer can be
    # released once this returns.
    return image.rgbSwapped()


class RegionPickerDialog(QDialog):
    """Drag a rectangle over a full-screen frame; confirm returns image pixels."""

    def __init__(
        self,
        frame: np.ndarray | None = None,
        parent: QWidget | None = None,
        *,
        grabber: Callable[[], np.ndarray] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select region")
        self.setModal(True)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._grabber = grabber or (lambda: grab_first_frame().image)
        self._frame: np.ndarray | None = None
        self._grab_error: str | None = None
        if frame is None:
            try:
                grabbed = self._grabber()
            except Exception as exc:  # noqa: BLE001 - no display / no backend
                self._grab_error = str(exc)
            else:
                self._frame = np.ascontiguousarray(grabbed)
        else:
            self._frame = np.ascontiguousarray(frame)

        self._pixmap = QPixmap()
        if self._frame is not None:
            self._pixmap = QPixmap.fromImage(_frame_to_qimage(self._frame))

        self._origin: QPoint | None = None  # drag start (widget coords)
        self._current: QPoint | None = None  # drag current (widget coords)
        self._selection: QRect | None = None  # confirmed selection (image pixels)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        hint = QLabel(
            "Drag a rectangle over the frame to pick a region, then press OK. "
            "Esc cancels.",
            self,
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if not isinstance(ok_button, QPushButton) or not isinstance(
            cancel_button, QPushButton
        ):
            raise RuntimeError("expected Ok/Cancel buttons to be QPushButton")
        self._ok_button: QPushButton = ok_button
        self._ok_button.setText("OK")
        self._ok_button.setEnabled(False)
        cancel_button.setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

        if self._grab_error is not None:
            self._ok_button.setEnabled(False)

    # -- geometry ---------------------------------------------------------

    def _display_rect(self) -> QRectF:
        """Rect (widget coords) where the pixmap is painted, centered."""
        if (
            self._pixmap.isNull()
            or self.width() <= 0
            or self.height() <= 0
        ):
            return QRectF()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        dw, dh = pw * scale, ph * scale
        return QRectF(
            (self.width() - dw) / 2.0,
            (self.height() - dh) / 2.0,
            dw,
            dh,
        )

    def _drag_rect(self) -> QRect | None:
        if self._origin is None or self._current is None:
            return None
        return QRect(self._origin, self._current).normalized()

    def _image_rect_from_drag(self, drag: QRect) -> QRect | None:
        """Convert a widget-coords drag rect to image-pixel coords, clamped."""
        drect = self._display_rect()
        if drect.isNull() or self._pixmap.isNull():
            return None
        scale = drect.width() / max(self._pixmap.width(), 1)
        ix = (drag.x() - drect.x()) / scale
        iy = (drag.y() - drect.y()) / scale
        iw = drag.width() / scale
        ih = drag.height() / scale
        fw, fh = float(self._pixmap.width()), float(self._pixmap.height())
        ix = min(max(ix, 0.0), fw)
        iy = min(max(iy, 0.0), fh)
        iw = max(0.0, min(iw, fw - ix))
        ih = max(0.0, min(ih, fh - iy))
        if iw <= 0 or ih <= 0:
            return None
        return QRect(round(ix), round(iy), round(iw), round(ih))

    def _to_display(self, rect: QRect) -> QRectF:
        """Image-pixel rect -> widget (display) coords."""
        drect = self._display_rect()
        scale = drect.width() / max(self._pixmap.width(), 1)
        return QRectF(
            drect.x() + rect.x() * scale,
            drect.y() + rect.y() * scale,
            rect.width() * scale,
            rect.height() * scale,
        )

    def _refresh_ok(self) -> None:
        if self._ok_button is not None:
            self._ok_button.setEnabled(self._selection is not None)

    # -- input ------------------------------------------------------------

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self._selection = None
            self._refresh_ok()
            self.update()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._origin is not None
            and self._current is not None
        ):
            drag = QRect(self._origin, self._current).normalized()
            self._selection = self._image_rect_from_drag(drag)
            self._origin = None
            self._current = None
            self._refresh_ok()
            self.update()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            if self._selection is not None:
                self.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def accept(self) -> None:
        if self._selection is None:
            return  # require a selection before accepting
        super().accept()

    # -- result -----------------------------------------------------------

    def region(self) -> QRect | None:
        """Confirmed selection in pixels of the captured frame, or None."""
        return self._selection

    # -- paint ------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.fillRect(
            self.rect(), self.palette().color(QPalette.ColorRole.Window)
        )
        if not self._pixmap.isNull():
            painter.drawPixmap(self._display_rect().toRect(), self._pixmap)
        highlight = self.palette().color(QPalette.ColorRole.Highlight)
        painter.setPen(QPen(highlight, 2))
        fill = QColor(highlight)
        fill.setAlpha(60)
        painter.setBrush(fill)
        drag = self._drag_rect()
        if drag is not None and not drag.isNull():
            painter.drawRect(drag)
        if self._selection is not None:
            painter.drawRect(self._to_display(self._selection))
        painter.end()


class PixelPickerDialog(QDialog):
    """Click a pixel on a full-screen frame; report its color (issue #18).

    A modal full-screen dialog that paints one captured frame. Clicking a
    pixel reports its ``(x, y)`` in captured-frame coordinates and its BGR
    color, so a PIXEL COLOR block's X/Y/COLOR pins can be filled without
    typing coordinates by hand. Cancel leaves the pins unchanged.

    Headless-safe: accepts a pre-captured frame (tests pass a fake frame, no
    real screen grab) and never opens any input/output device. Painting uses
    palette colors only so it fits the Adwaita theming tracked in issue #20.
    """

    def __init__(
        self,
        frame: np.ndarray | None = None,
        parent: QWidget | None = None,
        *,
        grabber: Callable[[], np.ndarray] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick pixel")
        self.setModal(True)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._grabber = grabber or (lambda: grab_first_frame().image)
        self._frame: np.ndarray | None = None
        self._grab_error: str | None = None
        if frame is None:
            try:
                grabbed = self._grabber()
            except Exception as exc:  # noqa: BLE001 - no display / no backend
                self._grab_error = str(exc)
            else:
                self._frame = np.ascontiguousarray(grabbed)
        else:
            self._frame = np.ascontiguousarray(frame)

        self._pixmap = QPixmap()
        if self._frame is not None:
            self._pixmap = QPixmap.fromImage(_frame_to_qimage(self._frame))

        self._pixel: QPoint | None = None  # selected pixel (image pixels)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        hint = QLabel(
            "Click a pixel to sample its color, then press OK. Esc cancels.",
            self,
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        self._info = QLabel("", self)
        self._info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if not isinstance(ok_button, QPushButton) or not isinstance(
            cancel_button, QPushButton
        ):
            raise RuntimeError("expected Ok/Cancel buttons to be QPushButton")
        self._ok_button: QPushButton = ok_button
        self._ok_button.setText("OK")
        self._ok_button.setEnabled(False)
        cancel_button.setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

        if self._grab_error is not None:
            self._ok_button.setEnabled(False)

    # -- geometry ---------------------------------------------------------

    def _image_point_from_click(self, point: QPoint) -> QPoint | None:
        """Widget-coords click -> pixel coords in the captured frame."""
        drect = self._display_rect()
        if drect.isNull() or self._pixmap.isNull():
            return None
        scale = drect.width() / max(self._pixmap.width(), 1)
        ix = (point.x() - drect.x()) / scale
        iy = (point.y() - drect.y()) / scale
        fw, fh = float(self._pixmap.width()), float(self._pixmap.height())
        ix = min(max(ix, 0.0), fw - 1.0)
        iy = min(max(iy, 0.0), fh - 1.0)
        return QPoint(round(ix), round(iy))

    def _refresh_ok(self) -> None:
        if self._ok_button is not None:
            self._ok_button.setEnabled(self._pixel is not None)

    def _display_rect(self) -> QRectF:
        """Rect (widget coords) where the pixmap is painted, centered."""
        if (
            self._pixmap.isNull()
            or self.width() <= 0
            or self.height() <= 0
        ):
            return QRectF()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        scale = min(self.width() / pw, self.height() / ph)
        dw, dh = pw * scale, ph * scale
        return QRectF(
            (self.width() - dw) / 2.0,
            (self.height() - dh) / 2.0,
            dw,
            dh,
        )

    # -- input ------------------------------------------------------------

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = self._image_point_from_click(event.position().toPoint())
            if point is not None:
                frame = self._frame
                if frame is None:
                    return
                self._pixel = point
                b, g, r = (int(v) for v in frame[point.y(), point.x()])
                self._info.setText(
                    f"x={point.x()}, y={point.y()}  BGR=({b}, {g}, {r})"
                )
                self._refresh_ok()
                self.update()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            if self._pixel is not None:
                self.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def accept(self) -> None:
        if self._pixel is None:
            return  # require a pixel before accepting
        super().accept()

    # -- result -----------------------------------------------------------

    def pixel(self) -> tuple[int, int, int, int, int] | None:
        """``(x, y, b, g, r)`` of the clicked pixel, or None if canceled."""
        if self._pixel is None or self._frame is None:
            return None
        x, y = self._pixel.x(), self._pixel.y()
        b, g, r = (int(v) for v in self._frame[y, x])
        return x, y, b, g, r

    # -- paint ------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.fillRect(
            self.rect(), self.palette().color(QPalette.ColorRole.Window)
        )
        if not self._pixmap.isNull():
            painter.drawPixmap(self._display_rect().toRect(), self._pixmap)
        if self._pixel is not None and not self._pixmap.isNull():
            highlight = self.palette().color(QPalette.ColorRole.Highlight)
            pen = QPen(highlight, 2)
            painter.setPen(pen)
            drect = self._display_rect()
            scale = drect.width() / max(self._pixmap.width(), 1)
            cx = drect.x() + self._pixel.x() * scale
            cy = drect.y() + self._pixel.y() * scale
            painter.drawLine(int(cx - 12), int(cy), int(cx + 12), int(cy))
            painter.drawLine(int(cx), int(cy - 12), int(cx), int(cy + 12))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(cx - 6, cy - 6, 12, 12))
        painter.end()
