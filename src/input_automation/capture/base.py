"""Screen capture abstraction.

Every backend yields ``Frame`` objects (BGR numpy images) through the same
protocol, so the analysis layer and trigger engine never care where frames
come from. Headless tests use :class:`input_automation.capture.mock.MockCaptureBackend`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

import numpy as np

__all__ = ["CaptureBackend", "CaptureError", "Frame"]


class CaptureError(Exception):
    """Screen capture could not be started or delivered frames."""


@dataclass(frozen=True)
class Frame:
    """One captured screen frame (BGR, like OpenCV expects)."""

    image: np.ndarray
    ts: float

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])


class CaptureBackend(Protocol):
    """A source of consecutive screen frames."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def grab(self, timeout: float = 10.0) -> Frame: ...

    def frames(self) -> Iterator[Frame]:
        """Yield frames until :meth:`stop` is called (blocking)."""
        ...


def monotonic_ts() -> float:
    return time.monotonic()
