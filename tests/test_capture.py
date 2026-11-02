"""Tests for the capture abstraction and mock backend (headless)."""

from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

import input_automation.cli as cli_module
from input_automation.capture.base import Frame
from input_automation.capture.mock import MockCaptureBackend, bar_pattern


class TestBarPattern:
    def test_shape_and_dtype(self) -> None:
        image = bar_pattern(0.0)
        assert image.shape == (64, 64, 3)
        assert image.dtype == np.uint8

    def test_animates_over_time(self) -> None:
        empty = bar_pattern(0.0)
        full = bar_pattern(0.5)  # sine peak -> bar fully filled
        assert int(empty.sum()) < int(full.sum())
        # deterministic: same t -> same frame
        assert np.array_equal(bar_pattern(0.5), full)

    def test_custom_size(self) -> None:
        image = bar_pattern(0.5, size=(32, 48))
        assert image.shape == (32, 48, 3)


class TestMockBackend:
    def test_frames_yield_until_stop(self) -> None:
        capture = MockCaptureBackend(fps=1000.0)
        capture.start()
        frames: list[Frame] = []
        for frame in capture.frames():
            frames.append(frame)
            if len(frames) == 3:
                capture.stop()
                break

        assert len(frames) == 3
        assert frames[0].ts <= frames[1].ts <= frames[2].ts
        assert capture.frame_count == 3

    def test_stop_ends_generator(self) -> None:
        capture = MockCaptureBackend(fps=10_000.0)
        capture.start()
        gen = capture.frames()
        next(gen)
        capture.stop()
        remaining = 0
        for _ in gen:
            remaining += 1
        assert remaining <= 2  # current + at most one buffered frame

    def test_not_started_raises(self) -> None:
        capture = MockCaptureBackend()
        with pytest.raises(RuntimeError, match="not started"):
            list(capture.frames())

    def test_double_start_raises(self) -> None:
        capture = MockCaptureBackend()
        capture.start()
        with pytest.raises(RuntimeError, match="already running"):
            capture.start()
        capture.stop()

    def test_grab_returns_single_frame(self) -> None:
        capture = MockCaptureBackend()
        frame = capture.grab()
        assert isinstance(frame, Frame)
        assert frame.width == 64
        assert frame.height == 64

    def test_custom_pattern_fn(self) -> None:
        sentinel = np.full((4, 4, 3), 7, dtype=np.uint8)
        capture = MockCaptureBackend(pattern_fn=lambda t: sentinel)
        assert np.array_equal(capture.grab().image, sentinel)


class TestFrame:
    def test_properties(self) -> None:
        image = np.zeros((10, 20, 3), dtype=np.uint8)
        frame = Frame(image=image, ts=1.0)
        assert frame.width == 20
        assert frame.height == 10


def test_screenshot_cli(tmp_path: Path) -> None:
    out = tmp_path / "shot.png"
    result = CliRunner().invoke(
        cli_module.app, ["screenshot", "--output", str(out)]
    )
    assert result.exit_code == 0
    assert "saved" in result.output
    assert out.exists()
    image = cv2.imread(str(out))
    assert image is not None
    assert image.shape == (64, 64, 3)


def test_screenshot_cli_unknown_backend() -> None:
    result = CliRunner().invoke(
        cli_module.app, ["screenshot", "--backend", "portal"]
    )
    assert result.exit_code == 2
    assert "unknown backend" in result.output
