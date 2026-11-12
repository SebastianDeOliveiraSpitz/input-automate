"""Engine: typed pins, Block base class, block registry."""

import pytest

from input_automation.engine import (
    Block,
    BlockRegistry,
    Pin,
    PinDirection,
    PinDType,
    PinTypeError,
    default_registry,
)
from input_automation.project.pins import PinDirection as SchemaPinDirection


class _AndBlock(Block):
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


class _PassThrough(Block):
    type_name = "PASS"
    input_pins = (Pin("IN", PinDirection.INPUT, PinDType.INT, default=0),)
    output_pins = (Pin("OUT", PinDirection.OUTPUT, PinDType.INT),)

    def evaluate(self) -> None:
        self.output("OUT").set(self.input("IN").value)


def test_pin_reuses_schema_enums() -> None:
    assert PinDirection is SchemaPinDirection


def pin_factory() -> Pin:
    return Pin("X", PinDirection.INPUT, PinDType.INT, default=7)


def test_pin_stores_and_validates_values() -> None:
    pin = pin_factory()
    assert pin.value == 7
    pin.set(42)
    assert pin.value == 42
    pin.set(None)
    assert pin.value is None


def test_pin_rejects_wrong_type() -> None:
    pin = pin_factory()
    with pytest.raises(PinTypeError):
        pin.set("not an int")
    # booleans are not integers for INT pins
    with pytest.raises(PinTypeError):
        pin.set(True)
    assert pin.value == 7  # unchanged after rejected writes


def test_pin_bool_excludes_ints() -> None:
    pin = Pin("B", PinDirection.INPUT, PinDType.BOOL)
    pin.set(True)
    assert pin.value is True
    with pytest.raises(PinTypeError):
        pin.set(1)


def test_block_is_abstract() -> None:
    with pytest.raises(TypeError):
        Block("b1")


def test_block_exposes_own_pin_copies() -> None:
    block = _AndBlock("and1")
    assert block.id == "and1"
    assert block.type_name == "AND"
    assert set(block.inputs) == {"IN1", "IN2"}
    assert set(block.outputs) == {"Q"}
    # two instances never share pin state
    a, b = _AndBlock("a"), _AndBlock("b")
    a.input("IN1").set(True)
    assert b.input("IN1").value is None


def test_block_unknown_pin_raises_key_error() -> None:
    block = _AndBlock("and1")
    with pytest.raises(KeyError):
        block.input("NOPE")
    with pytest.raises(KeyError):
        block.output("NOPE")


def test_block_evaluate_and_reset() -> None:
    block = _AndBlock("and1")
    block.input("IN1").set(True)
    block.input("IN2").set(False)
    block.evaluate()
    assert block.output("Q").value is False
    block.input("IN2").set(True)
    block.evaluate()
    assert block.output("Q").value is True
    block.reset()
    assert block.output("Q").value is None


def test_pass_through_keeps_default() -> None:
    block = _PassThrough("p1")
    assert block.input("IN").value == 0
    block.evaluate()
    assert block.output("OUT").value == 0
    block.input("IN").set(9)
    block.evaluate()
    assert block.output("OUT").value == 9


def test_registry_register_create_lookup() -> None:
    registry = BlockRegistry()
    registry.register("AND", _AndBlock)
    assert registry.has("AND")
    assert "AND" in registry.names()
    block = registry.create("AND", "and1")
    assert isinstance(block, _AndBlock)
    assert block.id == "and1"
    assert registry.get("AND") is _AndBlock


def test_registry_duplicate_rejected() -> None:
    registry = BlockRegistry()
    registry.register("AND", _AndBlock)
    with pytest.raises(ValueError):
        registry.register("AND", _PassThrough)


def test_registry_unknown_raises_key_error() -> None:
    registry = BlockRegistry()
    with pytest.raises(KeyError):
        registry.get("NOPE")
    with pytest.raises(KeyError):
        registry.create("NOPE", "x")
    with pytest.raises(KeyError):
        registry.deregister("NOPE")


def test_registry_rejects_non_block() -> None:
    registry = BlockRegistry()
    with pytest.raises(TypeError):
        registry.register("NOTABLOCK", int)  # type: ignore[arg-type]


def test_fresh_registry_starts_empty() -> None:
    assert BlockRegistry().names() == []


def test_default_registry_contains_function_blocks() -> None:
    names = default_registry.names()
    for name in ("AND", "OR", "NOT", "XOR", "SR", "TON", "CTU"):
        assert name in names
    # and they instantiate through the registry
    for name in names:
        block = default_registry.create(name, "t")
        assert isinstance(block, Block)


def test_timer_blocks_have_timer_category() -> None:
    # The GUI palette categorizes blocks via this attribute.
    for name in ("TON", "TOF", "TP", "CTU", "CLK"):
        block = default_registry.create(name, "t")
        assert block.category == "Timer"


def test_tof_holds_q_after_in_falls() -> None:
    block = default_registry.create("TOF", "tof1")
    block.input("PT").set(1.0)
    block.input("DT").set(0.25)
    block.input("IN").set(True)
    block.evaluate()
    assert block.output("Q").value is True
    assert block.output("ET").value == 0.0
    block.input("IN").set(False)
    for _ in range(3):
        block.evaluate()
    # ET = 0.75 < PT -> Q still true
    assert block.output("Q").value is True
    assert block.output("ET").value == 0.75
    block.evaluate()  # ET = 1.0 >= PT
    assert block.output("Q").value is False
    assert block.output("ET").value == 1.0
    # IN true again resets the hold time
    block.input("IN").set(True)
    block.evaluate()
    assert block.output("Q").value is True
    assert block.output("ET").value == 0.0


def test_tp_pulse_width_on_rising_edge() -> None:
    block = default_registry.create("TP", "tp1")
    block.input("PT").set(1.0)
    block.input("DT").set(0.25)
    block.input("IN").set(False)
    block.evaluate()
    assert block.output("Q").value is False
    block.input("IN").set(True)  # rising edge starts the pulse
    block.evaluate()
    assert block.output("Q").value is True
    assert block.output("ET").value == 0.25
    block.input("IN").set(False)  # IN low, pulse continues
    block.evaluate()  # ET = 0.5
    block.evaluate()  # ET = 0.75
    assert block.output("Q").value is True
    assert block.output("ET").value == 0.75
    block.evaluate()  # ET = 1.0 >= PT -> pulse done
    assert block.output("Q").value is False
    assert block.output("ET").value == 1.0
    # a second rising edge restarts the pulse
    block.input("IN").set(True)
    block.evaluate()
    assert block.output("Q").value is True
    assert block.output("ET").value == 0.25


def test_clk_fires_true_each_cycle() -> None:
    block = default_registry.create("CLK", "clk1")
    block.input("PERIOD").set(1.0)
    block.input("ON").set(0.5)
    block.input("DT").set(0.25)
    values = []
    for _ in range(12):
        block.evaluate()
        values.append(block.output("Q").value)
    # phase: 0.25, 0.5, 0.75, 0.0, ... -> Q true while phase < 0.5
    assert values == [True, False, False, True] * 3
    # tick 12 lands exactly on the period boundary -> phase wraps to 0.0
    assert block.output("ET").value == 0.0
    # IN false disables the clock and resets the phase
    block.input("IN").set(False)
    block.evaluate()
    assert block.output("Q").value is False
    assert block.output("ET").value == 0.0
