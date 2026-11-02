"""GStreamer frame source: turns a video pipeline into BGR numpy frames.

The pipeline ends in an ``appsink`` named ``sink``;
:class:`GStreamerFrameSource` converts samples to BGR numpy frames.

Headless-testable: any source works, e.g.
``videotestsrc ! videoconvert ! video/x-raw,format=BGR ! appsink name=sink``.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import time
from typing import Any, Protocol

import numpy as np

from input_automation.capture.base import CaptureError, Frame

logger = logging.getLogger(__name__)

__all__ = ["FrameSource", "GStreamerFrameSource", "build_pipeline"]

Gst: Any = None  # populated lazily by _ensure_gst()


def _ensure_gst() -> Any:
    """Import and initialize GStreamer once."""
    global Gst
    if Gst is None:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst as _Gst

        _Gst.init(None)
        Gst = _Gst
    return Gst


class FrameSource(Protocol):
    """Anything that can produce consecutive frames."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def read_frame(self, timeout: float = 10.0) -> Frame:
        """Return the next frame; raises TimeoutError if none arrives."""
        ...


def build_pipeline(description: str) -> Any:
    """Parse a GStreamer pipeline description that contains ``name=sink``."""
    gst = _ensure_gst()
    pipeline = gst.parse_launch(description)
    sink = pipeline.get_by_name("sink")
    if sink is None:
        raise ValueError("pipeline description must contain 'name=sink' appsink")
    return pipeline


class GStreamerFrameSource:
    """FrameSource backed by a GStreamer pipeline with an appsink ``sink``.

    New samples arrive through the appsink's ``new-sample`` callback (queued,
    never blocking the streaming thread); :meth:`read_frame` pops them.
    """

    def __init__(self, pipeline: Any) -> None:
        gst = _ensure_gst()
        self._gst = gst
        self._pipeline = pipeline
        self._sink = pipeline.get_by_name("sink")
        self._queue: queue.Queue[Frame] = queue.Queue(maxsize=4)
        self._running = False
        self._error: str | None = None

    def start(self) -> None:
        if self._running:
            raise RuntimeError("frame source already running")
        self._running = True
        self._error: str | None = None
        self._sink.set_property("emit-signals", True)
        self._sink.connect("new-sample", self._on_new_sample)
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_bus_error)
        self._pipeline.set_state(self._gst.State.PLAYING)

    def stop(self) -> None:
        self._running = False
        try:
            self._pipeline.set_state(self._gst.State.NULL)
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.exception("error stopping pipeline")

    def read_frame(self, timeout: float = 10.0) -> Frame:
        if self._error is not None:
            raise CaptureError(self._error)
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"no frame within {timeout}s") from exc

    # -- internals ----------------------------------------------------------

    def _on_bus_error(self, _bus: Any, message: Any) -> None:
        err, debug = message.parse_error()
        logger.error("pipeline error: %s (%s)", err.message, debug)
        self._error = err.message

    def _on_new_sample(self, sink: Any) -> Any:
        sample = sink.emit("pull-sample")
        if sample is None:
            return self._gst.FlowReturn.ERROR
        try:
            frame = self._sample_to_frame(sample)
        except Exception:  # noqa: BLE001 - a bad frame must not kill the stream
            logger.exception("failed to convert sample")
            return self._gst.FlowReturn.ERROR
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(frame)  # drop frames rather than block the pipeline
        return self._gst.FlowReturn.OK

    def _sample_to_frame(self, sample: Any) -> Frame:
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        height = int(structure.get_value("height"))
        width = int(structure.get_value("width"))
        buffer = sample.get_buffer()
        pts = buffer.pts  # nanoseconds; read before unmap for safety
        ok, info = buffer.map(self._gst.MapFlags.READ)
        if not ok:
            raise RuntimeError("could not map GStreamer buffer")
        try:
            channels = len(info.data) // (height * width)
            image = np.frombuffer(info.data, dtype=np.uint8).reshape(
                height, width, channels
            ).copy()  # copy: the mapped memory dies after unmap
        finally:
            buffer.unmap(info)
        return Frame(image=image, ts=pts / 1e9 if pts > 0 else time.monotonic())
