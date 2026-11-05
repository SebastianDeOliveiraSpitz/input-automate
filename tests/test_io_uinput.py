"""uinput OUTPUT block + UinputOutputProvider: headless, fake devices only.

The provider emits through an injectable opener; tests use a recording fake —
no /dev/uinput access required (T8).
"""

from __future__ import annotations

from evdev import ecodes

from input_automation.engine import (
    Block,
    Pin,
    PinDirection,
    PinDType,
    ScanExecutor,
    WireGraph,
    default_registry,
)
from input_automation.engine.blocks import NOTBlock
from input_automation.engine.io_blocks import InputBlock, OutputBlock
from input_automation.io.evdev import EvdevInputProvider
from input_automation.io.uinput import UinputOutputProvider


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


class _FakeDevice:
    """evdev-like device: programmable key state and axis values."""

    def __init__(
        self,
        name: str,
        pressed: frozenset[int] = frozenset(),
        axes: dict[int, int] | None = None,
    ) -> None:
        self.name = name
        self.phys = ""
        self.fd = 7
        self._pressed = set(pressed)
        self._axes = dict(axes or {})
        self.closed = False

    def capabilities(self, verbose: bool = False) -> dict[int, list[int]]:
        caps: dict[int, list[int]] = {ecodes.EV_KEY: sorted(self._pressed)}
        if self._axes:
            caps[ecodes.EV_ABS] = sorted(self._axes)
        return caps

    def active_keys(self) -> set[int]:
        return set(self._pressed)

    def absinfo(self, axis: int) -> int:
        return self._axes.get(axis, 0)

    def close(self) -> None:
        self.closed = True


class _And(Block):
    type_name = "AND"
    input_pins = (
        Pin("IN1", PinDirection.INPUT, PinDType.BOOL),
        Pin("IN2", PinDirection.INPUT, PinDType.BOOL),
    )
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def evaluate(self) -> None:
        self.output("Q").set(
            bool(self.input("IN1").value) and bool(self.input("IN2").value)
        )


def _output_block(device: str, code: str, block_id: str = "out1") -> OutputBlock:
    block = OutputBlock(block_id)
    block.input("DEVICE").set(device)
    block.input("CODE").set(code)
    return block


def _run_once(
    provider: UinputOutputProvider, *blocks: Block
) -> None:
    graph = WireGraph()
    for block in blocks:
        graph.add_block(block)
    ScanExecutor(graph, write_outputs=provider).run_once()


# -- OUTPUT block ---------------------------------------------------------

def test_output_block_pins() -> None:
    block = OutputBlock("out1")
    assert block.type_name == "OUTPUT"
    assert set(block.inputs) == {"DEVICE", "CODE", "Q", "VALUE"}
    assert set(block.outputs) == set()
    assert block.input("DEVICE").dtype is PinDType.STRING
    assert block.input("CODE").dtype is PinDType.STRING
    assert block.input("Q").dtype is PinDType.BOOL
    assert block.input("VALUE").dtype is PinDType.INT
    assert block.input("DEVICE").value == ""
    assert block.input("CODE").value == ""
    assert block.input("Q").value is False
    assert block.input("VALUE").value == 1


def test_output_block_registered_in_default_registry() -> None:
    assert default_registry.get("OUTPUT") is OutputBlock


def test_output_block_evaluate_changes_nothing() -> None:
    block = _output_block("out-keyboard", "KEY_Q")
    block.input("Q").set(True)
    block.input("VALUE").set(5)
    block.evaluate()
    assert block.input("Q").value is True
    assert block.input("VALUE").value == 5


# -- provider: keys/buttons -----------------------------------------------

def test_provider_emits_key_press_and_release() -> None:
    device = _FakeUinputDevice("out-keyboard", [])
    provider = UinputOutputProvider(open_device_fn=lambda name, specs: device)
    block = _output_block("out-keyboard", "KEY_Q")

    block.input("Q").set(True)
    _run_once(provider, block)
    assert device.events == [((ecodes.EV_KEY, ecodes.KEY_Q), 1, True)]

    block.input("Q").set(False)
    _run_once(provider, block)
    assert device.events == [
        ((ecodes.EV_KEY, ecodes.KEY_Q), 1, True),
        ((ecodes.EV_KEY, ecodes.KEY_Q), 0, True),
    ]


def test_provider_only_emits_on_change() -> None:
    device = _FakeUinputDevice("out-keyboard", [])
    provider = UinputOutputProvider(open_device_fn=lambda name, specs: device)
    block = _output_block("out-keyboard", "KEY_Q")

    block.input("Q").set(True)
    _run_once(provider, block)
    _run_once(provider, block)  # held down: no repeat events
    assert len(device.events) == 1

    block.input("Q").set(False)
    _run_once(provider, block)
    assert len(device.events) == 2


def test_provider_button_code_is_ev_key() -> None:
    device = _FakeUinputDevice("out-mouse", [])
    provider = UinputOutputProvider(open_device_fn=lambda name, specs: device)
    block = _output_block("out-mouse", "BTN_LEFT")
    block.input("Q").set(True)
    _run_once(provider, block)
    assert device.events == [((ecodes.EV_KEY, ecodes.BTN_LEFT), 1, True)]

    block.input("Q").set(False)
    _run_once(provider, block)
    assert device.events[-1] == ((ecodes.EV_KEY, ecodes.BTN_LEFT), 0, True)


def test_provider_short_key_form() -> None:
    device = _FakeUinputDevice("out-keyboard", [])
    provider = UinputOutputProvider(open_device_fn=lambda name, specs: device)
    block = _output_block("out-keyboard", "q")
    block.input("Q").set(True)
    _run_once(provider, block)
    assert device.events == [((ecodes.EV_KEY, ecodes.KEY_Q), 1, True)]


# -- provider: absolute axes ----------------------------------------------

def test_provider_axis_emits_raw_value() -> None:
    device = _FakeUinputDevice("out-axis", [])
    provider = UinputOutputProvider(open_device_fn=lambda name, specs: device)
    block = _output_block("out-axis", "ABS_X")
    block.input("Q").set(True)
    block.input("VALUE").set(42)
    _run_once(provider, block)
    assert device.events == [((ecodes.EV_ABS, ecodes.ABS_X), 42, True)]

    block.input("Q").set(False)
    _run_once(provider, block)
    assert device.events[-1] == ((ecodes.EV_ABS, ecodes.ABS_X), 0, True)


# -- safety ---------------------------------------------------------------

def test_provider_unknown_code_safe() -> None:
    device = _FakeUinputDevice("out-keyboard", [])
    provider = UinputOutputProvider(open_device_fn=lambda name, specs: device)
    block = _output_block("out-keyboard", "NOT_A_CODE")
    _run_once(provider, block)
    assert device.events == []
    assert not device.closed


def test_provider_unopenable_device_safe() -> None:
    def boom(name: str, specs: list[tuple[int, object]]) -> _FakeUinputDevice:
        raise OSError("permission denied")

    provider = UinputOutputProvider(open_device_fn=boom)
    block = _output_block("out-keyboard", "KEY_Q")
    _run_once(provider, block)  # must not raise
    assert provider._devices == {}  # nothing cached on failure


def test_provider_no_output_blocks() -> None:
    provider = UinputOutputProvider(
        open_device_fn=lambda name, specs: _FakeUinputDevice(name, [])
    )
    _run_once(provider)  # empty graph must not raise


def test_provider_devices_are_cached_and_closed() -> None:
    device = _FakeUinputDevice("out-keyboard", [])
    provider = UinputOutputProvider(open_device_fn=lambda name, specs: device)
    block = _output_block("out-keyboard", "KEY_Q")
    block.input("Q").set(True)
    _run_once(provider, block)
    _run_once(provider, block)
    assert len(provider._devices) == 1  # one device for both cycles
    provider.close()
    assert device.closed
    assert len(provider._devices) == 0


# -- full scan cycle: INPUT -> NOT -> OUTPUT ------------------------------

def test_scan_chain_input_not_output() -> None:
    input_device = _FakeDevice("event0", pressed=frozenset({ecodes.KEY_Q}))
    output_device = _FakeUinputDevice("out-keyboard", [])

    evdev_provider = EvdevInputProvider(
        open_device_fn=lambda path: input_device
    )
    uinput_provider = UinputOutputProvider(
        open_device_fn=lambda name, specs: output_device
    )

    source = InputBlock("in1")
    source.input("DEVICE").set("/dev/input/event0")
    source.input("CODE").set("KEY_Q")
    inverter = NOTBlock("inv1")
    out = OutputBlock("out1")
    out.input("DEVICE").set("out-keyboard")
    out.input("CODE").set("KEY_Q")

    graph = WireGraph()
    graph.add_block(source)
    graph.add_block(inverter)
    graph.add_block(out)
    graph.connect("in1", "Q", "inv1", "IN")
    graph.connect("inv1", "Q", "out1", "Q")

    executor = ScanExecutor(
        graph, read_inputs=evdev_provider, write_outputs=uinput_provider
    )

    # key pressed: INPUT Q=True -> NOT Q=False -> OUTPUT deasserted (0)
    executor.run_once()
    assert output_device.events == [((ecodes.EV_KEY, ecodes.KEY_Q), 0, True)]

    # key released: INPUT Q=False -> NOT Q=True -> OUTPUT asserted (1)
    input_device._pressed = set()
    executor.run_once()
    assert output_device.events[-1] == ((ecodes.EV_KEY, ecodes.KEY_Q), 1, True)
