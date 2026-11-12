"""CAPTURE block + CaptureInputProvider: headless, mock frames only (T9).

The provider grabs through an injected backend; tests use MockCaptureBackend
with fixed synthetic frames -- no display, PipeWire, or mutter session
required.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from evdev import ecodes

from input_automation.capture.mock import MockCaptureBackend
from input_automation.engine import (
    Block,
    PinDType,
    ScanExecutor,
    WireGraph,
    default_registry,
)
from input_automation.engine.io_blocks import CaptureBlock, OutputBlock
from input_automation.io.capture import CaptureInputProvider
from input_automation.io.uinput import UinputOutputProvider

GREEN = (60, 200, 60)  # BGR


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


class _FailingBackend:
    """Backend whose grab() always raises."""

    def grab(self, timeout: float = 10.0) -> object:
        raise RuntimeError("capture backend is gone")


def _capture_block(
    block_id: str = "cap1",
    region: str = "",
    mode: str = "color",
    color: str = "60,200,60",
    tolerance: int = 40,
    limit: float = 0.5,
) -> CaptureBlock:
    block = CaptureBlock(block_id)
    block.input("REGION").set(region)
    block.input("MODE").set(mode)
    block.input("COLOR").set(color)
    block.input("TOLERANCE").set(tolerance)
    block.input("LIMIT").set(limit)
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


def _half_green_image() -> np.ndarray:
    image = _black_image()
    image[:, :10] = GREEN
    return image


# -- block ------------------------------------------------------------------


def test_capture_block_pins() -> None:
    block = CaptureBlock("cap1")
    assert block.type_name == "CAPTURE"
    assert set(block.inputs) == {
        "REGION",
        "MODE",
        "COLOR",
        "TOLERANCE",
        "LIMIT",
        "UNIT",
        "TEMPLATE",
    }
    assert set(block.outputs) == {"VALUE", "Q"}
    assert block.input("REGION").dtype is PinDType.STRING
    assert block.input("MODE").dtype is PinDType.STRING
    assert block.input("COLOR").dtype is PinDType.STRING
    assert block.input("TOLERANCE").dtype is PinDType.INT
    assert block.input("LIMIT").dtype is PinDType.FLOAT
    assert block.output("VALUE").dtype is PinDType.FLOAT
    assert block.output("Q").dtype is PinDType.BOOL
    assert block.input("REGION").value == ""
    assert block.input("MODE").value == "color"
    assert block.input("COLOR").value == ""
    assert block.input("TOLERANCE").value == 40
    assert block.input("LIMIT").value == 0.5
    assert block.input("UNIT").value == "pixels"
    assert block.input("TEMPLATE").value == ""


def test_capture_block_registered_in_default_registry() -> None:
    assert default_registry.get("CAPTURE") is CaptureBlock


def test_capture_block_evaluate_changes_nothing() -> None:
    block = _capture_block()
    block.output("VALUE").set(0.7)
    block.output("Q").set(True)
    block.evaluate()
    assert block.output("VALUE").value == 0.7
    assert block.output("Q").value is True


# -- provider: color fraction ----------------------------------------------


def test_provider_measures_whole_frame() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(color="60,200,60", tolerance=40, limit=0.5)
    _run_once(provider, block)
    assert block.output("VALUE").value == 1.0
    assert block.output("Q").value is True

    backend = MockCaptureBackend(pattern_fn=lambda t: _black_image())
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(color="60,200,60", tolerance=40, limit=0.5)
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_measures_region() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _half_green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(color="60,200,60", tolerance=40)
    block.input("REGION").set("0,0,10,20")  # left half: all green
    _run_once(provider, block)
    assert block.output("VALUE").value == 1.0

    block.input("REGION").set("10,0,10,20")  # right half: all black
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0


def test_provider_threshold_mode() -> None:
    backend = MockCaptureBackend(
        pattern_fn=lambda t: np.full((20, 20, 3), (128, 128, 128), dtype=np.uint8)
    )
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(mode="threshold", limit=0.5)
    _run_once(provider, block)
    assert abs(float(block.output("VALUE").value) - 128 / 255) < 1e-6
    assert block.output("Q").value is True

    block.input("LIMIT").set(0.6)
    _run_once(provider, block)
    assert block.output("Q").value is False


# -- safety -----------------------------------------------------------------


def test_provider_malformed_region_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)

    block = _capture_block(region="not,a,region")
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False

    block.input("REGION").set("0,0,1000,1000")  # outside the 20x20 frame
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_malformed_color_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(color="not,a,color")
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_unknown_mode_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(mode="weird")
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_backend_failure_safe() -> None:
    provider = CaptureInputProvider(backend=_FailingBackend())
    block = _capture_block()
    _run_once(provider, block)  # must not raise
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_ignores_non_capture_blocks() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    _run_once(provider)  # empty graph must not raise


# -- full scan cycle: CAPTURE -> OUTPUT -------------------------------------


def test_scan_chain_capture_to_output() -> None:
    images = [_green_image()]
    backend = MockCaptureBackend(pattern_fn=lambda t: images[0])
    capture_provider = CaptureInputProvider(backend=backend)
    output_device = _FakeUinputDevice("out-keyboard", [])
    uinput_provider = UinputOutputProvider(
        open_device_fn=lambda name, specs: output_device
    )

    cap = _capture_block(color="60,200,60", tolerance=40, limit=0.5)
    out = _output_block("out-keyboard", "KEY_Q")

    graph = WireGraph()
    graph.add_block(cap)
    graph.add_block(out)
    graph.connect("cap1", "Q", "out1", "Q")

    executor = ScanExecutor(
        graph, read_inputs=capture_provider, write_outputs=uinput_provider
    )

    # green frame: CAPTURE Q=True -> OUTPUT asserts KEY_Q (1)
    executor.run_once()
    assert output_device.events == [((ecodes.EV_KEY, ecodes.KEY_Q), 1, True)]

    # black frame: CAPTURE Q=False -> OUTPUT deasserts KEY_Q (0)
    images[0] = _black_image()
    executor.run_once()
    assert output_device.events[-1] == ((ecodes.EV_KEY, ecodes.KEY_Q), 0, True)


def _write_png(path: Path, image: np.ndarray) -> None:
    assert cv2.imwrite(str(path), image), f"could not write {path}"


# -- new measurement modes -------------------------------------------------


def test_provider_template_mode(tmp_path: Path) -> None:
    """Exact template inside the region → score 1.0, Q true at default limit."""
    template_path = tmp_path / "tpl_template.png"
    template = np.full((4, 4, 3), GREEN, dtype=np.uint8)
    _write_png(template_path, template)
    image = _black_image()  # 20x20 black with a green 4x4 block at (6, 4)
    image[4:8, 6:10] = GREEN
    backend = MockCaptureBackend(pattern_fn=lambda t: image)
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(mode="template")
    block.input("TEMPLATE").set(str(template_path))
    block.input("REGION").set("0,0,20,20")
    _run_once(provider, block)
    assert block.output("VALUE").value == pytest.approx(1.0, abs=1e-3)
    assert block.output("Q").value is True


def test_provider_template_missing_file_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(mode="template")
    block.input("TEMPLATE").set("/nonexistent/template.png")
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_template_no_path_safe() -> None:
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(mode="template")  # TEMPLATE defaults to ""
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False


def test_provider_bar_mode() -> None:
    """Fraction of columns that are at least half filled → fill fraction."""
    image = _black_image()  # 20x20; first 8 of 20 columns green → 0.4
    image[:, :8] = GREEN
    backend = MockCaptureBackend(pattern_fn=lambda t: image)
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(mode="bar", color="60,200,60", tolerance=40)
    block.input("REGION").set("0,0,20,20")
    _run_once(provider, block)
    assert block.output("VALUE").value == pytest.approx(0.4)
    assert block.output("Q").value is False  # 0.4 < default limit 0.5

    block.input("LIMIT").set(0.4)
    _run_once(provider, block)
    assert block.output("Q").value is True


def test_provider_percent_unit_conversion() -> None:
    """Percent REGION is scaled to frame pixels before measuring."""
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())  # 20x20 green
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(color="60,200,60", tolerance=40)
    block.input("UNIT").set("percent")
    block.input("REGION").set("50,50,50,50")  # → 10,10,10,10 px, all green
    _run_once(provider, block)
    assert block.output("VALUE").value == pytest.approx(1.0)


def test_provider_percent_unit_out_of_bounds_safe() -> None:
    """Percent REGION that lands out of bounds → safe VALUE=0.0, Q=False."""
    backend = MockCaptureBackend(pattern_fn=lambda t: _green_image())  # 20x20
    provider = CaptureInputProvider(backend=backend)
    block = _capture_block(color="60,200,60", tolerance=40)
    block.input("UNIT").set("percent")
    block.input("REGION").set("90,90,20,20")  # → 18,18,4,4 px → 22 > 20 out of bounds
    _run_once(provider, block)
    assert block.output("VALUE").value == 0.0
    assert block.output("Q").value is False
