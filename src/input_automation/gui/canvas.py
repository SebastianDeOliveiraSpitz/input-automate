"""SPS function-block diagram canvas (T11).

``BlockCanvas`` is a :class:`PySide6.QtWidgets.QGraphicsView` over a scene of
movable :class:`BlockItem`s and :class:`WireItem`s. A :class:`BlockPalette`
lists every block type registered in the engine's default registry and drags
them onto the canvas.

The canvas layer is Qt-only; the engine stays pure Python. :meth:`BlockCanvas.to_graph`
builds an engine :class:`~input_automation.engine.wires.WireGraph` from the
scene (validating blocks, pins and dtypes), and :meth:`BlockCanvas.from_graph`
paints an engine graph onto the scene, so a diagram round-trips through the
headless engine without opening any device or showing a window.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import (
    QByteArray,
    QLineF,
    QMimeData,
    QPointF,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStyle,
    QStyleOptionGraphicsItem,
    QWidget,
)

from input_automation.engine.block import Block
from input_automation.engine.pins import Pin
from input_automation.engine.registry import default_registry
from input_automation.engine.wires import (
    DuplicateWireError,
    UnknownBlockError,
    UnknownPinError,
    WireGraph,
    WireGraphError,
    WireTypeError,
)
from input_automation.project.pins import PinDirection

__all__ = [
    "BlockCanvas",
    "BlockItem",
    "BlockPalette",
    "CanvasWindow",
    "WireItem",
    "run",
]

BLOCK_MIME = "application/x-input-automation-block"

BLOCK_WIDTH = 160
TITLE_HEIGHT = 26
PIN_ROW = 22
PORT = 10
PAD = 6


class BlockItem(QGraphicsRectItem):
    """One block: title bar, body, input pins on the left, output pins on the right."""

    def __init__(
        self,
        block_id: str,
        type_name: str,
        input_pins: tuple[Pin, ...] = (),
        output_pins: tuple[Pin, ...] = (),
    ) -> None:
        super().__init__(0, 0, BLOCK_WIDTH, TITLE_HEIGHT)
        self.block_id = block_id
        self.type_name = type_name
        self.input_pins = list(input_pins)
        self.output_pins = list(output_pins)
        self._canvas: BlockCanvas | None = None
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self._relayout()

    # -- geometry ---------------------------------------------------------

    def _relayout(self) -> None:
        count = max(len(self.input_pins), len(self.output_pins), 1)
        height = TITLE_HEIGHT + PAD + count * PIN_ROW + PAD
        self.setRect(0, 0, BLOCK_WIDTH, max(height, 70))

    def input_pin_names(self) -> list[str]:
        return [pin.name for pin in self.input_pins]

    def output_pin_names(self) -> list[str]:
        return [pin.name for pin in self.output_pins]

    def input_pin(self, name: str) -> Pin:
        for pin in self.input_pins:
            if pin.name == name:
                return pin
        raise KeyError(f"block {self.block_id!r}: no input pin {name!r}")

    def output_pin(self, name: str) -> Pin:
        for pin in self.output_pins:
            if pin.name == name:
                return pin
        raise KeyError(f"block {self.block_id!r}: no output pin {name!r}")

    def _pin_center_y(self, index: int, count: int) -> float:
        return TITLE_HEIGHT + PAD + index * PIN_ROW + PIN_ROW / 2

    def port_rect(self, pin_name: str, direction: PinDirection) -> QRectF:
        """Port rect in item coordinates."""
        pins = self.input_pins if direction is PinDirection.INPUT else self.output_pins
        index = next((i for i, pin in enumerate(pins) if pin.name == pin_name), None)
        if index is None:
            raise KeyError(f"block {self.block_id!r}: no {direction.value} pin {pin_name!r}")
        y = self._pin_center_y(index, len(pins)) - PORT / 2
        x = 0.0 if direction is PinDirection.INPUT else BLOCK_WIDTH - PORT
        return QRectF(x, y, PORT, PORT)

    def port_scene_pos(self, pin_name: str, direction: PinDirection) -> QPointF:
        center = self.port_rect(pin_name, direction).center()
        return self.scenePos() + center

    # -- painting ---------------------------------------------------------

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del widget  # unused
        rect = self.rect()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        title = QRectF(0, 0, rect.width(), TITLE_HEIGHT)
        painter.fillRect(title, QColor("#4a4f5a") if selected else QColor("#343940"))
        font = QFont()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(
            title.adjusted(8, 0, -8, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{self.type_name}  ·  {self.block_id}",
        )

        body = QRectF(0, TITLE_HEIGHT, rect.width(), rect.height() - TITLE_HEIGHT)
        painter.fillRect(body, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#c4c8cc"), 1.0))
        painter.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.drawLine(0, TITLE_HEIGHT, int(rect.width()), TITLE_HEIGHT)

        painter.setFont(QFont())
        painter.setPen(QPen(QColor("#111827")))
        for index, pin in enumerate(self.input_pins):
            y = self._pin_center_y(index, len(self.input_pins))
            painter.fillRect(
                QRectF(1.5, y - PORT / 2, PORT, PORT), QColor("#007acc")
            )
            painter.drawText(
                QRectF(14, y - 10, rect.width() - 18, 20),
                Qt.AlignmentFlag.AlignVCenter,
                pin.name,
            )
        for index, pin in enumerate(self.output_pins):
            y = self._pin_center_y(index, len(self.output_pins))
            painter.fillRect(
                QRectF(BLOCK_WIDTH - PORT - 1.5, y - PORT / 2, PORT, PORT),
                QColor("#6b7280"),
            )
            painter.drawText(
                QRectF(2, y - 10, BLOCK_WIDTH - PORT - 6, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                pin.name,
            )

    def itemChange(
        self, change: QGraphicsItem.GraphicsItemChange, value: Any
    ) -> Any:
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and self._canvas is not None
        ):
            self._canvas._block_moved(self)
        return super().itemChange(change, value)


class WireItem(QGraphicsLineItem):
    """One directed wire: src block/pin -> dst block/pin."""

    def __init__(
        self,
        src_block: str,
        src_pin: str,
        dst_block: str,
        dst_pin: str,
    ) -> None:
        super().__init__()
        self.src_block = src_block
        self.src_pin = src_pin
        self.dst_block = dst_block
        self.dst_pin = dst_pin
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._refresh_pen()

    def _refresh_pen(self) -> None:
        selected = self.isSelected()
        color = QColor("#1f77b4") if selected else QColor("#6b7280")
        width = 2.6 if selected else 1.6
        self.setPen(QPen(color, width))


class BlockCanvas(QGraphicsView):
    """The editing canvas: movable blocks, click-to-wire pins, drag-in from the palette."""

    graphChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(QRectF(-2000, -2000, 4000, 4000))
        self.setScene(self._scene)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumSize(640, 480)

        self._palette: BlockPalette | None = None
        self._items: dict[str, BlockItem] = {}
        self._wires: dict[tuple[str, str, str, str], WireItem] = {}
        self._wiring: tuple[str, str] | None = None
        self._rubber: QGraphicsLineItem | None = None
        self._auto_pos = QPointF(40, 40)

    # -- palette ----------------------------------------------------------

    def set_palette(self, palette: BlockPalette) -> None:
        self._palette = palette

    # -- blocks -----------------------------------------------------------

    def block_ids(self) -> list[str]:
        return list(self._items)

    def block_item(self, block_id: str) -> BlockItem:
        try:
            return self._items[block_id]
        except KeyError:
            raise UnknownBlockError(f"unknown block {block_id!r}") from None

    def block_items(self) -> list[BlockItem]:
        return list(self._items.values())

    def has_block(self, block_id: str) -> bool:
        return block_id in self._items

    def add_block(
        self,
        type_name: str,
        block_id: str | None = None,
        pos: QPointF | None = None,
    ) -> BlockItem:
        """Create a block item of registered ``type_name`` (unique id if omitted)."""
        if block_id is None:
            n = 1
            while f"{type_name}{n}" in self._items:
                n += 1
            block_id = f"{type_name}{n}"
        if block_id in self._items:
            raise UnknownBlockError(f"block {block_id!r} already on canvas")
        block: Block = default_registry.create(type_name, block_id)
        item = BlockItem(
            block_id,
            type_name,
            tuple(block.inputs.values()),
            tuple(block.outputs.values()),
        )
        item._canvas = self
        if pos is None:
            pos = self._auto_pos
            self._auto_pos = QPointF(self._auto_pos.x() + 28, self._auto_pos.y() + 28)
        item.setPos(pos)
        self._scene.addItem(item)
        self._items[block_id] = item
        self.graphChanged.emit()
        return item

    def remove_block(self, block_id: str) -> None:
        item = self.block_item(block_id)
        for key in [k for k in self._wires if k[0] == block_id or k[2] == block_id]:
            self._remove_wire(key)
        self._scene.removeItem(item)
        del self._items[block_id]
        self.graphChanged.emit()

    def clear(self) -> None:
        self._cancel_wiring()
        self._scene.clear()
        self._items.clear()
        self._wires.clear()
        self._auto_pos = QPointF(40, 40)
        self.graphChanged.emit()

    # -- wires ------------------------------------------------------------

    def add_wire(
        self,
        src_block: str,
        src_pin: str,
        dst_block: str,
        dst_pin: str,
    ) -> WireItem:
        """Connect ``src_block.src_pin`` to ``dst_block.dst_pin``.

        Validate endpoints and dtype; raise engine errors on conflict.
        """
        key = (src_block, src_pin, dst_block, dst_pin)
        if key in self._wires:
            raise DuplicateWireError(
                f"wire {src_block!r}.{src_pin!r} -> {dst_block!r}.{dst_pin!r} already exists"
            )
        try:
            src_item = self.block_item(src_block)
            dst_item = self.block_item(dst_block)
            src_pin_obj = src_item.output_pin(src_pin)
            dst_pin_obj = dst_item.input_pin(dst_pin)
        except KeyError as exc:
            raise UnknownPinError(str(exc)) from None
        if src_pin_obj.dtype is not dst_pin_obj.dtype:
            raise WireTypeError(
                f"wire: pin type mismatch {src_block!r}.{src_pin!r} "
                f"({src_pin_obj.dtype.value}) -> {dst_block!r}.{dst_pin!r} "
                f"({dst_pin_obj.dtype.value})"
            )
        wire = WireItem(src_block, src_pin, dst_block, dst_pin)
        wire.setLine(
            QLineF(
                src_item.port_scene_pos(src_pin, PinDirection.OUTPUT),
                dst_item.port_scene_pos(dst_pin, PinDirection.INPUT),
            )
        )
        self._scene.addItem(wire)
        self._wires[key] = wire
        self.graphChanged.emit()
        return wire

    def _remove_wire(self, key: tuple[str, str, str, str]) -> None:
        wire = self._wires.pop(key, None)
        if wire is not None:
            self._scene.removeItem(wire)
            self.graphChanged.emit()

    def has_wire(self, src_block: str, src_pin: str, dst_block: str, dst_pin: str) -> bool:
        return (src_block, src_pin, dst_block, dst_pin) in self._wires

    def wires(self) -> list[tuple[str, str, str, str]]:
        return list(self._wires)

    # -- interaction ------------------------------------------------------

    def ports_at(self, scene_pos: QPointF) -> list[tuple[str, str, PinDirection]]:
        found: list[tuple[str, str, PinDirection]] = []
        for item in self._scene.items(scene_pos):
            if not isinstance(item, BlockItem):
                continue
            item_pos = item.mapFromScene(scene_pos)
            for direction in (PinDirection.INPUT, PinDirection.OUTPUT):
                pins = item.input_pins if direction is PinDirection.INPUT else item.output_pins
                for _index, pin in enumerate(pins):
                    if item.port_rect(pin.name, direction).contains(item_pos):
                        found.append((item.block_id, pin.name, direction))
        return found

    def _start_wiring(self, src_block: str, src_pin: str) -> None:
        self._wiring = (src_block, src_pin)
        item = self._items[src_block]
        start = item.port_scene_pos(src_pin, PinDirection.OUTPUT)
        rubber = QGraphicsLineItem(QLineF(start, start))
        rubber.setPen(QPen(QColor("#1f77b4"), 1.6, Qt.PenStyle.DashLine))
        rubber.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._scene.addItem(rubber)
        self._rubber = rubber

    def _cancel_wiring(self) -> None:
        self._wiring = None
        if self._rubber is not None:
            self._scene.removeItem(self._rubber)
            self._rubber = None

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasFormat(BLOCK_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: Any) -> None:
        mime = event.mimeData()
        if mime.hasFormat(BLOCK_MIME):
            data = mime.data(BLOCK_MIME)
            type_name = data.data().decode("utf-8") if not data.isEmpty() else ""
            if type_name:
                self.add_block(type_name, pos=self.mapToScene(event.position().toPoint()))
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            hits = self.ports_at(self.mapToScene(event.pos()))
            if hits:
                block_id, pin_name, direction = hits[0]
                if direction is PinDirection.OUTPUT:
                    if self._wiring == (block_id, pin_name):
                        self._cancel_wiring()
                    else:
                        self._start_wiring(block_id, pin_name)
                    return
                if direction is PinDirection.INPUT and self._wiring is not None:
                    src_block, src_pin = self._wiring
                    if src_block != block_id:
                        with contextlib.suppress(WireGraphError):
                            # duplicate or type mismatch: silently refuse
                            self.add_wire(src_block, src_pin, block_id, pin_name)
                    self._cancel_wiring()
                    return
            self._cancel_wiring()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._wiring is not None and self._rubber is not None:
            item = self._items[self._wiring[0]]
            start = item.port_scene_pos(self._wiring[1], PinDirection.OUTPUT)
            self._rubber.setLine(QLineF(start, self.mapToScene(event.pos())))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        if self._wiring is not None:
            hits = self.ports_at(self.mapToScene(event.pos()))
            if (
                hits
                and hits[0][2] is PinDirection.INPUT
                and hits[0][0] != self._wiring[0]
            ):
                src_block, src_pin = self._wiring
                with contextlib.suppress(WireGraphError):
                    self.add_wire(src_block, src_pin, hits[0][0], hits[0][1])
            self._cancel_wiring()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_wiring()
            return
        super().keyPressEvent(event)

    def _delete_selected(self) -> None:
        for item in list(self._scene.selectedItems()):
            if isinstance(item, WireItem):
                key = (item.src_block, item.src_pin, item.dst_block, item.dst_pin)
                if key in self._wires:
                    self._remove_wire(key)
            elif isinstance(item, BlockItem):
                self.remove_block(item.block_id)

    def _block_moved(self, block_item: BlockItem) -> None:
        del block_item  # recompute all wires; simple and robust
        self._rewire_geometry()

    def _rewire_geometry(self) -> None:
        for _key, wire in self._wires.items():
            src_item = self._items[wire.src_block]
            dst_item = self._items[wire.dst_block]
            wire.setLine(
                QLineF(
                    src_item.port_scene_pos(wire.src_pin, PinDirection.OUTPUT),
                    dst_item.port_scene_pos(wire.dst_pin, PinDirection.INPUT),
                )
            )

    # -- engine round trip ------------------------------------------------

    def to_graph(self) -> WireGraph:
        """Build an engine :class:`WireGraph` from the canvas."""
        graph: WireGraph = WireGraph()
        for item in self._items.values():
            graph.add_block(default_registry.create(item.type_name, item.block_id))
        for key in self._wires:
            src_block, src_pin, dst_block, dst_pin = key
            graph.connect(src_block, src_pin, dst_block, dst_pin)
        return graph

    def from_graph(self, graph: WireGraph) -> None:
        """Paint ``graph`` onto the canvas (layers on top of the current scene)."""
        self.clear()
        order = graph.topological_order()  # WireCycleError propagates
        depth: dict[str, int] = {}
        for block_id in order:
            block = graph.block(block_id)
            preds = graph.predecessors(block_id)
            depth[block_id] = 0 if not preds else 1 + max(depth[p] for p in preds)
        columns: dict[int, list[str]] = {}
        for block_id in order:
            columns.setdefault(depth[block_id], []).append(block_id)
        x = 40
        for depth_level in sorted(columns):
            y = 40
            for block_id in columns[depth_level]:
                block = graph.block(block_id)
                self.add_block(block.type_name, block_id, QPointF(x, y))
                y += 140
            x += 220
        for wire in graph.wires():
            self.add_wire(wire.src_block, wire.src_pin, wire.dst_block, wire.dst_pin)


class BlockPalette(QListWidget):
    """Draggable list of the block types in the engine registry."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        for type_name in default_registry.names():
            item = QListWidgetItem(type_name)
            item.setData(Qt.ItemDataRole.UserRole, type_name)
            self.addItem(item)

    def mimeTypes(self) -> list[str]:
        return [BLOCK_MIME, "text/plain"]

    def mimeData(self, items: Sequence[QListWidgetItem]) -> QMimeData:
        mime = QMimeData()
        name = ""
        for item in items:
            if isinstance(item, QListWidgetItem):
                name = item.text()
                break
        if name:
            mime.setData(BLOCK_MIME, QByteArray(name.encode("utf-8")))
            mime.setText(name)
        return mime

    def selected_block_type(self) -> str | None:
        item = self.currentItem()
        return item.text() if item is not None else None


class CanvasWindow(QMainWindow):
    """Top-level window: palette dock + canvas + validate action (T11 demo)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("input-automation — function block canvas")
        self._canvas = BlockCanvas()
        self.setCentralWidget(self._canvas)

        palette = BlockPalette()
        self._canvas.set_palette(palette)
        dock = QDockWidget("Palette", self)
        dock.setWidget(palette)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        bar = self.addToolBar("canvas")
        new_act = QAction("New", self)
        new_act.triggered.connect(self._canvas.clear)
        validate_act = QAction("Validate graph", self)
        validate_act.triggered.connect(self._validate)
        bar.addAction(new_act)
        bar.addAction(validate_act)
        self.statusBar().showMessage(
            "Drag blocks from the palette; click an output pin, then an input pin to wire."
        )

    def _validate(self) -> None:
        try:
            graph = self._canvas.to_graph()
        except Exception as exc:  # noqa: BLE001 - report in the status bar
            self.statusBar().showMessage(f"invalid graph: {exc}", 5000)
            return
        self.statusBar().showMessage(
            f"graph OK: {len(graph.blocks())} blocks, {len(graph.wires())} wires",
            5000,
        )


def run() -> None:
    app = QApplication.instance() or QApplication([])
    win = CanvasWindow()
    win.resize(1000, 700)
    win.show()
    app.exec()
