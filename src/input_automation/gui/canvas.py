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
import re
from collections.abc import Callable, Sequence
from functools import partial
from itertools import chain
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QByteArray,
    QLineF,
    QMimeData,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

from input_automation.devices.discovery import list_devices
from input_automation.engine.block import Block
from input_automation.engine.pins import Pin, PinTypeError
from input_automation.engine.registry import default_registry
from input_automation.engine.scan import ScanExecutor
from input_automation.engine.wires import (
    DuplicateWireError,
    UnknownBlockError,
    UnknownPinError,
    WireGraph,
    WireGraphError,
    WireTypeError,
)
from input_automation.gui.recording import InputRecorder
from input_automation.io.capture import CaptureInputProvider
from input_automation.io.evdev import EvdevInputProvider, code_names
from input_automation.io.uinput import UinputOutputProvider
from input_automation.project import (
    Block as ProjectBlock,
)
from input_automation.project import (
    Pin as ProjectPin,
)
from input_automation.project import (
    Project,
    load_project,
    save_project,
)
from input_automation.project import (
    Wire as ProjectWire,
)
from input_automation.project.pins import PinDirection, PinDType

__all__ = [
    "BlockCanvas",
    "BlockItem",
    "BlockPalette",
    "CanvasWindow",
    "PropertiesPanel",
    "WireItem",
    "run",
]

BLOCK_MIME = "application/x-input-automation-block"

RUN_CYCLE_MS = 50  # scan cycle period while the canvas is running (20 Hz)

GUI_CONFIG_DIR = Path.home() / ".config" / "input-automate"
SETTINGS_FILE = GUI_CONFIG_DIR / "gui.ini"
PROJECT_FILE_FILTER = "Project files (*.yaml *.yml *.json)"

BLOCK_WIDTH = 160
TITLE_HEIGHT = 26
PIN_ROW = 22
PORT = 10
PAD = 6

# Suggested virtual uinput device names for OUTPUT blocks. Virtual output
# devices are created lazily by name (there is nothing to discover under
# /dev/input), so the config panel offers these presets plus free text.
OUTPUT_DEVICE_PRESETS = ("virtual-keyboard", "virtual-mouse")


def _format_pin_value(value: bool | int | float | str | None) -> str:
    """Render a pin's current value for live monitoring (``None`` -> empty)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _project_name(stem: str) -> str:
    """Derive a schema-valid project name from a file name stem."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", stem)
    name = re.sub(r"^[^A-Za-z0-9]+", "", name)
    return name or "project"


def canvas_to_project(canvas: BlockCanvas, name: str) -> Project:
    """Serialize ``canvas`` as a :class:`Project` (validates the graph first)."""
    graph = canvas.to_graph()
    blocks: list[ProjectBlock] = []
    for item in canvas.block_items():
        block = graph.block(item.block_id)
        pins: list[ProjectPin] = []
        for pin in chain(block.inputs.values(), block.outputs.values()):
            pins.append(
                ProjectPin(
                    name=pin.name,
                    direction=pin.direction,
                    dtype=pin.dtype,
                    default=pin.value,
                )
            )
        blocks.append(
            ProjectBlock(
                id=item.block_id,
                type=item.type_name,
                pins=pins,
                parameters=dict(item.parameters),
            )
        )
    wires = [
        ProjectWire(
            src_block=src_block,
            src_pin=src_pin,
            dst_block=dst_block,
            dst_pin=dst_pin,
        )
        for src_block, src_pin, dst_block, dst_pin in canvas.wires()
    ]
    return Project(name=name, blocks=blocks, wires=wires)


def project_to_canvas(project: Project, canvas: BlockCanvas) -> None:
    """Paint ``project`` onto ``canvas``, replacing the current diagram."""
    graph: WireGraph = WireGraph()
    for pblock in project.blocks:
        block = default_registry.create(pblock.type, pblock.id)
        for ppin in pblock.pins:
            try:
                pin = block.input(ppin.name)
            except KeyError:
                pin = block.output(ppin.name)
            if ppin.default is not None:
                pin.set(ppin.default)
        graph.add_block(block)
    for pwire in project.wires:
        graph.connect(
            pwire.src_block, pwire.src_pin, pwire.dst_block, pwire.dst_pin
        )
    canvas.from_graph(graph)


class RecentProjects:
    """Most-recently opened or saved project paths, persisted in QSettings."""

    MAX = 10

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    def paths(self) -> list[str]:
        raw = self._settings.value("recent/projects", [])
        return [str(path) for path in raw] if isinstance(raw, list) else []

    def add(self, path: str | Path) -> None:
        text = str(path)
        paths = [p for p in self.paths() if p != text]
        paths.insert(0, text)
        self._settings.setValue("recent/projects", paths[: self.MAX])

    def remove(self, path: str | Path) -> None:
        text = str(path)
        paths = [p for p in self.paths() if p != text]
        self._settings.setValue("recent/projects", paths)


class _CanvasScene(QGraphicsScene):
    """Scene that keeps wire geometry in sync while blocks are moved.

    Qt never delivers ``ItemPositionHasChanged`` for programmatic
    ``setPos()`` or for interactive drags, so wires would freeze in place
    when a block moves (the "freeze when moved" bug). ``BlockItem.setPos``
    covers programmatic moves; this scene covers interactive drags, which
    Qt drives from C++ and therefore bypasses Python ``setPos`` overrides.
    """

    def __init__(self, canvas: BlockCanvas) -> None:
        super().__init__(canvas)
        self._canvas = canvas

    def mouseMoveEvent(self, event: Any) -> None:
        super().mouseMoveEvent(event)
        self._canvas._rewire_geometry()

    def mouseReleaseEvent(self, event: Any) -> None:
        super().mouseReleaseEvent(event)
        self._canvas._rewire_geometry()


class BlockItem(QGraphicsRectItem):
    """One block: title bar, body, input pins on the left, output pins on the right."""

    def __init__(
        self,
        block_id: str,
        type_name: str,
        input_pins: tuple[Pin, ...] = (),
        output_pins: tuple[Pin, ...] = (),
        parameters: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(0, 0, BLOCK_WIDTH, TITLE_HEIGHT)
        self.block_id = block_id
        self.type_name = type_name
        self.input_pins = list(input_pins)
        self.output_pins = list(output_pins)
        self.parameters: dict[str, Any] = dict(parameters or {})
        self._canvas: BlockCanvas | None = None
        self._pin_values: dict[
            tuple[PinDirection, str], bool | int | float | str | None
        ] = {}
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

    def setPos(self, *args: Any) -> None:
        super().setPos(*args)
        # Qt does not deliver ItemPositionHasChanged on setPos (nor while an
        # item is being dragged), so wires would freeze in place when a
        # block moves. Notify the canvas so all wire geometry is recomputed.
        if self._canvas is not None:
            self._canvas._block_moved(self)

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

    def set_pin_values(
        self, values: dict[tuple[PinDirection, str], bool | int | float | str | None]
    ) -> None:
        """Store live pin values (from the scan engine) and repaint."""
        self._pin_values = dict(values)
        self.update()

    def pin_value(
        self, direction: PinDirection, pin_name: str
    ) -> bool | int | float | str | None:
        """Return the last live value for ``direction`` + ``pin_name``."""
        return self._pin_values.get((direction, pin_name))

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
                self._pin_label(PinDirection.INPUT, pin.name),
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
                self._pin_label(PinDirection.OUTPUT, pin.name),
            )

    def _pin_label(self, direction: PinDirection, pin_name: str) -> str:
        """``pin_name`` plus its live value, e.g. ``IN1 true`` or ``Q false``."""
        value = self._pin_values.get((direction, pin_name))
        if value is None:
            return pin_name
        return f"{pin_name} {_format_pin_value(value)}"

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

    def itemChange(
        self, change: QGraphicsItem.GraphicsItemChange, value: Any
    ) -> Any:
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._refresh_pen()
        return super().itemChange(change, value)


class BlockCanvas(QGraphicsView):
    """The editing canvas: movable blocks, click-to-wire pins, drag-in from the palette."""

    graphChanged = Signal()
    selectionChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewport(_CanvasViewport(self))
        self._scene = _CanvasScene(self)
        self._scene.setSceneRect(QRectF(-2000, -2000, 4000, 4000))
        self.setScene(self._scene)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumSize(640, 480)

        self._palette: BlockPalette | None = None
        self._items: dict[str, BlockItem] = {}
        self._wires: dict[tuple[str, str, str, str], WireItem] = {}
        self._wiring: tuple[str, str] | None = None
        self._rewire: WireItem | None = None
        self._rubber: QGraphicsLineItem | None = None
        self._auto_pos = QPointF(40, 40)
        self._scene.selectionChanged.connect(self._on_selection_changed)

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

    def selected_block(self) -> BlockItem | None:
        """Return the single selected block, or ``None`` if none/multiple are selected."""
        selected = [it for it in self._scene.selectedItems() if isinstance(it, BlockItem)]
        return selected[0] if len(selected) == 1 else None

    def _on_selection_changed(self) -> None:
        self.selectionChanged.emit()

    def add_block(
        self,
        type_name: str,
        block_id: str | None = None,
        pos: QPointF | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> BlockItem:
        """Create a block item of registered ``type_name`` (unique id if omitted).

        ``parameters`` are editable parameter values (e.g. a TON block's ``PT``)
        that are applied to the block's input pins when the canvas is exported
        to an engine :class:`~input_automation.engine.wires.WireGraph`.
        """
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
            parameters=parameters,
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

    def _cancel_rewire(self) -> None:
        self._rewire = None

    def _attach_rewire(self, hit: tuple[str, str, PinDirection]) -> None:
        """Reattach the rewire-pending wire to the clicked port.

        An output click re-sources the wire; an input click re-targets it.
        Conflicts (same block, duplicate wire, type mismatch) are refused.
        """
        block_id, pin_name, direction = hit
        wire = self._rewire
        self._cancel_rewire()
        if wire is None or wire.scene() is None:
            return
        if direction is PinDirection.OUTPUT:
            if block_id == wire.src_block:
                return
            self._rewire_endpoint(wire, src_block=block_id, src_pin=pin_name)
        elif direction is PinDirection.INPUT:
            if block_id == wire.dst_block:
                return
            self._rewire_endpoint(wire, dst_block=block_id, dst_pin=pin_name)

    def _rewire_endpoint(
        self,
        wire: WireItem,
        src_block: str | None = None,
        src_pin: str | None = None,
        dst_block: str | None = None,
        dst_pin: str | None = None,
    ) -> None:
        """Move one endpoint of ``wire``; restore the old wire on conflict."""
        old_key = (wire.src_block, wire.src_pin, wire.dst_block, wire.dst_pin)
        new_key = (
            src_block if src_block is not None else wire.src_block,
            src_pin if src_pin is not None else wire.src_pin,
            dst_block if dst_block is not None else wire.dst_block,
            dst_pin if dst_pin is not None else wire.dst_pin,
        )
        if new_key == old_key:
            return
        self._remove_wire(old_key)
        try:
            new_wire = self.add_wire(*new_key)
        except WireGraphError:
            # duplicate or type mismatch: put the original wire back
            self.add_wire(*old_key)
        else:
            new_wire.setSelected(True)

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

    def dragMoveEvent(self, event: Any) -> None:
        if event.mimeData().hasFormat(BLOCK_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

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
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        scene_pos = self.mapToScene(event.pos())
        hits = self.ports_at(scene_pos)
        if self._rewire is not None and hits:
            # A wire awaits reattachment: an output port re-sources it, an
            # input port re-targets it; conflicts are silently refused.
            self._attach_rewire(hits[0])
            return
        self._cancel_rewire()
        wire_items = [
            item
            for item in self._scene.items(scene_pos)
            if isinstance(item, WireItem)
        ]
        if wire_items:
            self._cancel_wiring()
            wire = wire_items[0]
            wire.setSelected(True)
            self._rewire = wire
            return
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

    def _wiring_target(
        self, hits: list[tuple[str, str, PinDirection]], src_block: str
    ) -> tuple[str, str] | None:
        """First input-port hit on a block other than the wiring source.

        ``ports_at`` returns ports in scene stacking order, so the topmost
        hit may be an output port or the source block's own input — neither
        can receive a new wire. Searching all hits keeps a drag released
        over a cluster of ports from being silently dropped ("line won't
        attach to the tile").
        """
        for block_id, pin_name, direction in hits:
            if direction is PinDirection.INPUT and block_id != src_block:
                return block_id, pin_name
        return None

    def mouseReleaseEvent(self, event: Any) -> None:
        if self._wiring is not None:
            hits = self.ports_at(self.mapToScene(event.pos()))
            target = self._wiring_target(hits, self._wiring[0])
            if target is not None:
                src_block, src_pin = self._wiring
                with contextlib.suppress(WireGraphError):
                    # duplicate or type mismatch: silently refuse
                    self.add_wire(src_block, src_pin, *target)
            self._cancel_wiring()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._cancel_wiring()
            self._cancel_rewire()
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

    # -- live pin values --------------------------------------------------

    def refresh_pin_values(self, graph: WireGraph) -> None:
        """Write ``graph``'s current pin values onto the block items.

        Called after every scan cycle while the canvas runs so the painted
        pin values stay live. Blocks missing from ``graph`` keep their values.
        """
        for block_id, item in self._items.items():
            try:
                block = graph.block(block_id)
            except UnknownBlockError:
                continue
            values: dict[
                tuple[PinDirection, str], bool | int | float | str | None
            ] = {}
            for name, pin in block.inputs.items():
                values[(PinDirection.INPUT, name)] = pin.value
            for name, pin in block.outputs.items():
                values[(PinDirection.OUTPUT, name)] = pin.value
            item.set_pin_values(values)
            item.update()

    # -- engine round trip ------------------------------------------------

    def to_graph(self) -> WireGraph:
        """Build an engine :class:`WireGraph` from the canvas.

        Each block's editable :attr:`BlockItem.parameters` are applied to the
        engine block's input pins, so a parameter such as a TON block's ``PT``
        survives the round trip into the headless engine.
        """
        graph: WireGraph = WireGraph()
        for item in self._items.values():
            block = default_registry.create(item.type_name, item.block_id)
            for name, value in item.parameters.items():
                try:
                    pin = block.input(name)
                except KeyError:
                    raise UnknownPinError(
                        f"block {item.block_id!r}: unknown parameter {name!r}"
                    ) from None
                try:
                    pin.set(value)
                except PinTypeError as exc:
                    raise WireTypeError(str(exc)) from None
            graph.add_block(block)
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
                parameters = {
                    name: pin.value
                    for name, pin in block.inputs.items()
                    if pin.value is not None
                }
                self.add_block(block.type_name, block_id, QPointF(x, y), parameters)
                y += 140
            x += 220
        for wire in graph.wires():
            self.add_wire(wire.src_block, wire.src_pin, wire.dst_block, wire.dst_pin)


class _CanvasViewport(QWidget):
    """Viewport that forwards drag-and-drop events to the canvas.

    A :class:`QGraphicsView` routes drag and drop events to its viewport
    (the widget under the cursor), not to the view itself. ``BlockCanvas``
    reimplements ``dragEnterEvent``/``dragMoveEvent``/``dropEvent``, so the
    viewport forwards them here; otherwise palette drops never reach the
    canvas.
    """

    def __init__(self, canvas: BlockCanvas) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._canvas.dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._canvas.dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._canvas.dropEvent(event)


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


class PropertiesPanel(QWidget):
    """Editable parameters for the single block currently selected on the canvas.

    Shows one editor per input pin that carries a default value (e.g. a TON
    block's ``PT`` or a CTU block's ``PV``). Editing a value updates the
    selected block item's :attr:`~BlockItem.parameters` and emits
    :attr:`parametersChanged` so the canvas can be re-validated / re-exported.
    """

    parametersChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block: BlockItem | None = None
        self._editors: dict[str, tuple[QWidget, PinDType]] = {}
        self._device_combo: QComboBox | None = None
        self._record_button: QPushButton | None = None
        self._record_status: QLabel | None = None
        self._recorder = InputRecorder(parent=self)
        self._record_slot: Callable[[str], None] | None = None
        self._fail_slot: Callable[[str], None] | None = None

        layout = QVBoxLayout(self)
        self._title = QLabel("No block selected")
        self._form = QFormLayout()
        layout.addWidget(self._title)
        layout.addLayout(self._form)
        layout.addStretch(1)

    def set_block(self, block: BlockItem | None) -> None:
        self._recorder.stop()
        self._disconnect_recorder()
        self._block = block
        self._rebuild()

    def _disconnect_recorder(self) -> None:
        """Disconnect recording slots so callbacks never touch deleted widgets.

        The recorder outlives a single properties-panel build (it is parented
        to the panel, not to the record row). Without this, a ``recorded`` /
        ``failed`` signal delivered after ``_rebuild()`` removes the record row
        invokes closures holding already-deleted ``QPushButton``/``QLabel``
        objects, raising ``RuntimeError: ... already deleted``.
        """
        if self._record_slot is not None:
            with contextlib.suppress(TypeError):  # not currently connected
                self._recorder.recorded.disconnect(self._record_slot)
            self._record_slot = None
        if self._fail_slot is not None:
            with contextlib.suppress(TypeError):  # not currently connected
                self._recorder.failed.disconnect(self._fail_slot)
            self._fail_slot = None

    def _rebuild(self) -> None:
        while self._form.count():
            self._form.removeRow(0)
        self._editors.clear()

        if self._block is None:
            self._title.setText("No block selected")
            self._record_button = None
            self._record_status = None
            self._device_combo = None
            return

        self._title.setText(f"{self._block.type_name}  ·  {self._block.block_id}")
        template = default_registry.create(self._block.type_name, self._block.block_id)
        parameters = dict(self._block.parameters)
        is_input = self._block.type_name == "INPUT"
        is_output = self._block.type_name == "OUTPUT"
        is_configurable = is_input or is_output
        if not is_configurable:
            # Only INPUT/OUTPUT blocks expose device selection / recording;
            # drop any stale references from a previously shown block.
            self._record_button = None
            self._record_status = None
            self._device_combo = None

        for name, pin in template.inputs.items():
            if pin.value is None:  # no default -> not a configurable parameter
                continue
            if is_configurable and name == "DEVICE":
                continue  # replaced by the device picker row below
            editor = self._editor(name, pin.dtype, parameters.get(name, pin.value))
            if is_configurable and name == "CODE" and isinstance(editor, QLineEdit):
                self._attach_code_completer(editor)
            self._form.addRow(QLabel(name), editor)
            self._editors[name] = (editor, pin.dtype)
        if is_configurable:
            self._build_device_row(parameters, is_output=is_output)
            self._build_record_row()

    @staticmethod
    def _attach_code_completer(editor: QLineEdit) -> None:
        """Suggest evdev code names (``KEY_A``, ``BTN_LEFT``, …) while typing."""
        completer = QCompleter(code_names(), editor)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        editor.setCompleter(completer)

    def _build_device_row(self, parameters: dict[str, Any], *, is_output: bool = False) -> None:
        assert self._block is not None
        combo = QComboBox()
        combo.setEditable(True)
        if is_output:
            self._populate_output_devices(combo)
            refresh_tooltip = "Virtual output devices are created by name; nothing to rescan"
        else:
            self._populate_devices(combo)
            refresh_tooltip = "Re-scan /dev/input for available devices"

        def device_changed(index: int) -> None:
            block = self._block
            if block is None:
                return
            data = combo.itemData(index)
            path = data if isinstance(data, str) else combo.currentText()
            block.parameters["DEVICE"] = path
            self.parametersChanged.emit()

        combo.currentIndexChanged.connect(device_changed)

        current = str(parameters.get("DEVICE") or "")
        match = next(
            (
                index
                for index in range(combo.count())
                if combo.itemData(index) == current
            ),
            None,
        )
        if match is not None:
            combo.setCurrentIndex(match)
        elif current:
            combo.setEditText(current)

        def refresh_devices() -> None:
            if is_output:
                self._populate_output_devices(combo)
            else:
                self._populate_devices(combo)

        refresh = QPushButton("Refresh")
        refresh.setToolTip(refresh_tooltip)
        refresh.clicked.connect(refresh_devices)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(combo, 1)
        layout.addWidget(refresh)
        self._device_combo = combo
        self._form.addRow(QLabel("Device"), row)

    def _populate_output_devices(self, combo: QComboBox) -> None:
        """Fill the output device combo with suggested virtual device names.

        Virtual uinput devices are created lazily by name (there is nothing to
        discover under /dev/input), so the presets double as the pickable
        targets and the combo stays editable for a free-form device name.
        """
        combo.blockSignals(True)
        combo.clear()
        for name in OUTPUT_DEVICE_PRESETS:
            combo.addItem(name, name)
        combo.blockSignals(False)

    def _populate_devices(self, combo: QComboBox) -> None:
        combo.blockSignals(True)
        combo.clear()
        try:
            devices = list_devices()
        except OSError:
            devices = []
        for info in devices:
            kinds = ", ".join(sorted(info.kinds))
            combo.addItem(f"{info.name} [{kinds}] ({info.path})", info.path)
        combo.blockSignals(False)

    def _build_record_row(self) -> None:
        assert self._block is not None
        button = QPushButton("Record key/button…")
        button.setToolTip(
            "Record a key press or button click from the selected input "
            "device and write its evdev code name (e.g. KEY_Q, BTN_LEFT) "
            "into the CODE field"
        )
        status = QLabel("")
        status.setStyleSheet("color: #6b7280; font-size: 11px;")
        self._record_button = button
        self._record_status = status

        def start_recording() -> None:
            block = self._block
            if block is None:
                return
            path = str(block.parameters.get("DEVICE") or "")
            self._recorder.start([path] if path else None)
            button.setEnabled(False)
            status.setText("recording… press a key or button on the device")

        def on_recorded(code_name: str) -> None:
            # The recorder outlives the record row: a queued ``recorded``
            # signal may arrive after _rebuild() deleted these widgets. Ignore
            # events from any build other than the current one (`is` on a
            # deleted PySide wrapper compares Python identity only, so it is
            # safe even once the C++ object is gone).
            if self._record_button is not button:
                return
            button.setEnabled(True)
            status.setText(f"recorded {code_name}")
            editor = self._editors.get("CODE")
            if editor is not None:
                edit_widget = editor[0]
                if isinstance(edit_widget, QLineEdit):
                    edit_widget.setText(code_name)

        def on_failed(message: str) -> None:
            if self._record_button is not button:
                return
            button.setEnabled(True)
            status.setText(f"record failed: {message}")

        button.clicked.connect(start_recording)
        self._record_slot = on_recorded
        self._fail_slot = on_failed
        self._recorder.recorded.connect(on_recorded)
        self._recorder.failed.connect(on_failed)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(button)
        layout.addWidget(status, 1)
        self._form.addRow(QLabel("Record"), row)

    def _editor(
        self, name: str, dtype: PinDType, value: bool | int | float | str | None
    ) -> QWidget:
        if dtype is PinDType.BOOL:
            checkbox = QCheckBox()
            checkbox.setChecked(bool(value))
            checkbox.setToolTip(f"Set the boolean value of input pin {name}")
            checkbox.toggled.connect(partial(self._set_value, name))
            return checkbox
        if dtype is PinDType.INT:
            spinbox = QSpinBox()
            spinbox.setRange(-2_147_483_648, 2_147_483_647)
            spinbox.setValue(int(value if isinstance(value, int) else 0))
            spinbox.valueChanged.connect(partial(self._set_value, name))
            return spinbox
        if dtype is PinDType.FLOAT:
            float_spin = QDoubleSpinBox()
            float_spin.setRange(-1e9, 1e9)
            float_spin.setValue(float(value if isinstance(value, (int, float)) else 0.0))
            float_spin.valueChanged.connect(partial(self._set_value, name))
            return float_spin
        line_edit = QLineEdit(str(value if value is not None else ""))
        line_edit.textChanged.connect(partial(self._set_value, name))
        return line_edit

    def _set_value(self, name: str, value: bool | int | float | str) -> None:
        if self._block is None:
            return
        self._block.parameters[name] = value
        self.parametersChanged.emit()


class CanvasWindow(QMainWindow):
    """Top-level window: palette dock + canvas, project save/open, validate, run/stop."""

    def __init__(
        self, parent: QWidget | None = None, *, settings: QSettings | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("input-automation — function block canvas")
        self._canvas = BlockCanvas()
        self._executor: ScanExecutor | None = None
        self._timer: QTimer | None = None
        self._reader: EvdevInputProvider | None = None
        self._capturer: CaptureInputProvider | None = None
        self._writer: UinputOutputProvider | None = None
        self._cycle_count = 0
        self._path: Path | None = None
        self._dirty = False
        GUI_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._settings = settings or QSettings(
            str(SETTINGS_FILE), QSettings.Format.IniFormat
        )
        self._recent = RecentProjects(self._settings)
        self.setCentralWidget(self._canvas)

        palette = BlockPalette()
        self._canvas.set_palette(palette)
        self._palette_dock = QDockWidget("Palette", self)
        self._palette_dock.setWidget(palette)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._palette_dock)

        self._properties = PropertiesPanel()
        self._canvas.selectionChanged.connect(
            lambda: self._properties.set_block(self._canvas.selected_block())
        )
        self._properties.parametersChanged.connect(self._canvas.graphChanged)
        self._properties_dock = QDockWidget("Properties", self)
        self._properties_dock.setWidget(self._properties)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._properties_dock)

        bar = self.addToolBar("canvas")
        self._new_act = QAction("New", self)
        self._new_act.setToolTip("Create a new empty project")
        self._new_act.triggered.connect(self._new)
        self._open_act = QAction("Open…", self)
        self._open_act.setToolTip("Open a project file (.yaml or .json)")
        self._open_act.triggered.connect(self._open)
        self._save_act = QAction("Save", self)
        self._save_act.setToolTip("Save the current project")
        self._save_act.triggered.connect(self._save)
        validate_act = QAction("Validate graph", self)
        validate_act.setToolTip("Check the wiring graph: blocks, pins, pin types and cycles")
        validate_act.triggered.connect(self._validate)
        self._run_action = QAction("Run", self)
        self._run_action.setToolTip(
            "Start/stop the cyclic scan engine: read inputs, evaluate blocks, write outputs"
        )
        self._run_action.setCheckable(True)
        self._run_action.triggered.connect(self._toggle_run)
        bar.addAction(self._new_act)
        bar.addAction(self._open_act)
        bar.addAction(self._save_act)
        bar.addSeparator()
        bar.addAction(validate_act)
        bar.addAction(self._run_action)
        self._build_menu_bar()
        self._canvas.graphChanged.connect(self._mark_dirty)
        self._update_title()
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

    # -- run / stop -------------------------------------------------------

    def _toggle_run(self, _checked: bool = False) -> None:
        del _checked  # state is driven by _timer/_executor, not the action
        if self._timer is not None:
            self._stop_run()
            return
        try:
            graph = self._canvas.to_graph()
            graph.topological_order()  # refuse to run cyclic graphs
        except Exception as exc:  # noqa: BLE001 - report in the status bar
            self.statusBar().showMessage(f"invalid graph: {exc}", 5000)
            return
        reader = EvdevInputProvider()
        capturer = CaptureInputProvider()
        writer = UinputOutputProvider()

        def read_inputs(graph: WireGraph) -> None:
            reader(graph)
            capturer(graph)

        self._executor = ScanExecutor(
            graph,
            cycle_time_s=RUN_CYCLE_MS / 1000,
            read_inputs=read_inputs,
            write_outputs=writer,
        )
        self._reader, self._capturer, self._writer = reader, capturer, writer
        self._cycle_count = 0
        timer = QTimer(self)
        timer.setInterval(RUN_CYCLE_MS)
        timer.timeout.connect(self._run_cycle)
        timer.start()
        self._timer = timer
        self._run_cycle()  # first cycle right away
        self._run_action.setChecked(True)
        self._run_action.setText("Stop")
        self.statusBar().showMessage(f"running: {self._cycle_count} cycles")

    def _run_cycle(self) -> None:
        assert self._executor is not None
        try:
            self._executor.run_once()
        except Exception as exc:  # noqa: BLE001 - stop cleanly, report in bar
            self._stop_run()
            self.statusBar().showMessage(f"run error: {exc}", 5000)
            return
        self._cycle_count += 1
        self._canvas.refresh_pin_values(self._executor.graph)
        self.statusBar().showMessage(f"running: {self._cycle_count} cycles")

    def _stop_run(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        for provider in (self._reader, self._capturer, self._writer):
            if provider is not None:
                with contextlib.suppress(Exception):
                    provider.close()
        self._reader = None
        self._capturer = None
        self._writer = None
        self._executor = None
        self._run_action.setChecked(False)
        self._run_action.setText("Run")
        self.statusBar().showMessage("stopped")

    # -- project save / open ---------------------------------------------

    def _build_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self._new_act)
        file_menu.addAction(self._open_act)
        file_menu.addAction(self._save_act)
        save_as_act = QAction("Save As…", self)
        save_as_act.setToolTip("Save the project under a new name")
        save_as_act.triggered.connect(self._save_as)
        file_menu.addAction(save_as_act)
        file_menu.addSeparator()
        self._recent_menu: QMenu = file_menu.addMenu("Open Recent")
        file_menu.addSeparator()
        exit_act = QAction("Exit", self)
        exit_act.setToolTip("Close the application")
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)
        file_menu.aboutToShow.connect(self._refresh_recent_menu)
        self._refresh_recent_menu()

        view_menu = self.menuBar().addMenu("&View")
        self._view_properties_act = QAction("Properties", self, checkable=True)
        self._view_properties_act.setToolTip("Show or hide the properties panel")
        self._view_properties_act.setChecked(self._properties_dock.isVisible())
        self._view_properties_act.toggled.connect(self._properties_dock.setVisible)
        self._properties_dock.visibilityChanged.connect(
            self._view_properties_act.setChecked
        )
        view_menu.addAction(self._view_properties_act)

        self._view_palette_act = QAction("Palette", self, checkable=True)
        self._view_palette_act.setToolTip("Show or hide the block palette")
        self._view_palette_act.setChecked(self._palette_dock.isVisible())
        self._view_palette_act.toggled.connect(self._palette_dock.setVisible)
        self._palette_dock.visibilityChanged.connect(
            self._view_palette_act.setChecked
        )
        view_menu.addAction(self._view_palette_act)

    def _refresh_recent_menu(self) -> None:
        self._recent_menu.clear()
        for path in self._recent.paths():
            if not Path(path).exists():
                continue
            action = QAction(path, self)
            action.setData(path)
            action.triggered.connect(partial(self._open_path, Path(path)))
            self._recent_menu.addAction(action)
        if not self._recent_menu.actions():
            empty = QAction("(no recent projects)", self)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)

    def _start_dir(self) -> Path:
        if self._path is not None and self._path.parent.exists():
            return self._path.parent
        projects_dir = GUI_CONFIG_DIR / "projects"
        if projects_dir.exists():
            return projects_dir
        return Path.home()

    def _open(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self, "Open project", str(self._start_dir()), PROJECT_FILE_FILTER
        )
        if not path:
            return
        self._open_path(Path(path))

    def _open_path(self, path: Path) -> None:
        try:
            project = load_project(path)
        except Exception as exc:  # noqa: BLE001 - show the failure in a dialog
            QMessageBox.critical(self, "Open project", f"failed to load {path}:\n{exc}")
            return
        try:
            project_to_canvas(project, self._canvas)
        except Exception as exc:  # noqa: BLE001 - show the failure in a dialog
            QMessageBox.critical(self, "Open project", f"failed to paint {path}:\n{exc}")
            return
        self._path = path
        self._dirty = False
        self._recent.add(path)
        self._update_title()
        self.statusBar().showMessage(
            f"opened {path.name}: {len(project.blocks)} blocks, {len(project.wires)} wires",
            3000,
        )

    def _save(self) -> bool:
        if self._path is None:
            return self._save_as()
        try:
            project = canvas_to_project(self._canvas, _project_name(self._path.stem))
            save_project(project, self._path)
        except Exception as exc:  # noqa: BLE001 - show the failure in a dialog
            QMessageBox.critical(self, "Save project", f"failed to save {self._path}:\n{exc}")
            return False
        self._dirty = False
        self._recent.add(self._path)
        self._update_title()
        self.statusBar().showMessage(f"saved {self._path}", 3000)
        return True

    def _save_as(self) -> bool:
        path_str, _selected = QFileDialog.getSaveFileName(
            self, "Save project", str(self._start_dir()), PROJECT_FILE_FILTER
        )
        if not path_str:
            return False
        path = Path(path_str)
        if path.suffix.lower() not in (".yaml", ".yml", ".json"):
            path = path.with_suffix(".yaml")
        self._path = path
        return self._save()

    def _new(self) -> None:
        self._canvas.clear()
        self._path = None
        self._dirty = False
        self._update_title()
        self.statusBar().showMessage("new project", 2000)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_title()

    def _update_title(self) -> None:
        name = self._path.stem if self._path else "untitled"
        title = f"input-automation — function block canvas — {name}"
        if self._path is None:
            title += " (unsaved)"
        if self._dirty:
            title += " •"
        self.setWindowTitle(title)


def run() -> None:
    app = QApplication.instance() or QApplication([])
    win = CanvasWindow()
    win.resize(1000, 700)
    win.show()
    app.exec()
