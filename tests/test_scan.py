"""Cyclic scan executor: read inputs → evaluate in topological order → write outputs,
at a fixed cycle time, deterministic."""

from __future__ import annotations

import threading

import pytest

from input_automation.engine import (
    Block,
    Pin,
    PinDirection,
    PinDType,
    ScanExecutor,
    WireCycleError,
    WireGraph,
)


class _Source(Block):
    """No inputs; writes its :attr:`value` to output Q on every evaluate."""

    type_name = "SOURCE"
    input_pins = ()
    output_pins = (Pin("Q", PinDirection.OUTPUT, PinDType.BOOL),)

    def __init__(self, block_id: str) -> None:
        super().__init__(block_id)
        self.value = False

    def evaluate(self) -> None:
        self.output("Q").set(self.value)


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


class _Pass(Block):
    type_name = "PASS"
    input_pins = (Pin("IN", PinDirection.INPUT, PinDType.INT),)
    output_pins = (Pin("OUT", PinDirection.OUTPUT, PinDType.INT),)

    def evaluate(self) -> None:
        self.output("OUT").set(self.input("IN").value)


class _Tracer(Block):
    """Appends its id to ``order`` on every evaluate; no pins."""

    type_name = "TRACE"
    input_pins = ()
    output_pins = ()

    def __init__(self, block_id: str, order: list[str]) -> None:
        super().__init__(block_id)
        self._order = order

    def evaluate(self) -> None:
        self._order.append(self.id)


def _chain_graph() -> WireGraph:
    """All-BOOL chain src -> and1 -> and2 so data can flow across wires."""
    graph = WireGraph()
    graph.add_block(_Source("src"))
    graph.add_block(_And("and1"))
    graph.add_block(_And("and2"))
    graph.connect("src", "Q", "and1", "IN1")
    graph.connect("and1", "Q", "and2", "IN1")
    return graph


def _block(graph: WireGraph, block_id: str) -> Block:
    return graph.block(block_id)


def test_run_once_executes_predecessors_first() -> None:
    order: list[str] = []
    graph = WireGraph()
    for bid in ("z", "a", "m"):
        graph.add_block(_Tracer(bid, order))
    executor = ScanExecutor(graph)
    executor.run_once()
    # disconnected blocks: lexicographic order
    assert order == ["a", "m", "z"]

    # with dependencies src -> and1 -> and2: predecessors first
    assert _chain_graph().topological_order() == ["src", "and1", "and2"]


def test_data_flows_along_wires() -> None:
    graph = _chain_graph()
    src = _block(graph, "src")
    and1 = _block(graph, "and1")
    and2 = _block(graph, "and2")
    src.value = True  # type: ignore[attr-defined]
    and1.input("IN2").set(True)
    and2.input("IN2").set(True)

    ScanExecutor(graph).run_once()

    assert and1.output("Q").value is True
    assert and2.output("Q").value is True


def test_read_inputs_runs_before_evaluation() -> None:
    graph = _chain_graph()
    phases: list[str] = []

    def read_inputs(g: WireGraph) -> None:
        phases.append("read")
        _block(g, "src").value = True  # type: ignore[attr-defined]
        _block(g, "and1").input("IN2").set(True)
        _block(g, "and2").input("IN2").set(True)

    def write_outputs(g: WireGraph) -> None:
        phases.append("write")

    executor = ScanExecutor(
        graph, read_inputs=read_inputs, write_outputs=write_outputs
    )
    executor.run_once()
    assert phases == ["read", "write"]
    assert _block(graph, "and1").output("Q").value is True


def test_write_outputs_runs_after_evaluation() -> None:
    graph = _chain_graph()
    phases: list[str] = []

    def read_inputs(g: WireGraph) -> None:
        phases.append("read")
        _block(g, "src").value = True  # type: ignore[attr-defined]
        _block(g, "and1").input("IN2").set(True)
        _block(g, "and2").input("IN2").set(True)

    def write_outputs(g: WireGraph) -> None:
        phases.append("write")
        assert _block(g, "and1").output("Q").value is True
        assert _block(g, "and2").output("Q").value is True

    ScanExecutor(
        graph, read_inputs=read_inputs, write_outputs=write_outputs
    ).run_once()
    assert phases == ["read", "write"]


def test_run_once_raises_on_cycle() -> None:
    graph = WireGraph()
    graph.add_block(_And("a1"))
    graph.add_block(_And("a2"))
    graph.connect("a1", "Q", "a2", "IN1")
    graph.connect("a2", "Q", "a1", "IN1")
    executor = ScanExecutor(graph)
    with pytest.raises(WireCycleError):
        executor.run_once()


def test_cycle_time_must_be_positive() -> None:
    graph = WireGraph()
    graph.add_block(_Source("s"))
    with pytest.raises(ValueError):
        ScanExecutor(graph, cycle_time_s=0)
    with pytest.raises(ValueError):
        ScanExecutor(graph, cycle_time_s=-0.01)


def test_run_forever_stops_on_event() -> None:
    graph = _chain_graph()
    _block(graph, "src").value = True  # type: ignore[attr-defined]
    stop = threading.Event()
    cycles = [0]

    def count_read(g: WireGraph) -> None:
        cycles[0] += 1

    executor = ScanExecutor(graph, cycle_time_s=0.005, read_inputs=count_read)
    thread = threading.Thread(target=executor.run_forever, args=(stop,))
    thread.start()
    stop.wait(0.05)
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert cycles[0] >= 5


def test_inputs_keep_last_value_between_cycles() -> None:
    graph = WireGraph()
    graph.add_block(_Pass("p1"))
    executor = ScanExecutor(graph, cycle_time_s=0.001)
    _block(graph, "p1").input("IN").set(7)  # type: ignore[attr-defined]
    executor.run_once()
    assert _block(graph, "p1").output("OUT").value == 7
    executor.run_once()
    assert _block(graph, "p1").output("OUT").value == 7
