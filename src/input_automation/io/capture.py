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
import os
from itertools import chain
from typing import Protocol

import cv2
import numpy as np

from input_automation.capture.base import Frame
from input_automation.capture.mock import MockCaptureBackend
from input_automation.engine.block import Block
from input_automation.engine.wires import WireGraph
from input_automation.vision.primitives import (
    bar_fill_fraction,
    color_fraction,
    region_crop,
    template_match,
)

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
    """Grab one frame per cycle and drive capture/pixel-compare block output pins.

    Handles ``CAPTURE`` blocks (region color/threshold/template/bar
    measurement), ``PIXEL_COLOR`` blocks (single-pixel color match) and
    ``REGION_COMPARE`` blocks (frame-to-frame or image-to-region comparison).
    One frame is grabbed per cycle and shared by every capture block in the
    graph; :attr:`_previous_image` is kept between cycles so ``diff`` and
    ``shape`` modes can compare against the previous frame.

    Pass an instance as ``read_inputs`` to
    :class:`input_automation.engine.ScanExecutor`; each cycle it grabs one
    screen frame and writes every managed block's ``VALUE``/``Q`` output pins.

    The backend is injectable, so tests run against
    :class:`input_automation.capture.mock.MockCaptureBackend` with synthetic
    frames -- no display, PipeWire, or mutter session needed. ``backend`` defaults
    to the mock backend, so a bare :class:`ScanExecutor` works headless; pass a
    real backend (e.g.
    :class:`input_automation.capture.screencast.ScreencastBackend`) for live
    screen capture. A backend failure or a malformed region/color spec yields
    safe output values (``VALUE=0`` / ``Q=False``) with a debug log line and
    never aborts a scan cycle.
    """

    # Block type names this provider manages; all of them consume captured frames.
    MANAGED_TYPES = frozenset({"CAPTURE", "PIXEL_COLOR", "REGION_COMPARE"})

    def __init__(self, *, backend: _FrameSource | None = None) -> None:
        self._backend = backend or MockCaptureBackend()
        self._previous_image: np.ndarray | None = None

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
        """Grab one frame per cycle and drive every managed block's output pins."""
        capture_blocks: list[Block] = []
        pixel_blocks: list[Block] = []
        compare_blocks: list[Block] = []
        for block_id in graph.blocks():
            block = graph.block(block_id)
            type_name = getattr(block, "type_name", None)
            if type_name == "CAPTURE":
                capture_blocks.append(block)
            elif type_name == "PIXEL_COLOR":
                pixel_blocks.append(block)
            elif type_name == "REGION_COMPARE":
                compare_blocks.append(block)
        if not (capture_blocks or pixel_blocks or compare_blocks):
            return
        frame = self._grab_frame()
        if frame is None:
            for block in chain(capture_blocks, pixel_blocks, compare_blocks):
                if block.type_name == "CAPTURE":
                    self._write_safe(block)
                elif block.type_name == "PIXEL_COLOR":
                    self._write_safe_pixel(block)
                else:
                    self._write_safe_compare(block)
            return
        image = frame.image
        previous = self._previous_image
        self._previous_image = image
        for block in capture_blocks:
            self._measure_block(block, image)
        for block in pixel_blocks:
            self._measure_pixel_block(block, image)
        for block in compare_blocks:
            self._measure_compare_block(block, image, previous)

    @staticmethod
    def _write_safe_pixel(block: Block) -> None:
        block.output("VALUE").set(0)
        block.output("Q").set(False)

    @staticmethod
    def _write_safe_compare(block: Block) -> None:
        block.output("VALUE").set(0.0)
        block.output("Q").set(False)

    def _measure_pixel_block(self, block: Block, image: np.ndarray) -> None:
        """Sample one pixel and compare its BGR to COLOR within TOLERANCE."""
        x = _as_int(block.input("X").value, 0)
        y = _as_int(block.input("Y").value, 0)
        color = _parse_color(str(block.input("COLOR").value or "0,0,0"))
        tolerance = _as_int(block.input("TOLERANCE").value, 40)
        frame_h, frame_w = image.shape[0], image.shape[1]
        if color is None or not (0 <= x < frame_w and 0 <= y < frame_h):
            logger.debug(
                "pixel %s: out-of-bounds pixel (%s, %s) or bad COLOR spec",
                block.id,
                x,
                y,
            )
            self._write_safe_pixel(block)
            return
        pixel = image[y, x]
        matched = all(
            abs(int(pixel[channel]) - target) <= tolerance
            for channel, target in enumerate(color)
        )
        block.output("VALUE").set(1 if matched else 0)
        block.output("Q").set(bool(matched))

    def _measure_compare_block(
        self, block: Block, image: np.ndarray, previous: np.ndarray | None
    ) -> None:
        """Compare a region across frames or against a reference image."""
        mode = str(block.input("MODE").value or "diff")
        tolerance = _as_float(block.input("TOLERANCE").value, 0.1)
        spec = str(block.input("REGION").value or "")
        coords = None if spec == "" else _parse_region(spec)
        if coords is None and spec != "":
            logger.debug("compare %s: malformed REGION spec %r", block.id, spec)
            self._write_safe_compare(block)
            return
        try:
            region = image if coords is None else region_crop(image, *coords)
            if mode == "diff":
                value = self._diff_value(block, region, previous, coords)
            elif mode == "shape":
                value = self._shape_value(block, region, previous, coords)
            elif mode == "template":
                value = self._template_value(block, region)
            else:
                logger.debug("compare %s: unknown MODE %r", block.id, mode)
                self._write_safe_compare(block)
                return
        except Exception as exc:  # noqa: BLE001 - one bad block must not abort a cycle
            logger.debug("compare %s: measurement failed: %s", block.id, exc)
            value = 0.0
        block.output("VALUE").set(float(value))
        block.output("Q").set(bool(value >= tolerance))

    @staticmethod
    def _crop_same(
        previous: np.ndarray,
        coords: tuple[int, int, int, int] | None,
    ) -> np.ndarray | None:
        """Crop the previous frame with the same coords; None when invalid."""
        if coords is None:
            return previous
        try:
            return region_crop(previous, *coords)
        except ValueError:
            return None

    def _diff_value(
        self,
        block: Block,
        region: np.ndarray,
        previous: np.ndarray | None,
        coords: tuple[int, int, int, int] | None,
    ) -> float:
        """Fraction of pixels that changed vs the previous frame's same region."""
        if previous is None:
            logger.debug("compare %s: diff mode with no previous frame yet", block.id)
            return 0.0
        prev_region = self._crop_same(previous, coords)
        if prev_region is None:
            return 0.0
        changed = (region != prev_region).any(axis=2)
        return float(changed.mean())

    def _shape_value(
        self,
        block: Block,
        region: np.ndarray,
        previous: np.ndarray | None,
        coords: tuple[int, int, int, int] | None,
    ) -> float:
        """Edge-map similarity of the region vs the previous frame (colors ignored)."""
        if previous is None:
            logger.debug("compare %s: shape mode with no previous frame yet", block.id)
            return 0.0
        prev_region = self._crop_same(previous, coords)
        if prev_region is None:
            return 0.0
        cur_edges = cv2.Canny(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY), 100, 200)
        prev_edges = cv2.Canny(cv2.cvtColor(prev_region, cv2.COLOR_BGR2GRAY), 100, 200)
        return float((cur_edges == prev_edges).mean())

    def _template_value(self, block: Block, region: np.ndarray) -> float:
        """Template-match score of the region vs the REFERENCE image file."""
        template_path = str(block.input("REFERENCE").value or "")
        if not template_path:
            logger.debug("compare %s: template mode with no REFERENCE path", block.id)
            return 0.0
        if not os.path.isfile(template_path):
            logger.debug(
                "compare %s: REFERENCE file not readable: %s", block.id, template_path
            )
            return 0.0
        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            logger.debug(
                "compare %s: could not decode REFERENCE image: %s",
                block.id,
                template_path,
            )
            return 0.0
        try:
            return float(template_match(region, template).score)
        except ValueError as exc:  # template larger than region, etc.
            logger.debug("compare %s: template match failed: %s", block.id, exc)
            return 0.0

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
        limit = _as_float(block.input("LIMIT").value, 0.5)
        try:
            region = self._region(image, block)
            if mode == "color":
                value = self._color_fraction(region, block)
            elif mode == "threshold":
                value = float(region.mean() / 255.0)
            elif mode == "template":
                value = self._template_match(region, block)
            elif mode == "bar":
                value = self._bar_fill_fraction(region, block)
            else:
                logger.debug("capture %s: unknown mode %r", block.id, mode)
                self._write_safe(block)
                return
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
        unit = str(block.input("UNIT").value or "pixels")
        if unit == "percent":
            frame_h, frame_w = image.shape[0], image.shape[1]
            x = round(x / 100.0 * frame_w)
            y = round(y / 100.0 * frame_h)
            w = round(w / 100.0 * frame_w)
            h = round(h / 100.0 * frame_h)
        elif unit != "pixels":
            logger.debug("capture %s: unknown unit %r; treating as pixels", block.id, unit)
        return region_crop(image, x, y, w, h)  # ValueError if out of bounds / degenerate

    @staticmethod
    def _template_match(region: np.ndarray, block: Block) -> float:
        template_path = str(block.input("TEMPLATE").value or "")
        if not template_path:
            logger.debug("capture %s: template mode with no TEMPLATE path", block.id)
            return 0.0
        if not os.path.isfile(template_path):
            logger.debug(
                "capture %s: TEMPLATE file not readable: %s", block.id, template_path
            )
            return 0.0
        template = cv2.imread(template_path, cv2.IMREAD_COLOR)
        if template is None:
            logger.debug(
                "capture %s: could not decode TEMPLATE image: %s",
                block.id,
                template_path,
            )
            return 0.0
        try:
            return float(template_match(region, template).score)
        except ValueError as exc:  # template larger than region, etc.
            logger.debug("capture %s: template match failed: %s", block.id, exc)
            return 0.0

    @staticmethod
    def _bar_fill_fraction(region: np.ndarray, block: Block) -> float:
        color = _parse_color(str(block.input("COLOR").value or ""))
        if color is None:
            return 0.0
        tolerance = _as_int(block.input("TOLERANCE").value, 40)
        b, g, r = color
        lower = (max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance))
        upper = (min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance))
        return float(bar_fill_fraction(region, lower, upper))

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
