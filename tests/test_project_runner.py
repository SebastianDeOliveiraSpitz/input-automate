"""Headless project execution through the scan engine (T6)."""

import pytest

from input_automation.engine import Block, Pin, PinDirection, PinDType
from input_automation.engine.pins import PinDirection as EnginePinDirection
from input_automation.project import (
    Block as SchemaBlock,
)
from input_automation.project import (
    Pin as SchemaPin,
)
from input_automation.project import (
    PinDirection as SchemaPinDirection,
)
from input_automation.project import (
    PinDType as SchemaPinDType,
)
from input_automation.project import (
    Project,
    Wire,
)
from input_automation.project.runner import (
    ProjectRunError,
    build_graph,
    run_project,
)

# engine and project share the same pin enums (enforced by test_engine.py)
assert PinDirection is SchemaPinDirection is EnginePinDirection
assert PinDType is SchemaPinDType

INPUT = PinDirection.INPUT
OUTPUT = PinDirection.OUTPUT
BOOL = PinDType.BOOL
INT = PinDType.INT
FLOAT = PinDType.FLOAT
STRING = PinDType.STRING


def _schema_pin(name, direction, dtype, default=None):
    return SchemaPin(name=name, direction=direction, dtype=dtype, default=default)


def _schema_block(block_id, block_type, pins=None):
    return SchemaBlock(id=block_id, type=block_type, pins=pins or [])


def _and_pins():
    return [
        _schema_pin("IN1", INPUT, BOOL),
        _schema_pin("IN2", INPUT, BOOL),
        _schema_pin("Q", OUTPUT, BOOL),
    ]


def test_combinational_chain_flows_values() -> None:
    project = Project(
        name="chain",
        blocks=[
            _schema_block(
                "a1", "AND",
                [_schema_pin("IN1", INPUT, BOOL, default=True),
                 _schema_pin("IN2", INPUT, BOOL, default=False),
                 _schema_pin("Q", OUTPUT, BOOL)],
            ),
            _schema_block(
                "a2", "AND",
                [_schema_pin("IN1", INPUT, BOOL),
                 _schema_pin("IN2", INPUT, BOOL, default=True),
                 _schema_pin("Q", OUTPUT, BOOL)],
            ),
        ],
        wires=[Wire(src_block="a1", src_pin="Q", dst_block="a2", dst_pin="IN1")],
    )
    assert run_project(project, cycles=1) == {"a1": {"Q": False}, "a2": {"Q": False}}


def test_combinational_chain_passes_true() -> None:
    project = Project(
        name="chain",
        blocks=[
            _schema_block(
                "a1", "AND",
                [_schema_pin("IN1", INPUT, BOOL, default=True),
                 _schema_pin("IN2", INPUT, BOOL, default=True),
                 _schema_pin("Q", OUTPUT, BOOL)],
            ),
            _schema_block(
                "a2", "AND",
                [_schema_pin("IN1", INPUT, BOOL),
                 _schema_pin("IN2", INPUT, BOOL, default=True),
                 _schema_pin("Q", OUTPUT, BOOL)],
            ),
        ],
        wires=[Wire(src_block="a1", src_pin="Q", dst_block="a2", dst_pin="IN1")],
    )
    assert run_project(project, cycles=1) == {"a1": {"Q": True}, "a2": {"Q": True}}


def test_ton_advances_over_cycles() -> None:
    project = Project(
        name="timer",
        blocks=[
            _schema_block(
                "t1", "TON",
                [_schema_pin("IN", INPUT, BOOL, default=True),
                 _schema_pin("PT", INPUT, FLOAT, default=2.5),
                 _schema_pin("DT", INPUT, FLOAT, default=0.25),
                 _schema_pin("Q", OUTPUT, BOOL),
                 _schema_pin("ET", OUTPUT, FLOAT)],
            ),
        ],
        wires=[],
    )
    assert run_project(project, cycles=1) == {"t1": {"Q": False, "ET": 0.25}}
    assert run_project(project, cycles=10) == {"t1": {"Q": True, "ET": 2.5}}


def test_unwired_output_reads_none() -> None:
    project = Project(
        name="pass",
        blocks=[_schema_block(
            "p1", "PASS",
            [_schema_pin("IN", INPUT, FLOAT), _schema_pin("Q", OUTPUT, FLOAT)],
        )],
        wires=[],
    )
    assert run_project(project) == {"p1": {"Q": None}}


def test_unknown_block_type_raises() -> None:
    project = Project(name="bad", blocks=[_schema_block("x", "NOSUCHTYPE", [])])
    with pytest.raises(ProjectRunError, match="unknown block type"):
        build_graph(project)


def test_cyclic_wires_raise() -> None:
    project = Project(
        name="cyc",
        blocks=[_schema_block("a1", "AND", _and_pins()),
                _schema_block("a2", "AND", _and_pins())],
        wires=[
            Wire(src_block="a1", src_pin="Q", dst_block="a2", dst_pin="IN1"),
            Wire(src_block="a2", src_pin="Q", dst_block="a1", dst_pin="IN1"),
        ],
    )
    build_graph(project)  # wiring itself is valid
    with pytest.raises(ProjectRunError, match="cycle"):
        run_project(project)


def test_cycles_must_be_positive() -> None:
    project = Project(name="empty", blocks=[])
    with pytest.raises(ProjectRunError, match=">= 1"):
        run_project(project, cycles=0)


def test_schema_dtype_mismatch_with_engine_block_raises() -> None:
    # schema declares AND's Q as INT with a default; the engine AND block's Q
    # is BOOL, so applying the default fails
    project = Project(
        name="mismatch",
        blocks=[_schema_block(
            "a1", "AND",
            [_schema_pin("IN1", INPUT, BOOL),
             _schema_pin("IN2", INPUT, BOOL),
             _schema_pin("Q", OUTPUT, INT, default=5)],
        )],
        wires=[],
    )
    with pytest.raises(ProjectRunError, match=r"block 'a1': pin 'Q' default"):
        build_graph(project)


def test_build_graph_returns_ordered_graph() -> None:
    project = Project(
        name="order",
        blocks=[_schema_block("a1", "AND", _and_pins()),
                _schema_block("a2", "AND", _and_pins())],
        wires=[Wire(src_block="a1", src_pin="Q", dst_block="a2", dst_pin="IN1")],
    )
    graph = build_graph(project)
    assert graph.topological_order() == ["a1", "a2"]


def test_run_uses_custom_registry() -> None:
    from input_automation.engine import BlockRegistry

    class _Constant(Block):
        type_name = "CONST"
        input_pins = ()
        output_pins = (Pin("Q", OUTPUT, BOOL, default=True),)

        def evaluate(self) -> None:
            pass

    registry = BlockRegistry()
    registry.register("CONST", _Constant)
    project = Project(
        name="custom",
        blocks=[_schema_block("c1", "CONST", [_schema_pin("Q", OUTPUT, BOOL)])],
        wires=[],
    )
    assert run_project(project, registry=registry) == {"c1": {"Q": True}}


def test_run_with_mock_io_providers() -> None:
    """INPUT -> AND -> OUTPUT end to end with mock providers: no /dev/input,
    no /dev/uinput, no privileges, deterministic.
    """
    from evdev import ecodes

    from input_automation.io.mock import (
        MockEvdevInputProvider,
        MockUinputOutputProvider,
    )

    project = Project(
        name="e2e",
        blocks=[
            _schema_block(
                "in1",
                "INPUT",
                [
                    _schema_pin("DEVICE", INPUT, STRING, default="/dev/input/event0"),
                    _schema_pin("CODE", INPUT, STRING, default="KEY_Q"),
                    _schema_pin("Q", OUTPUT, BOOL),
                    _schema_pin("VALUE", OUTPUT, INT, default=0),
                ],
            ),
            _schema_block(
                "a1",
                "AND",
                [
                    _schema_pin("IN1", INPUT, BOOL),
                    _schema_pin("IN2", INPUT, BOOL, default=True),
                    _schema_pin("Q", OUTPUT, BOOL),
                ],
            ),
            _schema_block(
                "out1",
                "OUTPUT",
                [
                    _schema_pin("DEVICE", INPUT, STRING, default="out-kb"),
                    _schema_pin("CODE", INPUT, STRING, default="KEY_A"),
                    _schema_pin("Q", INPUT, BOOL, default=False),
                    _schema_pin("VALUE", INPUT, INT, default=1),
                ],
            ),
        ],
        wires=[
            Wire(src_block="in1", src_pin="Q", dst_block="a1", dst_pin="IN1"),
            Wire(src_block="a1", src_pin="Q", dst_block="out1", dst_pin="Q"),
        ],
    )
    evdev = MockEvdevInputProvider()
    outputs = MockUinputOutputProvider()

    evdev.press("/dev/input/event0", ecodes.KEY_Q)
    results = run_project(project, cycles=1, read_inputs=evdev, write_outputs=outputs)
    assert results["in1"] == {"Q": True, "VALUE": 1}
    assert results["a1"] == {"Q": True}
    assert results["out1"] == {}
    assert outputs.device_events("out-kb") == [
        ((ecodes.EV_KEY, ecodes.KEY_A), 1, True)
    ]

    evdev.release("/dev/input/event0", ecodes.KEY_Q)
    results = run_project(project, cycles=1, read_inputs=evdev, write_outputs=outputs)
    assert results["in1"] == {"Q": False, "VALUE": 0}
    assert results["a1"] == {"Q": False}
    assert ((ecodes.EV_KEY, ecodes.KEY_A), 0, True) in outputs.device_events("out-kb")
