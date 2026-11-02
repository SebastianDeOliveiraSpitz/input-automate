"""Mock capture backend: synthetic frames for tests and headless use."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator

import numpy as np

from input_automation.capture.base import Frame

__all__ = ["MockCaptureBackend", "bar_pattern"]

PatternFn = Callable[[float], np.ndarray]


def bar_pattern(t: float, size: tuple[int, int] = (64, 64)) -> np.ndarray:
    """Dark frame with an animated green 'health bar' that oscillates.

    Period is 2 s — useful to test bar-fraction measurement and visual
    triggers against a known, deterministic change.
    """
    height, width = size
    image = np.zeros((height, width, 3), dtype=np.uint8)
    fraction = (math.sin(t * math.pi) + 1) / 2  # oscillates 0..1, period 2 s
    bar_width = int(width * 0.8 * fraction)
    if bar_width > 0:
        image[8:16, 8 : 8 + bar_width] = (60, 200, 60)  # BGR green
    return image


class MockCaptureBackend:
    """Synthetic frame source implementing CaptureBackend.

    ``pattern_fn`` maps a monotonic timestamp to a BGR image. Defaults to
    :func:`bar_pattern`. ``fps`` controls the generator's pacing.
    """

    def __init__(
        self,
        pattern_fn: PatternFn | None = None,
        *,
        fps: float = 30.0,
        size: tuple[int, int] = (64, 64),
    ) -> None:
        self._pattern = pattern_fn or (lambda t: bar_pattern(t, size))
        self._interval = 1.0 / fps
        self._t0 = time.monotonic()
        self._running = False
        self._frame_count = 0

    def start(self) -> None:
        if self._running:
            raise RuntimeError("capture backend already running")
        self._running = True
        self._t0 = time.monotonic()
        self._frame_count = 0

    def stop(self) -> None:
        self._running = False

    def frames(self) -> Iterator[Frame]:
        if not self._running:
            raise RuntimeError("capture backend not started")
        while self._running:
            yield self._make_frame()
            time.sleep(self._interval)

    def grab(self) -> Frame:
        """Convenience: one frame without the generator loop."""
        if not self._running:
            self.start()
            self.stop()
        return self._make_frame()

    def _make_frame(self) -> Frame:
        ts = time.monotonic()
        image = self._pattern(ts - self._t0)
        self._frame_count += 1
        return Frame(image=image, ts=ts)

    @property
    def frame_count(self) -> int:
        return self._frame_count
