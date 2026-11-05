"""evdev INPUT block + EvdevInputProvider: headless, fake devices only.

The provider polls via non-blocking ioctls (active_keys / absinfo), so every
test uses an evdev-like fake — no real /dev/input device required (T7).
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
)
from input_automation.engine.io_blocks import InputBlock
from input_automation.io.evdev import EvdevInputProvider, resolve_evdev_code


class _AbsInfo:
    """Minimal AbsInfo stand-in: evdev returns a namedtuple with .value."""

    def __init__(self, value: int) -> None:
        self.value = value


class _FakeDevice:
    """evdev-like device: programmable key state and axis values."""

    def __init__(
        self,
        name: str,
        pressed: frozenset[int] = frozenset(),
        axes: dict[int, int] | None = None,
        *,
        fail_open: bool = False,
    ) -> None:
        self.name = name
        self.phys = ""
        self.fd = 7
        self._pressed = set(pressed)
        self._axes = dict(axes or {})
        self.closed = False
        self.fail_open = fail_open

    def capabilities(self, verbose: bool = False) -> dict[int, list[int]]:
        caps: dict[int, list[int]] = {ecodes.EV_KEY: sorted(self._pressed)}
        if self._axes:
            caps[ecodes.EV_ABS] = sorted(self._axes)
        return caps

    def active_keys(self) -> set[int]:
        return set(self._pressed)

    def absinfo(self, axis: int) -> _AbsInfo:
        return _AbsInfo(self._axes.get(axis, 0))

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


def _input_block(device: str, code: str | None, block_id: str = "in1") -> InputBlock:
    block = InputBlock(block_id)
    block.input("DEVICE").set(device)
    block.input("CODE").set(code)
    return block


def _run_once(provider: EvdevInputProvider, **blocks: Block) -> None:
    graph = WireGraph()
    for block in blocks.values():
        graph.add_block(block)
    ScanExecutor(graph, read_inputs=provider).run_once()


# -- code resolution -----------------------------------------------------

def test_resolve_evdev_code_canonical_names() -> None:
    assert resolve_evdev_code("KEY_Q") == ecodes.KEY_Q
    assert resolve_evdev_code("BTN_LEFT") == ecodes.BTN_LEFT
    assert resolve_evdev_code("ABS_X") == ecodes.ABS_X


def test_resolve_evdev_code_short_key_form() -> None:
    assert resolve_evdev_code("q") == ecodes.KEY_Q
    assert resolve_evdev_code("Q") == ecodes.KEY_Q


def test_resolve_evdev_code_unknown() -> None:
    assert resolve_evdev_code("NOT_A_CODE") is None
    assert resolve_evdev_code("") is None
    assert resolve_evdev_code(None) is None


# -- INPUT block ---------------------------------------------------------

def test_input_block_pins() -> None:
    block = InputBlock("in1")
    assert block.type_name == "INPUT"
    assert set(block.inputs) == {"DEVICE", "CODE"}
    assert set(block.outputs) == {"Q", "VALUE"}
    assert block.input("DEVICE").dtype is PinDType.STRING
    assert block.input("CODE").dtype is PinDType.STRING
    assert block.output("Q").dtype is PinDType.BOOL
    assert block.output("VALUE").dtype is PinDType.INT
    assert block.input("DEVICE").value == ""
    assert block.input("CODE").value == ""
    assert block.output("VALUE").value == 0
    assert block.output("Q").value is None


def test_input_block_evaluate_is_pass_through() -> None:
    block = _input_block("/dev/input/event0", "KEY_Q")
    block.output("Q").set(True)
    block.output("VALUE").set(1)
    block.evaluate()
    # outputs hold whatever the IO provider wrote; evaluate changes nothing
    assert block.output("Q").value is True
    assert block.output("VALUE").value == 1


def test_input_block_registered_in_default_registry() -> None:
    from input_automation.engine import default_registry

    assert default_registry.get("INPUT") is InputBlock


# -- provider: keys/buttons ----------------------------------------------

def test_provider_writes_key_state() -> None:
    key_q = ecodes.KEY_Q
    device = _FakeDevice("event0", pressed=frozenset({key_q}))
    provider = EvdevInputProvider(open_device_fn=lambda path: device)

    source = _input_block("/dev/input/event0", "KEY_Q")
    and1 = _And("and1")
    and1.input("IN2").set(True)

    graph = WireGraph()
    graph.add_block(source)
    graph.add_block(and1)
    graph.connect("in1", "Q", "and1", "IN1")
    executor = ScanExecutor(graph, read_inputs=provider)

    executor.run_once()
    assert source.output("Q").value is True
    assert source.output("VALUE").value == 1
    assert and1.output("Q").value is True

    device._pressed = set()
    executor.run_once()
    assert source.output("Q").value is False
    assert source.output("VALUE").value == 0
    assert and1.output("Q").value is False


def test_provider_unknown_code_safe() -> None:
    provider = EvdevInputProvider(open_device_fn=lambda path: _FakeDevice("event0"))
    block = _input_block("/dev/input/event0", "NOT_A_CODE")
    _run_once(provider, in1=block)
    assert block.output("Q").value is False
    assert block.output("VALUE").value == 0


def test_provider_unopenable_device_safe() -> None:
    def boom(path: str) -> _FakeDevice:
        raise OSError("permission denied")

    provider = EvdevInputProvider(open_device_fn=boom)
    block = _input_block("/dev/input/event9", "KEY_Q")
    _run_once(provider, in1=block)
    assert block.output("Q").value is False
    assert block.output("VALUE").value == 0


def test_provider_no_input_blocks() -> None:
    provider = EvdevInputProvider(open_device_fn=lambda path: _FakeDevice("event0"))
    _run_once(provider)  # empty graph must not raise


# -- provider: absolute axes ---------------------------------------------

def test_provider_axis_state() -> None:
    device = _FakeDevice("event0", axes={ecodes.ABS_X: 42})
    provider = EvdevInputProvider(open_device_fn=lambda path: device)
    block = _input_block("/dev/input/event0", "ABS_X")
    _run_once(provider, in1=block)
    assert block.output("Q").value is True
    assert block.output("VALUE").value == 42


def test_provider_axis_at_center() -> None:
    device = _FakeDevice("event0", axes={ecodes.ABS_X: 0})
    provider = EvdevInputProvider(open_device_fn=lambda path: device)
    block = _input_block("/dev/input/event0", "ABS_X")
    _run_once(provider, in1=block)
    assert block.output("Q").value is False
    assert block.output("VALUE").value == 0
