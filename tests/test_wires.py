"""Wire graph: add/remove connections, cycle detection, topological sort."""

import pytest

from input_automation.engine import (
    Block,
    DuplicateBlockError,
    DuplicateWireError,
    Pin,
    PinDirection,
    PinDType,
    UnknownBlockError,
    UnknownPinError,
    UnknownWireError,
    Wire,
    WireCycleError,
    WireGraph,
    WireGraphError,
    WireTypeError,
)


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


def _wire_graph() -> WireGraph:
    graph = WireGraph()
    graph.add_block(_And("a1"))
    graph.add_block(_And("a2"))
    graph.add_block(_And("a3"))
    graph.add_block(_Pass("p1"))
    return graph


def test_wiregraph_errors_subclass_value_error() -> None:
    assert issubclass(WireGraphError, ValueError)
    for exc in (
        DuplicateBlockError,
        UnknownBlockError,
        UnknownPinError,
        DuplicateWireError,
        UnknownWireError,
        WireTypeError,
        WireCycleError,
    ):
        assert issubclass(exc, WireGraphError)


def test_block_crud() -> None:
    graph = WireGraph()
    assert graph.blocks() == []
    graph.add_block(_And("a1"))
    assert graph.has_block("a1")
    assert not graph.has_block("missing")
    assert graph.block("a1").type_name == "AND"
    assert graph.blocks() == ["a1"]
    with pytest.raises(DuplicateBlockError):
        graph.add_block(_And("a1"))
    graph.remove_block("a1")
    assert not graph.has_block("a1")
    assert graph.blocks() == []
    with pytest.raises(UnknownBlockError):
        graph.remove_block("a1")
    with pytest.raises(UnknownBlockError):
        graph.block("a1")


def test_remove_block_drops_incident_wires() -> None:
    graph = _wire_graph()
    graph.connect("a1", "Q", "a2", "IN1")
    graph.connect("a1", "Q", "a3", "IN1")
    graph.remove_block("a1")
    assert graph.wires() == []
    assert graph.successors("a2") == set()
    assert graph.predecessors("a3") == set()
    # a2, a3 and p1 remain, unwired
    assert graph.blocks() == ["a2", "a3", "p1"]


def test_connect_and_disconnect() -> None:
    graph = _wire_graph()
    graph.connect("a1", "Q", "a2", "IN1")
    assert graph.has_wire("a1", "Q", "a2", "IN1")
    assert graph.wires() == [Wire("a1", "Q", "a2", "IN1")]
    assert graph.successors("a1") == {"a2"}
    assert graph.predecessors("a2") == {"a1"}
    graph.disconnect("a1", "Q", "a2", "IN1")
    assert graph.wires() == []
    assert graph.successors("a1") == set()
    assert graph.predecessors("a2") == set()
    with pytest.raises(UnknownWireError):
        graph.disconnect("a1", "Q", "a2", "IN1")


def test_connect_validates_endpoints() -> None:
    graph = _wire_graph()
    with pytest.raises(UnknownBlockError):
        graph.connect("ghost", "Q", "a2", "IN1")
    with pytest.raises(UnknownBlockError):
        graph.connect("a1", "Q", "ghost", "IN1")
    # source pin must be an output
    with pytest.raises(UnknownPinError):
        graph.connect("a1", "IN1", "a2", "IN1")
    # destination pin must be an input
    with pytest.raises(UnknownPinError):
        graph.connect("a1", "Q", "a2", "Q")
    # unknown pin names
    with pytest.raises(UnknownPinError):
        graph.connect("a1", "NOPE", "a2", "IN1")
    with pytest.raises(UnknownPinError):
        graph.connect("a1", "Q", "a2", "NOPE")


def test_connect_rejects_type_mismatch_and_duplicates() -> None:
    graph = _wire_graph()
    graph.connect("a1", "Q", "a2", "IN1")  # BOOL -> BOOL
    with pytest.raises(DuplicateWireError):
        graph.connect("a1", "Q", "a2", "IN1")
    # INT output cannot feed BOOL input
    with pytest.raises(WireTypeError):
        graph.connect("p1", "OUT", "a2", "IN1")


def test_has_cycle() -> None:
    graph = _wire_graph()
    assert not graph.has_cycle()
    graph.connect("a1", "Q", "a2", "IN1")
    assert not graph.has_cycle()
    graph.connect("a2", "Q", "a1", "IN1")  # a1 -> a2 -> a1
    assert graph.has_cycle()
    graph.disconnect("a2", "Q", "a1", "IN1")
    assert not graph.has_cycle()


def test_self_loop_is_a_cycle() -> None:
    graph = WireGraph()
    graph.add_block(_And("a1"))
    graph.connect("a1", "Q", "a1", "IN1")
    assert graph.has_cycle()
    with pytest.raises(WireCycleError):
        graph.topological_order()


def test_topological_order_predecessors_first() -> None:
    graph = _wire_graph()
    graph.connect("a1", "Q", "a2", "IN1")
    graph.connect("a3", "Q", "a2", "IN1")
    order = graph.topological_order()
    assert order.index("a1") < order.index("a2")
    assert order.index("a3") < order.index("a2")
    # every block appears exactly once
    assert set(order) == {"a1", "a2", "a3", "p1"}


def test_topological_order_raises_on_cycle() -> None:
    graph = _wire_graph()
    graph.connect("a1", "Q", "a2", "IN1")
    graph.connect("a2", "Q", "a3", "IN1")
    graph.connect("a3", "Q", "a1", "IN1")  # a1 -> a2 -> a3 -> a1
    with pytest.raises(WireCycleError):
        graph.topological_order()
    assert graph.has_cycle()


def test_disconnected_blocks_still_ordered() -> None:
    graph = WireGraph()
    graph.add_block(_And("z"))
    graph.add_block(_And("a"))
    assert graph.topological_order() == ["a", "z"]
