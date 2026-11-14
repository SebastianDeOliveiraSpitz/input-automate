"""PIXEL_COLOR / REGION_COMPARE blocks + CaptureInputProvider (issue #18).

Headless: the provider grabs through an injected backend; tests use
MockCaptureBackend with fixed synthetic frames -- no display, PipeWire, or
mutter session required.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from evdev import ecodes

from input_automation.capture.mock import MockCaptureBackend
from input_automation.engine import Block, ScanExecutor, WireGraph, default_registry
from input_automation.engine.io_blocks import (
    OutputBlock,
    PixelColorBlock,
    RegionCompareBlock,
)
from input_automation.engine.pins import PinDType
from input_automation.io.capture import CaptureInputProvider
from input_automation.io.uinput import UinputOutputProvider

GREEN = (60, 200, 60)  # BGR
RED = (60, 60, 220)  # BGR


class _FailingBackend:
    """Backend whose grab() always raises."""

    def grab(self, timeout: float = 10.0) -> object:
        raise RuntimeError("capture backend is gone")


class _FakeUinputDevice:
    """Recording uinput device: appends every emit() instead of driving the kernel."""

    def __init__(self, name: str, specs: list[tuple[int, object]]) -> None:
        self.name = name
        self.specs = specs
        self.events: list[tuple[tuple[int, int], int, bool]] = []
        self.closed = False

    def emit(self, event: tuple[int, int], value: int, syn: bool = True) -> None:
        self.events.append((event, value, syn))

    def close(self) -> None:
        self.closed = True


def _pixel_block(
    block_id: str = "pix1",
    x: int = 0,
    y: int = 0,
    color: str = "60,200,60",
    tolerance: int = 40,
) -> PixelColorBlock:
    block = PixelColorBlock(block_id)
    block.input("X").set(x)
    block.input("Y").set(y)
    block.input("COLOR").set(color)
    block.input("TOLERANCE").set(tolerance)
    return block


def _compare_block(
    block_id: str = "cmp1",
    region: str = "",
    mode: str = "diff",
    reference: str = "",
    tolerance: float = 0.1,
) -> RegionCompareBlock:
    block = RegionCompareBlock(block_id)
    block.input("REGION").set(region)
    block.input("MODE").set(mode)
    block.input("REFERENCE").set(reference)
    block.input("TOLERANCE").set(tolerance)
    return block


def _output_block(device: str, code: str, block_id: str = "out1") -> OutputBlock:
    block = OutputBlock(block_id)
    block.input("DEVICE").set(device)
    block.input("CODE").set(code)
    return block


def _run_once(provider: CaptureInputProvider, *blocks: Block) -> None:
    graph = WireGraph()
    for block in blocks:
        graph.add_block(block)
    ScanExecutor(graph, read_inputs=provider).run_once()


def _green_image() -> np.ndarray:
    return np.full((20, 20, 3), GREEN, dtype=np.uint8)


def _black_image() -> np.ndarray:
    return np.zeros((20, 20, 3), dtype=np.uint8)


def _red_square_image() -> np.ndarray:
    image = _black_image()
    image[4:8, 6:10] = RED
    return image


def _green_square_image() -> np.ndarray:
    image = _black_image()
    image[4:8, 6:10] = GREEN
    return image


# -- block -----------------------------------------------------------------


def test_pixel_color_block_pins() -> None:
    block = PixelColorBlock("pix1")
    assert block.type_name == "PIXEL_COLOR"
    assert block.category == "Input"
    assert set(block.inputs) == {"X", "Y", "COLOR", "TOLERANCE"}
    assert set(block.outputs) == {"VALUE", "Q"}


def test_pixel_color_block_pin_types() -> None:
    block = PixelColorBlock("pix1")
    assert block.input("X").dtype is PinDType.INT
    assert block.input("Y").dtype is PinDType.INT
    assert block.input("COLOR").dtype is PinDType.STRING
    assert block.input("TOLERANCE").dtype is PinDType.INT
    assert block.output("VALUE").dtype is PinDType.INT
    assert block.output("Q").dtype is PinDType.BOOL
    assert block.input("X").value == 0
    assert block.input("Y").value == 0
    assert block.input("COLOR").value == "0,0,0"
    assert block.input("TOLERANCE").value == 40
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is False


def test_pixel_color_block_registered_in_default_registry() -> None:
    assert default_registry.get("PIXEL_COLOR") is PixelColorBlock


def test_pixel_color_block_evaluate_changes_nothing() -> None:
    block = _pixel_block()
    block.output("VALUE").set(1)
    block.output("Q").set(True)
    block.evaluate()
    assert block.output("VALUE").value == 1
    assert block.output("Q").value is True


def test_region_compare_block_pins() -> None:
    block = RegionCompareBlock("cmp1")
    assert block.type_name == "REGION_COMPARE"
    assert block.category == "Input"
    assert set(block.inputs) == {"REGION", "MODE", "REFERENCE", "TOLERANCE"}
    assert set(block.outputs) == {"VALUE", "Q"}
    assert block.input("REGION").dtype is PinDType.STRING
    assert block.input("MODE").dtype is PinDType.STRING
    assert block.input("REFERENCE").dtype is PinDType.STRING
    assert block.input("TOLERANCE").dtype is PinDType.FLOAT
    assert block.output("VALUE").dtype is PinDType.FLOAT
    assert block.output("Q").dtype is PinDType.BOOL
    assert block.input("REGION").value == ""
    assert block.input("MODE").value == "diff"
    assert block.input("REFERENCE").value == ""
    assert block.input("TOLERANCE").value == 0.1
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_region_compare_block_registered_in_default_registry() -> None:
    assert default_registry.get("REGION_COMPARE") is RegionCompareBlock


def test_region_compare_block_evaluate_changes_nothing() -> None:
    block = _compare_block()
    block.output("VALUE").set(0.7)
    block.output("Q").set(True)
    block.evaluate()
    assert block.output("VALUE").value == 0.7
    assert block.output("Q").value is True


# -- provider: PIXEL_COLOR -------------------------------------------------


def test_provider_pixel_match() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _pixel_block(color="60,200,60", tolerance=40)
    block.input("X").set(5)
    block.input("Y").set(5)
    _run_once(provider, block)
    assert block.output("VALUE").value == 1
    assert block.output("Q").value is True


def test_provider_pixel_mismatch() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _pixel_block(color="60,60,220", tolerance=40)
    block.input("X").set(5)
    block.input("Y").set(5)
    _run_once(provider, block)
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is False


def test_provider_pixel_out_of_bounds_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _pixel_block()
    block.input("X").set(100)
    block.input("Y").set(100)  # outside the 20x20 frame
    _run_once(provider, block)
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is False


def test_provider_pixel_negative_coords_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _pixel_block()
    block.input("X").set(-1)
    block.input("Y").set(0)
    _run_once(provider, block)
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is False


def test_provider_pixel_malformed_color_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _pixel_block(color="not,a,color")
    block.input("X").set(5)
    block.input("Y").set(5)
    _run_once(provider, block)
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is False


def test_provider_pixel_tolerance_boundary() -> None:
    # G channel differs by 100: match at tolerance 120, not at 40.
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _pixel_block(color="60,200,160", tolerance=40)
    block.input("X").set(5)
    block.input("Y").set(5)
    _run_once(provider, block)
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is False

    block.input("TOLERANCE").set(120)
    _run_once(provider, block)
    assert block.output("VALUE").value == 1
    assert block.output("Q").value is True


def test_provider_backend_failure_safe_for_pixel() -> None:
    provider = CaptureInputProvider(backend=_FailingBackend())
    block = _pixel_block()
    _run_once(provider, block)  # must not raise
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is False


# -- provider: REGION_COMPARE ----------------------------------------------


def test_provider_diff_detects_change() -> None:
    images = [_black_image(), _red_square_image()]
    backend = MockCaptureBackend(pattern_fn=lambda t: images[0])
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="diff", tolerance=0.1)

    _run_once(provider, block)  # first cycle: no previous frame yet
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False

    images[0] = images[1]  # second cycle: red square appears
    _run_once(provider, block)
    # 16 of 400 pixels changed -> 0.04 < 0.1 tolerance -> Q stays false
    assert abs(float(block.output("VALUE").value) - 16 / 400) < 1e-6
    assert block.output("Q").value is False

    block.input("TOLERANCE").set(0.01)
    images[0] = np.full((20, 20, 3), RED, dtype=np.uint8)  # fully red vs red square
    _run_once(provider, block)
    assert block.output("Q").value is True


def test_provider_diff_no_change() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _black_image())
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="diff")
    _run_once(provider, block)
    _run_once(provider, block)  # identical frame again
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_shape_ignores_colors() -> None:
    images = [_green_square_image(), _red_square_image()]
    backend = MockCaptureBackend(pattern_fn=lambda t: images[0])
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="shape", tolerance=0.1)

    _run_once(provider, block)  # no previous frame yet
    assert block.output("VALUE").value == 0.0

    images[0] = images[1]  # same shape, different color
    _run_once(provider, block)
    assert block.output("VALUE").value == pytest.approx(1.0, abs=1e-6)
    assert block.output("Q").value is True


def test_provider_shape_same_frames() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _black_image())
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="shape", tolerance=0.1)
    _run_once(provider, block)  # no previous frame yet
    _run_once(provider, block)  # identical empty frames
    assert block.output("VALUE").value == pytest.approx(1.0, abs=1e-6)


def _write_png(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image), f"could not write {path}"


def test_provider_template_mode(tmp_path: Path) -> None:
    template_path = tmp_path / "tpl_reference.png"
    _write_png(template_path, _green_square_image()[4:8, 6:10])
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_square_image())
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="template", reference=str(template_path))
    _run_once(provider, block)
    assert block.output("VALUE").value == pytest.approx(1.0, abs=1e-3)
    assert block.output("Q").value is True


def test_provider_template_missing_file_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="template", reference="/nonexistent/ref.png")
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_template_no_path_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="template")  # REFERENCE defaults to ""
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_unknown_mode_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(mode="weird")
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_malformed_region_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _compare_block(region="not,a,region")
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


# -- full scan cycle: PIXEL_COLOR -> OUTPUT --------------------------------


def test_scan_chain_pixel_to_output() -> None:
    images = [_green_image()]
    backend = MockCaptureBackend(pattern_fn=lambda t: images[0])
    capture_provider = CaptureInputProvider(backend=backend)
    output_device = _FakeUinputDevice("out-keyboard", [])
    uinput_provider = UinputOutputProvider(
        open_device_fn=lambda name, specs: output_device
    )

    pix = _pixel_block(color="60,200,60", tolerance=40)
    pix.input("X").set(5)
    pix.input("Y").set(5)
    out = _output_block("out-keyboard", "KEY_Q")

    graph = WireGraph()
    graph.add_block(pix)
    graph.add_block(out)
    graph.connect("pix1", "Q", "out1", "Q")

    executor = ScanExecutor(
        graph,
        read_inputs=capture_provider,
        write_outputs=uinput_provider,
    )

    # green frame: PIXEL_COLOR Q=True -> OUTPUT asserts KEY_Q (1)
    executor.run_once()
    assert output_device.events == [((ecodes.EV_KEY, ecodes.KEY_Q), 1, True)]

    # black frame: PIXEL_COLOR Q=False -> OUTPUT deasserts KEY_Q (0)
    images[0] = _black_image()
    executor.run_once()
    assert output_device.events[-1] == ((ecodes.EV_KEY, ecodes.KEY_Q), 0, True)
