"""Mock IO providers: full headless scan cycles without real devices (T10).

MockEvdevInputProvider / MockUinputOutputProvider replace /dev/input and
/dev/uinput with programmable state and event recording, so INPUT -> LOGIC ->
OUTPUT chains run under the plain ScanExecutor with no hardware and no
privileges.
"""

from __future__ import annotations

from evdev import ecodes

from input_automation.engine import (
    Block,
    InputReader,
    OutputWriter,
    ScanExecutor,
    WireGraph,
)
from input_automation.engine.io_blocks import InputBlock, OutputBlock
from input_automation.io.mock import MockEvdevInputProvider, MockUinputOutputProvider


def _input_block(device_path: str, code: str, block_id: str = "in1") -> InputBlock:
    block = InputBlock(block_id)
    block.input("DEVICE").set(device_path)
    block.input("CODE").set(code)
    return block


def _output_block(device: str, code: str, block_id: str = "out1") -> OutputBlock:
    block = OutputBlock(block_id)
    block.input("DEVICE").set(device)
    block.input("CODE").set(code)
    return block


def _scan(
    read_inputs: InputReader,
    write_outputs: OutputWriter,
    *blocks: Block,
) -> None:
    graph = WireGraph()
    for block in blocks:
        graph.add_block(block)
    ScanExecutor(graph, read_inputs=read_inputs, write_outputs=write_outputs).run_once()


def _read_only(provider: MockEvdevInputProvider, *blocks: Block) -> None:
    _scan(provider, lambda _graph: None, *blocks)


def _write_only(provider: MockUinputOutputProvider, *blocks: Block) -> None:
    _scan(lambda _graph: None, provider, *blocks)


# -- MockEvdevInputProvider -------------------------------------------------


def test_mock_evdev_press_and_release() -> None:
    provider = MockEvdevInputProvider()
    block = _input_block("/dev/input/event0", "KEY_Q")

    provider.press("/dev/input/event0", ecodes.KEY_Q)
    _read_only(provider, block)
    assert block.output("Q").value is True
    assert block.output("VALUE").value == 1

    provider.release("/dev/input/event0", ecodes.KEY_Q)
    _read_only(provider, block)
    assert block.output("Q").value is False
    assert block.output("VALUE").value == 0


def test_mock_evdev_axis() -> None:
    provider = MockEvdevInputProvider()
    block = _input_block("/dev/input/event0", "ABS_X")

    provider.set_axis("/dev/input/event0", ecodes.ABS_X, 42)
    _read_only(provider, block)
    assert block.output("Q").value is True
    assert block.output("VALUE").value == 42

    provider.set_axis("/dev/input/event0", ecodes.ABS_X, 0)
    _read_only(provider, block)
    assert block.output("Q").value is False
    assert block.output("VALUE").value == 0


def test_mock_evdev_unknown_device_safe() -> None:
    provider = MockEvdevInputProvider()
    block = _input_block("/dev/input/nope", "KEY_Q")
    _read_only(provider, block)  # must not raise
    assert block.output("Q").value is False
    assert block.output("VALUE").value == 0


def test_mock_evdev_unpressed_code_safe() -> None:
    provider = MockEvdevInputProvider()
    block = _input_block("/dev/input/event0", "KEY_R")
    _read_only(provider, block)
    assert block.output("Q").value is False


def test_mock_evdev_only_pressed_key_asserts() -> None:
    provider = MockEvdevInputProvider()
    key_q = _input_block("/dev/input/event0", "KEY_Q")
    key_r = _input_block("/dev/input/event0", "KEY_R", block_id="in2")

    provider.press("/dev/input/event0", ecodes.KEY_Q)
    _read_only(provider, key_q, key_r)
    assert key_q.output("Q").value is True
    assert key_r.output("Q").value is False


def test_mock_evdev_release_applies_next_cycle() -> None:
    provider = MockEvdevInputProvider()
    block = _input_block("/dev/input/event0", "KEY_Q")

    provider.press("/dev/input/event0", ecodes.KEY_Q)
    _read_only(provider, block)
    assert block.output("Q").value is True

    provider.release("/dev/input/event0", ecodes.KEY_Q)
    _read_only(provider, block)
    assert block.output("Q").value is False


def test_mock_evdev_clear() -> None:
    provider = MockEvdevInputProvider()
    block = _input_block("/dev/input/event0", "KEY_Q")

    provider.press("/dev/input/event0", ecodes.KEY_Q)
    provider.clear()
    _read_only(provider, block)
    assert block.output("Q").value is False


def test_mock_evdev_no_input_blocks() -> None:
    provider = MockEvdevInputProvider()
    _read_only(provider)  # empty graph must not raise


# -- MockUinputOutputProvider -----------------------------------------------


def test_mock_uinput_emits_key_press_and_release() -> None:
    sink = MockUinputOutputProvider()
    block = _output_block("out-keyboard", "KEY_Q")

    block.input("Q").set(True)
    _write_only(sink, block)
    assert sink.device_events("out-keyboard") == [
        ((ecodes.EV_KEY, ecodes.KEY_Q), 1, True)
    ]

    block.input("Q").set(False)
    _write_only(sink, block)
    assert sink.device_events("out-keyboard") == [
        ((ecodes.EV_KEY, ecodes.KEY_Q), 1, True),
        ((ecodes.EV_KEY, ecodes.KEY_Q), 0, True),
    ]


def test_mock_uinput_only_emits_on_change() -> None:
    sink = MockUinputOutputProvider()
    block = _output_block("out-keyboard", "KEY_Q")

    block.input("Q").set(True)
    _write_only(sink, block)
    _write_only(sink, block)  # held: no repeat
    assert len(sink.events) == 1

    block.input("Q").set(False)
    _write_only(sink, block)
    assert len(sink.events) == 2


def test_mock_uinput_axis_emits_raw_value() -> None:
    sink = MockUinputOutputProvider()
    block = _output_block("out-axis", "ABS_X")

    block.input("Q").set(True)
    block.input("VALUE").set(42)
    _write_only(sink, block)
    assert sink.events == [((ecodes.EV_ABS, ecodes.ABS_X), 42, True)]

    block.input("Q").set(False)
    _write_only(sink, block)
    assert sink.events[-1] == ((ecodes.EV_ABS, ecodes.ABS_X), 0, True)


def test_mock_uinput_unknown_code_safe() -> None:
    sink = MockUinputOutputProvider()
    block = _output_block("out-keyboard", "NOT_A_CODE")
    _write_only(sink, block)
    assert sink.events == []


def test_mock_uinput_clear() -> None:
    sink = MockUinputOutputProvider()
    block = _output_block("out-keyboard", "KEY_Q")
    block.input("Q").set(True)
    _write_only(sink, block)
    assert len(sink.events) == 1
    sink.clear()
    assert sink.events == []


def test_mock_uinput_close_marks_devices_closed() -> None:
    sink = MockUinputOutputProvider()
    block = _output_block("out-keyboard", "KEY_Q")
    block.input("Q").set(True)
    _write_only(sink, block)
    device = sink._devices.get("out-keyboard")
    assert device is not None
    sink.close()
    assert device.closed
    assert len(sink._devices) == 0


def test_mock_uinput_no_output_blocks() -> None:
    sink = MockUinputOutputProvider()
    _write_only(sink)  # empty graph must not raise


# -- full scan cycle: INPUT -> OUTPUT --------------------------------------


def test_scan_chain_input_to_output() -> None:
    provider = MockEvdevInputProvider()
    sink = MockUinputOutputProvider()
    source = _input_block("/dev/input/event0", "KEY_Q")
    out = _output_block("out-keyboard", "KEY_Q")

    graph = WireGraph()
    graph.add_block(source)
    graph.add_block(out)
    graph.connect("in1", "Q", "out1", "Q")
    executor = ScanExecutor(
        graph, read_inputs=provider, write_outputs=sink
    )

    provider.press("/dev/input/event0", ecodes.KEY_Q)
    executor.run_once()
    assert sink.device_events("out-keyboard") == [
        ((ecodes.EV_KEY, ecodes.KEY_Q), 1, True)
    ]

    provider.release("/dev/input/event0", ecodes.KEY_Q)
    executor.run_once()
    assert sink.device_events("out-keyboard")[-1] == (
        (ecodes.EV_KEY, ecodes.KEY_Q),
        0,
        True,
    )
