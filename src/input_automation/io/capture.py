"""Capture input provider: measure screen regions into ``CAPTURE`` blocks (T9).

Pass an instance as ``read_inputs`` to
:class:`input_automation.engine.ScanExecutor`; each cycle it grabs one screen
frame and measures every ``CAPTURE`` block's region (color fraction or mean
intensity), writing the block's ``VALUE``/``Q`` output pins. One frame is
shared by all capture blocks in a cycle.

The backend is injectable, so tests run against
:class:`input_automation.capture.mock.MockCaptureBackend` with synthetic
frames -- no display, PipeWire, or mutter session needed. ``backend`` defaults
to the mock backend, so a bare :class:`ScanExecutor` works headless; pass a
real backend (e.g.
:class:`input_automation.capture.screencast.ScreencastBackend`) for live
screen capture. A backend failure or a malformed region/color spec yields
``VALUE=0.0, Q=False`` with a debug log line and never aborts a scan cycle.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Protocol

import numpy as np

from input_automation.capture.base import Frame
from input_automation.capture.mock import MockCaptureBackend
from input_automation.engine.block import Block
from input_automation.engine.wires import WireGraph
from input_automation.vision.primitives import color_fraction, region_crop

logger = logging.getLogger(__name__)

__all__ = ["CaptureInputProvider"]


class _FrameSource(Protocol):
    """The capture backend surface the provider needs: one frame per grab."""

    def grab(self, timeout: float = 10.0) -> Frame: ...


def _parse_region(spec: str) -> tuple[int, int, int, int] | None:
    """Parse ``"x,y,w,h"``; return ``None`` when malformed or degenerate."""
    parts = [part.strip() for part in spec.split(",")]
    if len(parts) != 4:
        return None
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    x, y, w, h = numbers
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _parse_color(spec: str) -> tuple[int, int, int] | None:
    """Parse ``"b,g,r"`` into a BGR triple; ``None`` when malformed."""
    parts = [part.strip() for part in spec.split(",")]
    if len(parts) != 3:
        return None
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None
    b, g, r = numbers
    if not all(0 <= channel <= 255 for channel in (b, g, r)):
        return None
    return b, g, r


def _as_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _as_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


class CaptureInputProvider:
    """Grab one frame per cycle and drive ``CAPTURE`` block output pins."""

    def __init__(self, *, backend: _FrameSource | None = None) -> None:
        self._backend = backend or MockCaptureBackend()

    def close(self) -> None:
        """Release the capture backend if it holds any releasable resources.

        Symmetrical with :class:`input_automation.io.evdev.EvdevInputProvider`
        and :class:`input_automation.io.uinput.UinputOutputProvider`, both of
        which expose :meth:`close` so a scan loop can tear down cleanly on stop.
        """
        backend = getattr(self, "_backend", None)
        closer = getattr(backend, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                closer()

    def __call__(self, graph: WireGraph) -> None:
        """Measure every ``CAPTURE`` block against one freshly grabbed frame."""
        capture_blocks: list[Block] = []
        for block_id in graph.blocks():
            block = graph.block(block_id)
            if getattr(block, "type_name", None) == "CAPTURE":
                capture_blocks.append(block)
        if not capture_blocks:
            return
        frame = self._grab_frame()
        if frame is None:
            for block in capture_blocks:
                self._write_safe(block)
            return
        image = frame.image
        for block in capture_blocks:
            self._measure_block(block, image)

    def _grab_frame(self) -> Frame | None:
        try:
            return self._backend.grab()
        except Exception as exc:  # noqa: BLE001 - a dead backend must not abort a cycle
            logger.debug("capture backend grab failed: %s", exc)
            return None

    @staticmethod
    def _write_safe(block: Block) -> None:
        block.output("VALUE").set(0.0)
        block.output("Q").set(False)

    def _measure_block(self, block: Block, image: np.ndarray) -> None:
        mode = str(block.input("MODE").value or "color")
        if mode not in ("color", "threshold"):
            logger.debug("capture %s: unknown mode %r", block.id, mode)
            self._write_safe(block)
            return
        limit = _as_float(block.input("LIMIT").value, 0.5)
        try:
            region = self._region(image, block)
            if mode == "threshold":
                value = float(region.mean() / 255.0)
            else:
                value = self._color_fraction(region, block)
        except Exception as exc:  # noqa: BLE001 - one bad block must not abort a cycle
            logger.debug("capture %s: measurement failed: %s", block.id, exc)
            value = 0.0
        block.output("VALUE").set(float(value))
        block.output("Q").set(bool(value >= limit))

    @staticmethod
    def _region(image: np.ndarray, block: Block) -> np.ndarray:
        spec = str(block.input("REGION").value or "")
        parsed = _parse_region(spec)
        if parsed is None:
            if spec == "":
                return image  # whole frame
            raise ValueError(f"malformed region spec {spec!r}")
        x, y, w, h = parsed
        return region_crop(image, x, y, w, h)  # ValueError if out of bounds

    @staticmethod
    def _color_fraction(region: np.ndarray, block: Block) -> float:
        color = _parse_color(str(block.input("COLOR").value or ""))
        if color is None:
            return 0.0
        tolerance = _as_int(block.input("TOLERANCE").value, 40)
        b, g, r = color
        lower = (max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance))
        upper = (min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance))
        return float(color_fraction(region, lower, upper))
