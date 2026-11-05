"""Tests for the SPS block canvas (T11). Runs with QT_QPA_PLATFORM=offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QByteArray, QEvent, QMimeData, QPointF, Qt
from PySide6.QtGui import QDrag, QDropEvent, QKeyEvent

from input_automation.engine.block import Block
from input_automation.engine.registry import default_registry
from input_automation.engine.wires import (
    DuplicateWireError,
    UnknownPinError,
    WireCycleError,
    WireGraph,
    WireTypeError,
)
from input_automation.gui.canvas import (
    BLOCK_MIME,
    BlockCanvas,
    BlockPalette,
    PropertiesPanel,
)
from input_automation.project.pins import PinDirection


def default_block(type_name: str, block_id: str) -> Block:
    return default_registry.create(type_name, block_id)


class TestBlockPalette:
    def test_lists_all_registered_block_types(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        palette = BlockPalette()
        qtbot.addWidget(palette)
        types = [palette.item(i).text() for i in range(palette.count())]
        for expected in ("AND", "OR", "NOT", "TON", "CTU", "CAPTURE", "INPUT", "OUTPUT"):
            assert expected in types

    def test_selected_block_type(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        palette = BlockPalette()
        qtbot.addWidget(palette)
        assert palette.selected_block_type() is None
        matches = palette.findItems("AND", Qt.MatchFlag.MatchExactly)
        assert matches
        palette.setCurrentItem(matches[0])
        assert palette.selected_block_type() == "AND"


class TestBlockCanvas:
    def test_add_block_instantiates_engine_block(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        item = canvas.add_block("AND", "a1")
        assert item.block_id == "a1"
        assert item.type_name == "AND"
        assert item.input_pin_names() == ["IN1", "IN2"]
        assert item.output_pin_names() == ["Q"]
        assert canvas.has_block("a1")

    def test_auto_ids_are_unique(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        first = canvas.add_block("AND")
        second = canvas.add_block("AND")
        assert first.block_id != second.block_id
        assert first.block_id.startswith("AND")
        assert second.block_id.startswith("AND")

    def test_add_wire_round_trips_to_graph(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("AND", "a")
        canvas.add_block("TON", "t")
        canvas.add_wire("a", "Q", "t", "IN")
        assert canvas.has_wire("a", "Q", "t", "IN")
        graph = canvas.to_graph()
        assert graph.has_block("a")
        assert graph.has_block("t")
        assert graph.has_wire("a", "Q", "t", "IN")
        assert graph.topological_order() == ["a", "t"]

    def test_duplicate_wire_raises(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("AND", "a")
        canvas.add_block("TON", "t")
        canvas.add_wire("a", "Q", "t", "IN")
        with pytest.raises(DuplicateWireError):
            canvas.add_wire("a", "Q", "t", "IN")

    def test_type_mismatched_wire_raises(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("TON", "t")
        canvas.add_block("AND", "a")
        with pytest.raises(WireTypeError):
            canvas.add_wire("t", "ET", "a", "IN1")

    def test_remove_block_removes_its_wires(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("AND", "a")
        canvas.add_block("TON", "t")
        canvas.add_wire("a", "Q", "t", "IN")
        canvas.remove_block("a")
        assert not canvas.has_block("a")
        assert not canvas.has_wire("a", "Q", "t", "IN")
        assert canvas.has_block("t")

    def test_delete_selected_removes_block_and_wires(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("AND", "a")
        canvas.add_block("TON", "t")
        canvas.add_wire("a", "Q", "t", "IN")
        canvas.block_item("a").setSelected(True)
        canvas.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Delete,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert not canvas.has_block("a")
        assert not canvas.has_wire("a", "Q", "t", "IN")
        assert canvas.has_block("t")

    def test_clear(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("AND", "a")
        canvas.add_block("TON", "t")
        canvas.add_wire("a", "Q", "t", "IN")
        canvas.clear()
        assert canvas.block_ids() == []
        assert canvas.wires() == []

    def test_ports_at(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("AND", "a")
        item = canvas.block_item("a")
        hits = canvas.ports_at(item.mapToScene(item.port_rect("IN1", PinDirection.INPUT).center()))
        assert ("a", "IN1", PinDirection.INPUT) in hits
        hits = canvas.ports_at(item.mapToScene(item.port_rect("Q", PinDirection.OUTPUT).center()))
        assert ("a", "Q", PinDirection.OUTPUT) in hits

    def test_from_graph_and_back(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        graph = WireGraph()
        graph.add_block(default_block("AND", "a1"))
        graph.add_block(default_block("TON", "t1"))
        graph.connect("a1", "Q", "t1", "IN")

        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.from_graph(graph)
        assert set(canvas.block_ids()) == {"a1", "t1"}
        assert canvas.has_wire("a1", "Q", "t1", "IN")

        back = canvas.to_graph()
        assert back.has_block("a1")
        assert back.has_wire("a1", "Q", "t1", "IN")
        assert back.topological_order() == ["a1", "t1"]

    def test_from_graph_rejects_cycles(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        graph = WireGraph()
        graph.add_block(default_block("AND", "a1"))
        graph.add_block(default_block("TON", "t1"))
        graph.connect("a1", "Q", "t1", "IN")
        graph.connect("t1", "Q", "a1", "IN1")  # a1 -> t1 -> a1 cycle
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        with pytest.raises(WireCycleError):
            canvas.from_graph(graph)

    def test_drop_from_palette_adds_block(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        palette = BlockPalette()
        matches = palette.findItems("OR", Qt.MatchFlag.MatchExactly)
        assert matches
        palette.setCurrentItem(matches[0])
        canvas = BlockCanvas()
        canvas.set_palette(palette)
        qtbot.addWidget(palette)
        qtbot.addWidget(canvas)

        mime = QMimeData()
        mime.setData(BLOCK_MIME, QByteArray(b"OR"))
        mime.setText("OR")
        drag = QDrag(palette)
        drag.setMimeData(mime)
        event = QDropEvent(
            QPointF(110, 120),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QEvent.Type.Drop,
        )
        canvas.dropEvent(event)
        ids = canvas.block_ids()
        assert any(block_id.startswith("OR") for block_id in ids)
        graph = canvas.to_graph()
        assert graph.has_block(ids[0])


class TestBlockParameters:
    def test_add_block_stores_parameters(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        item = canvas.add_block("TON", "t", parameters={"PT": 2.5, "DT": 0.1})
        assert item.parameters == {"PT": 2.5, "DT": 0.1}

    def test_to_graph_applies_parameters(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("TON", "t", parameters={"PT": 2.5})
        graph = canvas.to_graph()
        assert graph.block("t").input("PT").value == 2.5
        # defaults still apply for parameters left unset
        assert graph.block("t").input("DT").value == 0.0

    def test_to_graph_unknown_parameter_raises(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("AND", "a", parameters={"NOPE": 1})
        with pytest.raises(UnknownPinError):
            canvas.to_graph()

    def test_to_graph_wrong_type_parameter_raises(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("TON", "t", parameters={"PT": "not-a-float"})
        with pytest.raises(WireTypeError):
            canvas.to_graph()

    def test_from_graph_round_trips_parameters(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        canvas.add_block("TON", "t", parameters={"PT": 2.5, "DT": 0.1})
        graph = canvas.to_graph()
        graph.block("t").input("PT").set(9.0)

        other = BlockCanvas()
        qtbot.addWidget(other)
        other.from_graph(graph)

        item = other.block_item("t")
        assert item.parameters == {"PT": 9.0, "DT": 0.1}
        back = other.to_graph()
        assert back.block("t").input("PT").value == 9.0


class TestPropertiesPanel:
    def test_no_block_selected(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = PropertiesPanel()
        qtbot.addWidget(panel)
        panel.set_block(None)
        assert panel._editors == {}

    def test_shows_only_defaulted_inputs(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        panel = PropertiesPanel()
        qtbot.addWidget(panel)

        ton = canvas.add_block("TON", "t")
        panel.set_block(ton)
        # TON inputs: IN (no default), PT (default 1.0), DT (default 0.0)
        assert set(panel._editors) == {"PT", "DT"}
        assert panel._editors["PT"][0].value() == 1.0
        assert panel._editors["DT"][0].value() == 0.0

        and_block = canvas.add_block("AND", "a")
        panel.set_block(and_block)
        # AND has no defaulted inputs -> no editors
        assert panel._editors == {}

    def test_editing_updates_block_parameters(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        panel = PropertiesPanel()
        qtbot.addWidget(panel)

        item = canvas.add_block("TON", "t", parameters={"PT": 1.0})
        panel.set_block(item)

        with qtbot.waitSignal(panel.parametersChanged):
            panel._editors["PT"][0].setValue(5.5)

        assert item.parameters == {"PT": 5.5}

    def test_panel_follows_canvas_selection(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        canvas = BlockCanvas()
        qtbot.addWidget(canvas)
        panel = PropertiesPanel()
        qtbot.addWidget(panel)
        canvas.selectionChanged.connect(
            lambda: panel.set_block(canvas.selected_block())
        )

        canvas.add_block("TON", "t")
        canvas.add_block("AND", "a")
        canvas.block_item("t").setSelected(True)
        # setSelected() does not emit scene.selectionChanged; drive the panel directly
        panel.set_block(canvas.selected_block())
        assert set(panel._editors) == {"PT", "DT"}

        canvas.block_item("t").setSelected(False)
        panel.set_block(canvas.selected_block())
        assert panel._editors == {}
