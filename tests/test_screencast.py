"""Tests for the screencast backend orchestration (fake session + pipeline)."""

from typing import Any

import numpy as np
import pytest

from input_automation.capture.base import Frame
from input_automation.capture.screencast import (
    CaptureError,
    ScreencastBackend,
    mutter_screencast_session,
)


class FakeFrameSource:
    """Fake GStreamerFrameSource yielding a fixed image list."""

    def __init__(self, node_id: int, images: list[np.ndarray]) -> None:
        self.node_id = node_id
        self.images = images
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def read_frame(self, timeout: float = 10.0) -> Frame:
        if not self.images:
            raise TimeoutError("no frames")
        image = self.images.pop(0)
        return Frame(image=image, ts=0.0)


def _fake_pipeline(images: list[np.ndarray]) -> Any:
    sources: list[FakeFrameSource] = []

    def factory(node_id: int) -> FakeFrameSource:
        source = FakeFrameSource(node_id, images)
        sources.append(source)
        return source

    return factory, sources


class TestScreencastBackend:
    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown screencast backend"):
            ScreencastBackend(backend="vnc")

    def test_lifecycle_and_frames(self) -> None:
        image = np.zeros((16, 32, 3), dtype=np.uint8)
        factory, sources = _fake_pipeline([image, image])
        backend = ScreencastBackend(
            session_factory=lambda: 42, pipeline_factory=factory
        )
        with backend as cap:
            frames: list[Frame] = []
            for frame in cap.frames():
                frames.append(frame)
                if len(frames) == 2:
                    break

        assert len(sources) == 1
        assert sources[0].node_id == 42
        assert sources[0].started and sources[0].stopped  # context exit stops it
        assert frames[0].image.shape == (16, 32, 3)

    def test_grab_starts_and_stops(self) -> None:
        image = np.full((8, 8, 3), 9, dtype=np.uint8)
        factory, sources = _fake_pipeline([image])
        backend = ScreencastBackend(
            session_factory=lambda: 7, pipeline_factory=factory
        )
        frame = backend.grab(timeout=2.0)

        assert frame.image[0, 0, 0] == 9
        assert sources[0].started and sources[0].stopped

    def test_not_started_raises(self) -> None:
        factory, _ = _fake_pipeline([])
        backend = ScreencastBackend(session_factory=lambda: 1, pipeline_factory=factory)
        with pytest.raises(RuntimeError, match="not started"):
            list(backend.frames())

    def test_double_start_raises(self) -> None:
        factory, _ = _fake_pipeline([])
        backend = ScreencastBackend(
            session_factory=lambda: 1, pipeline_factory=factory
        )
        backend.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                backend.start()
        finally:
            backend.stop()

    def test_session_failure_raises_capture_error(self) -> None:
        def fail() -> int:
            raise CaptureError("no session bus")

        factory, _ = _fake_pipeline([])
        backend = ScreencastBackend(session_factory=fail, pipeline_factory=factory)
        with pytest.raises(CaptureError, match="no session bus"):
            backend.grab(timeout=1.0)


def test_mutter_session_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    import input_automation.capture.screencast as sc

    class FailingBus:
        def call_sync(self, *_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("no such bus")

    monkeypatch.setattr(sc, "_session_bus", lambda: FailingBus())
    with pytest.raises(CaptureError, match="CreateSession failed"):
        mutter_screencast_session()


def test_run_isolated_propagates_worker_error() -> None:
    import input_automation.capture.screencast as sc

    result: dict[str, Any] = {}

    def run() -> None:
        result["error"] = CaptureError("worker blew up")

    with pytest.raises(CaptureError, match="worker blew up"):
        sc._run_isolated(run, result)


def test_run_isolated_worker_hang_raises_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as time_mod

    import input_automation.capture.screencast as sc

    monkeypatch.setattr(sc, "PORTAL_TIMEOUT_MS", 200)
    result: dict[str, Any] = {}

    def run() -> None:
        time_mod.sleep(2)  # worker never stores a node

    with pytest.raises(CaptureError, match="did not deliver a node id"):
        sc._run_isolated(run, result)
