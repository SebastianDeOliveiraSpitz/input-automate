"""Tests for the GStreamer frame source (headless via videotestsrc)."""

import pytest

from input_automation.capture.gstsource import GStreamerFrameSource, build_pipeline

TEST_PIPELINE = (
    "videotestsrc pattern=smpte ! video/x-raw,width=64,height=32,format=BGR ! "
    "videoconvert ! appsink name=sink max-buffers=2 drop=true"
)


def test_build_pipeline_requires_sink() -> None:
    with pytest.raises(ValueError, match="name=sink"):
        build_pipeline("videotestsrc ! fakesink")


def test_read_frames_from_pipeline() -> None:
    source = GStreamerFrameSource(build_pipeline(TEST_PIPELINE))
    source.start()
    try:
        frame = source.read_frame(timeout=10.0)
        assert frame.image.shape == (32, 64, 3)
        assert frame.image.dtype.name == "uint8"
        second = source.read_frame(timeout=10.0)
        assert second.image.shape == (32, 64, 3)
    finally:
        source.stop()


def test_double_start_raises() -> None:
    source = GStreamerFrameSource(build_pipeline(TEST_PIPELINE))
    source.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            source.start()
    finally:
        source.stop()


def test_read_frame_timeout_when_stopped() -> None:
    source = GStreamerFrameSource(build_pipeline(TEST_PIPELINE))
    source.start()
    source.stop()
    # pipeline stopped -> no new samples -> queue.get times out
    with pytest.raises(TimeoutError):
        source.read_frame(timeout=0.5)
