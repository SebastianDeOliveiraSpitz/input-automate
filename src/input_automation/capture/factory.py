"""Capture backend factory: pick a backend by name (T13).

Imports of the real (GStreamer/PipeWire) backends are deferred to the
selected branch so that plain CLI invocations (``--version``, mock capture)
never pay the GStreamer import cost.
"""

from __future__ import annotations

from input_automation.capture.base import CaptureBackend

__all__ = ["BACKEND_NAMES", "create_backend"]

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
