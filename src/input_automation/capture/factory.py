"""Capture backend factory: pick a backend by name (T13).

Imports of the real (GStreamer/PipeWire) backends are deferred to the
selected branch so that plain CLI invocations (``--version``, mock capture)
never pay the GStreamer import cost.
"""

from __future__ import annotations

from collections.abc import Sequence

from input_automation.capture.base import CaptureBackend, CaptureError, Frame

__all__ = [
    "BACKEND_NAMES",
    "create_backend",
    "grab_first_frame",
]

BACKEND_NAMES = ("mock", "mutter", "portal")


def create_backend(name: str) -> CaptureBackend:
    """Instantiate the capture backend ``name`` ("mock", "mutter", "portal")."""
    if name == "mock":
        from input_automation.capture.mock import MockCaptureBackend

        return MockCaptureBackend()
    if name in ("mutter", "portal"):
        from input_automation.capture.screencast import ScreencastBackend

        return ScreencastBackend(backend=name)
    raise ValueError(
        f"unknown capture backend {name!r} (available: {', '.join(BACKEND_NAMES)})"
    )


def grab_first_frame(
    names: Sequence[str] = ("mutter", "mock"), *, timeout: float = 5.0
) -> Frame:
    """Grab one frame from the first backend that works.

    The real screen (mutter) is tried before the mock backend; every
    backend manages its own start/stop inside :meth:`grab`. Raises
    :class:`CaptureError` when all candidates fail.
    """
    failures: list[str] = []
    for name in names:
        try:
            backend = create_backend(name)
            return backend.grab(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - try the next backend
            failures.append(f"{name}: {exc!r}")
    raise CaptureError(
        "no capture backend produced a frame ("
        + "; ".join(failures)
        + ")"
    )
