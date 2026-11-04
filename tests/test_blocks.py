"""Basic function blocks (T4): truth tables and sequential behavior."""

import pytest

from input_automation.engine import (
    ANDBlock,
    BlockRegistry,
    CTUBlock,
    EQBlock,
    GTBlock,
    LTBlock,
    MUXBlock,
    NEBlock,
    NOTBlock,
    ORBlock,
    PassThroughBlock,
    PinDType,
    SRBlock,
    ThresholdBlock,
    TONBlock,
    XORBlock,
    default_registry,
)


def _set(block, name: str, value) -> None:
    block.input(name).set(value)


def _q(block) -> bool:
    return bool(block.output("Q").value)


@pytest.mark.parametrize(
    ("in1", "in2", "expected"),
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_and_truth_table(in1, in2, expected) -> None:
    block = ANDBlock("a1")
    _set(block, "IN1", in1)
    _set(block, "IN2", in2)
    block.evaluate()
    assert _q(block) is expected


@pytest.mark.parametrize(
    ("in1", "in2", "expected"),
    [
        (False, False, False),
        (False, True, True),
        (True, False, True),
        (True, True, True),
    ],
)
def test_or_truth_table(in1, in2, expected) -> None:
    block = ORBlock("o1")
    _set(block, "IN1", in1)
    _set(block, "IN2", in2)
    block.evaluate()
    assert _q(block) is expected


@pytest.mark.parametrize(
    ("in1", "in2", "expected"),
    [
        (False, False, False),
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_xor_truth_table(in1, in2, expected) -> None:
    block = XORBlock("x1")
    _set(block, "IN1", in1)
    _set(block, "IN2", in2)
    block.evaluate()
    assert _q(block) is expected


@pytest.mark.parametrize(("inp", "expected"), [(False, True), (True, False)])
def test_not(inp, expected) -> None:
    block = NOTBlock("n1")
    _set(block, "IN", inp)
    block.evaluate()
    assert _q(block) is expected


def test_unwired_pins_treat_none_as_false() -> None:
    block = ANDBlock("a1")
    # nothing wired, nothing set: IN1/IN2 read None -> false
    block.evaluate()
    assert _q(block) is False


def test_sr_initial_and_hold() -> None:
    block = SRBlock("s1")
    block.evaluate()
    assert _q(block) is False
    _set(block, "S", False)
    _set(block, "R", False)
    block.evaluate()
    assert _q(block) is False


def test_sr_set_and_hold() -> None:
    block = SRBlock("s1")
    _set(block, "S", True)
    _set(block, "R", False)
    block.evaluate()
    assert _q(block) is True
    # S deasserted: Q holds
    _set(block, "S", False)
    block.evaluate()
    assert _q(block) is True


def test_sr_reset() -> None:
    block = SRBlock("s1")
    _set(block, "S", True)
    block.evaluate()
    assert _q(block) is True
    _set(block, "S", False)
    _set(block, "R", True)
    block.evaluate()
    assert _q(block) is False
    # R deasserted: Q stays reset
    _set(block, "R", False)
    block.evaluate()
    assert _q(block) is False


def test_sr_reset_priority_when_both_active() -> None:
    block = SRBlock("s1")
    _set(block, "S", True)
    _set(block, "R", True)
    block.evaluate()
    assert _q(block) is False


def test_sr_reset_output_pin_default() -> None:
    block = SRBlock("s1")
    assert block.output("Q").value is False


def test_ton_does_not_advance_while_in_is_false() -> None:
    block = TONBlock("t1")
    _set(block, "DT", 0.5)
    block.evaluate()
    assert _q(block) is False
    assert block.output("ET").value == 0.0


def test_ton_advances_and_fires_after_preset() -> None:
    block = TONBlock("t1")
    _set(block, "PT", 1.5)
    _set(block, "DT", 0.5)  # exact in binary floating point
    _set(block, "IN", True)
    block.evaluate()
    assert block.output("ET").value == 0.5
    assert _q(block) is False
    block.evaluate()
    assert block.output("ET").value == 1.0
    assert _q(block) is False
    block.evaluate()
    assert block.output("ET").value == 1.5
    assert _q(block) is True


def test_ton_default_preset_is_one_second() -> None:
    block = TONBlock("t1")
    _set(block, "IN", True)
    _set(block, "DT", 1.0)
    block.evaluate()
    assert block.output("ET").value == 1.0
    assert _q(block) is True


def test_ton_dt_default_zero_never_fires() -> None:
    block = TONBlock("t1")
    _set(block, "IN", True)
    # DT left unwired: defaults to 0.0
    for _ in range(10):
        block.evaluate()
    assert block.output("ET").value == 0.0
    assert _q(block) is False


def test_ton_resets_when_in_drops() -> None:
    block = TONBlock("t1")
    _set(block, "PT", 1.0)
    _set(block, "DT", 0.6)
    _set(block, "IN", True)
    block.evaluate()  # ET = 0.6
    _set(block, "IN", False)
    block.evaluate()
    assert block.output("ET").value == 0.0
    assert _q(block) is False


def test_ton_reset_restores_state() -> None:
    block = TONBlock("t1")
    _set(block, "PT", 1.0)
    _set(block, "DT", 0.6)
    _set(block, "IN", True)
    block.evaluate()
    block.reset()
    assert block.output("ET").value == 0.0
    assert _q(block) is False


def test_ctu_counts_rising_edges_only() -> None:
    block = CTUBlock("c1")
    _set(block, "PV", 3)
    _set(block, "CU", True)
    block.evaluate()  # rising edge -> CV = 1
    assert block.output("CV").value == 1
    assert _q(block) is False
    block.evaluate()  # level held: no new count
    assert block.output("CV").value == 1
    _set(block, "CU", False)
    block.evaluate()
    assert block.output("CV").value == 1
    _set(block, "CU", True)
    block.evaluate()  # rising edge -> CV = 2
    assert block.output("CV").value == 2
    _set(block, "CU", False)
    block.evaluate()
    _set(block, "CU", True)
    block.evaluate()  # rising edge -> CV = 3 -> Q fires
    assert block.output("CV").value == 3
    assert _q(block) is True


def test_ctu_reset() -> None:
    block = CTUBlock("c1")
    _set(block, "PV", 3)
    _set(block, "CU", True)
    block.evaluate()
    block.evaluate()  # no second count while held
    _set(block, "CU", True)
    _set(block, "R", True)
    block.evaluate()  # reset wins over CU
    assert block.output("CV").value == 0
    assert _q(block) is False
    _set(block, "R", False)
    block.evaluate()
    assert block.output("CV").value == 0


def test_ctu_default_preset_zero_q_immediately() -> None:
    block = CTUBlock("c1")
    block.evaluate()
    assert _q(block) is True  # CV = 0 >= PV = 0


def test_ctu_reset_restores_previous_cu() -> None:
    block = CTUBlock("c1")
    _set(block, "CU", True)
    block.evaluate()  # CV = 1
    block.reset()
    _set(block, "CU", True)
    block.evaluate()  # rising edge again after reset -> CV = 1
    assert block.output("CV").value == 1


def test_function_blocks_registered() -> None:
    names = default_registry.names()
    for name in (
        "AND",
        "OR",
        "NOT",
        "XOR",
        "SR",
        "TON",
        "CTU",
        "EQ",
        "NE",
        "LT",
        "GT",
        "THRESHOLD",
        "PASS",
        "MUX",
    ):
        assert name in names


def test_function_block_pin_types() -> None:
    ton = TONBlock("t1")
    assert ton.input("PT").dtype is PinDType.FLOAT
    assert ton.input("DT").dtype is PinDType.FLOAT
    assert ton.output("ET").dtype is PinDType.FLOAT
    ctu = CTUBlock("c1")
    assert ctu.input("PV").dtype is PinDType.INT
    assert ctu.output("CV").dtype is PinDType.INT
    eq = EQBlock("e1")
    assert eq.input("IN1").dtype is PinDType.FLOAT
    assert eq.output("Q").dtype is PinDType.BOOL
    th = ThresholdBlock("th1")
    assert th.input("IN").dtype is PinDType.FLOAT
    assert th.input("LIMIT").dtype is PinDType.FLOAT
    assert th.output("Q").dtype is PinDType.BOOL
    mux = MUXBlock("m1")
    assert mux.input("SEL").dtype is PinDType.BOOL
    assert mux.input("IN1").dtype is PinDType.FLOAT
    assert mux.output("Q").dtype is PinDType.FLOAT


def test_fresh_registry_can_register_function_block() -> None:
    registry = BlockRegistry()
    registry.register("TON", TONBlock)
    block = registry.create("TON", "t1")
    assert isinstance(block, TONBlock)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (5.0, 5.0, True),
        (5.0, 3.0, False),
        (3.0, 5.0, False),
        (-1.0, -1.0, True),
        (0, 0, True),
        (0, 1, False),
        (2, 2.0, True),
    ],
)
def test_eq_truth_table(a: float, b: float, expected: bool) -> None:
    block = EQBlock("e1")
    _set(block, "IN1", a)
    _set(block, "IN2", b)
    block.evaluate()
    assert _q(block) is expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (5.0, 5.0, False),
        (5.0, 3.0, True),
        (3.0, 5.0, True),
    ],
)
def test_ne_truth_table(a: float, b: float, expected: bool) -> None:
    block = NEBlock("n1")
    _set(block, "IN1", a)
    _set(block, "IN2", b)
    block.evaluate()
    assert _q(block) is expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (3.0, 5.0, True),
        (5.0, 3.0, False),
        (5.0, 5.0, False),
    ],
)
def test_lt_truth_table(a: float, b: float, expected: bool) -> None:
    block = LTBlock("l1")
    _set(block, "IN1", a)
    _set(block, "IN2", b)
    block.evaluate()
    assert _q(block) is expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (5.0, 3.0, True),
        (3.0, 5.0, False),
        (5.0, 5.0, False),
    ],
)
def test_gt_truth_table(a: float, b: float, expected: bool) -> None:
    block = GTBlock("g1")
    _set(block, "IN1", a)
    _set(block, "IN2", b)
    block.evaluate()
    assert _q(block) is expected


def test_compare_unwired_input_is_false() -> None:
    blocks = [EQBlock("e1"), NEBlock("n1"), LTBlock("l1"), GTBlock("g1")]
    for block in blocks:
        block.evaluate()
        assert _q(block) is False
    block = EQBlock("e2")
    _set(block, "IN1", 5.0)
    # IN2 unwired: Q stays false
    block.evaluate()
    assert _q(block) is False


@pytest.mark.parametrize(
    ("value", "limit", "expected"),
    [
        (5.0, 5.0, True),   # at threshold: on
        (6.0, 5.0, True),   # above threshold: on
        (4.0, 5.0, False),  # below threshold: off
        (-1.0, 0.0, False),
        (0.0, 0.0, True),
    ],
)
def test_threshold(value: float, limit: float, expected: bool) -> None:
    block = ThresholdBlock("th1")
    _set(block, "IN", value)
    _set(block, "LIMIT", limit)
    block.evaluate()
    assert _q(block) is expected


def test_threshold_unwired_is_false() -> None:
    block = ThresholdBlock("th1")
    block.evaluate()
    assert _q(block) is False
    _set(block, "IN", 5.0)
    # LIMIT unwired: Q stays false
    block.evaluate()
    assert _q(block) is False


def test_passthrough_passes_value_through() -> None:
    block = PassThroughBlock("p1")
    _set(block, "IN", 3.5)
    block.evaluate()
    assert block.output("Q").value == 3.5


def test_passthrough_passes_none_through() -> None:
    block = PassThroughBlock("p1")
    block.evaluate()
    assert block.output("Q").value is None


@pytest.mark.parametrize(
    ("sel", "in1", "in2", "expected"),
    [
        (False, 1.0, 2.0, 1.0),
        (True, 1.0, 2.0, 2.0),
    ],
)
def test_mux_selects_input(sel: bool, in1: float, in2: float, expected: float) -> None:
    block = MUXBlock("m1")
    _set(block, "SEL", sel)
    _set(block, "IN1", in1)
    _set(block, "IN2", in2)
    block.evaluate()
    assert block.output("Q").value == expected


def test_mux_unwired_sel_defaults_to_in1() -> None:
    block = MUXBlock("m1")
    _set(block, "IN1", 1.0)
    _set(block, "IN2", 2.0)
    block.evaluate()
    assert block.output("Q").value == 1.0
