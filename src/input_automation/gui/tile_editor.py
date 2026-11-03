"""Tile editor: the main window (M6 T17).

A vertical tile tree represents the macro: a ``Triggers`` section (input
tiles) and a ``Steps`` section whose nesting IS the step tree — ``if``/
``while`` tiles carry ``then``/``else``/``body`` branch rows. The palette
on the left offers Input/Logic/Timer/Output tiles; drag one onto the tree
(or double-click) to insert it. The selected tile is configured inline in
the panel below the tree; every change updates the macro and the live
YAML preview on the right.

Design invariants:
- The macro model stays the source of truth. The tree is rebuilt from it
  on load, and after every user mutation the macro is rebuilt from the
  tree and schema-validated; invalid mutations (e.g. a step dragged into
  the trigger section) revert the tree to the last good macro.
- Nothing here bypasses the same engine the CLI uses: test-run executes
  the current macro through :class:`MacroOrchestrator` with a NullSink.

Headless-testable: device/macro providers are injectable.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import shiboken6  # noqa: TID251 - ships with PySide6; used for Qt object liveness
from PySide6.QtCore import QByteArray, QMimeData, Qt, QTimer
from PySide6.QtGui import QAction, QDrag
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from input_automation.capture.factory import grab_first_frame
from input_automation.devices.discovery import list_devices
from input_automation.gui.editor import MacroEditorDialog, _action_summary, new_macro_template
from input_automation.gui.region_picker import RegionPickerDialog
from input_automation.macros.loader import (
    LoadError,
    load_macro,
    load_macros,
    macro_to_yaml_text,
)
from input_automation.macros.models import (
    Action,
    Condition,
    IfStep,
    Macro,
    SetStep,
    Step,
    StepKind,
    Trigger,
    TriggerKind,
    WhileStep,
)
from input_automation.macros.orchestrator import MacroOrchestrator, NullSink

__all__ = ["DevicesDialog", "TileEditorWindow", "run", "DEFAULT_MACRO_DIR", "TILE_MIME"]

# Macro home per AGENTS.md: ~/.config/input-automate/macros/
DEFAULT_MACRO_DIR = Path.home() / ".config" / "input-automate" / "macros"

TILE_MIME = "application/x-input-automation-tile"

_TILE_DATA = Qt.ItemDataRole.UserRole

_OPERATORS = ("equals", "not_equals", "greater_than", "less_than")

TRIGGER_TILE: dict[str, Any] = {
    "type": "trigger",
    "kind": "key",
    "device": "any",
    "code": "F10",
    "state": "down",
}


def _tile_data(key: str) -> dict[str, Any]:
    """A fresh data dict for a palette tile (deep copy of the template)."""
    if key == "trigger":
        return dict(TRIGGER_TILE)
    if key == "trigger_button":
        return {**TRIGGER_TILE, "kind": "button", "code": "LEFT"}
    if key in ("if", "while"):
        return {
            **TILE_TEMPLATES[key],
            "condition": dict(TILE_TEMPLATES[key]["condition"]),
        }
    return dict(TILE_TEMPLATES[key])


TILE_TEMPLATES: dict[str, dict[str, Any]] = {
    "tap": {"type": "action", "op": "tap", "target": "A"},
    "press": {"type": "action", "op": "press", "target": "A"},
    "release": {"type": "action", "op": "release", "target": "A"},
    "click": {"type": "action", "op": "click", "button": 1},
    "move": {"type": "action", "op": "move", "dx": 10, "dy": 0},
    "scroll": {"type": "action", "op": "scroll", "dy": -1},
    "wait": {"type": "action", "op": "wait", "ms": 100},
    "if": {"type": "if", "condition": {"var": "hp", "op": "less_than", "value": 10}},
    "while": {
        "type": "while",
        "condition": {"var": "hp", "op": "greater_than", "value": 0},
        "max_iterations": 1000,
    },
    "set": {"type": "set", "var": "hp", "value": 10},
}

# Palette groups: (group, label, template key)
PALETTE: list[tuple[str, str, str]] = [
    ("Input", "⚡ On key press", "trigger"),
    ("Input", "⚡ On mouse button", "trigger_button"),
    ("Logic", "◆ If condition", "if"),
    ("Logic", "◆ While loop", "while"),
    ("Logic", "◆ Set variable", "set"),
    ("Output", "▢ Tap key", "tap"),
    ("Output", "▢ Press key", "press"),
    ("Output", "▢ Release key", "release"),
    ("Output", "▢ Click mouse button", "click"),
    ("Output", "▢ Move mouse", "move"),
    ("Output", "▢ Scroll", "scroll"),
    ("Timer", "▢ Wait ms", "wait"),
]


def _tree_row(parent: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
    """``parent.child(index)`` with the Qt stub's Optional unwrapped."""
    item = parent.child(index)
    assert item is not None  # noqa: S101 - index is within range
    return item


def _section(tree: QTreeWidget, index: int) -> QTreeWidgetItem:
    """``tree.topLevelItem(index)`` with the Qt stub's Optional unwrapped."""
    item = tree.topLevelItem(index)
    assert item is not None  # noqa: S101 - sections are created in _populate_tree
    return item


def _tile_label(data: dict[str, Any]) -> str:
    """Human-readable one-line label for a tree row."""
    t = data["type"]
    if t == "trigger":
        return f"⚡ {data['kind']} {data['code']}  ({data['device']}, {data['state']})"
    if t == "action":
        action = Action(
            kind=StepKind.ACTION,
            **{k: v for k, v in data.items() if k != "type"},
        )
        return "▢ " + _action_summary(action)
    if t == "if":
        c = data["condition"]
        return f"◆ if  {c['var']} {c['op']} {c['value']!r}"
    if t == "while":
        c = data["condition"]
        return (
            f"◆ while  {c['var']} {c['op']} {c['value']!r}"
            f"  (max {data['max_iterations']})"
        )
    if t == "set":
        return f"◆ set  {data['var']} = {data['value']!r}"
    if t == "branch":
        return f"↳ {data['which']}"
    return str(t)


class TilePalette(QListWidget):
    """Drag source: each item carries its tile key; drags use TILE_MIME."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for group, label, key in PALETTE:
            item = QListWidgetItem(label)
            item.setData(_TILE_DATA, key)
            item.setToolTip(f"{group} tile — drag onto the tree")
            self.addItem(item)

    def startDrag(self, actions: Any) -> None:  # noqa: N802 - Qt naming
        item = self.currentItem()
        if item is None:
            return
        key = str(item.data(_TILE_DATA))
        mime = QMimeData()
        mime.setData(TILE_MIME, QByteArray(json.dumps({"tile": key}).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class TreeWidget(QTreeWidget):
    """The macro tree: accepts palette drops and internal reordering."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.on_tile_drop: Any = None  # callable(tile_key, target_item | None)
        self.on_moved: Any = None  # callable() after an internal move

    # -- drag & drop ---------------------------------------------------------

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasFormat(TILE_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasFormat(TILE_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if event.mimeData().hasFormat(TILE_MIME):
            payload = json.loads(bytes(event.mimeData().data(TILE_MIME)))
            if self.on_tile_drop is not None:
                self.on_tile_drop(payload["tile"], self.itemAt(event.position().toPoint()))
            event.acceptProposedAction()
            return
        super().dropEvent(event)
        # Internal moves land after the drop; sync once the model settles.
        if self.on_moved is not None:
            QTimer.singleShot(0, self.on_moved)


class DevicesDialog(QDialog):
    """Lists connected input devices (moved off the main window)."""

    def __init__(
        self,
        *,
        device_provider: Any = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Devices")
        self.resize(520, 360)
        self._provider = device_provider or list_devices
        layout = QVBoxLayout(self)
        self._list = QListWidget()
        layout.addWidget(self._list)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.refresh()

    def refresh(self) -> None:
        self._list.clear()
        try:
            devices = list(self._provider())
        except Exception as exc:  # noqa: BLE001 - shown in the list
            self._list.addItem(f"device refresh failed: {exc!r}")
            return
        if not devices:
            self._list.addItem("No input devices found.")
        for device in devices:
            kinds = ", ".join(sorted(device.kinds))
            self._list.addItem(f"{device.path}  {device.name}  [{kinds}]")


class TileEditorWindow(QMainWindow):
    """The app's first window: palette, tile tree, config, live YAML."""

    def __init__(
        self,
        *,
        device_provider: Any = None,
        macro_provider: Any = None,
        macro_dir: Path = DEFAULT_MACRO_DIR,
    ) -> None:
        super().__init__()
        self.setWindowTitle("input-automation")
        self.resize(1200, 700)
        self._device_provider = device_provider or list_devices
        self._macro_provider = macro_provider or self._default_macro_provider
        self._macro_dir = macro_dir
        self._macro: Macro | None = None
        self._path: Path | None = None
        # the tree item the config panel is currently bound to; config edits
        # against anything else are stale (their row may already be deleted)
        self._config_item: QTreeWidgetItem | None = None

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        # -- toolbar row ------------------------------------------------------
        bar = QHBoxLayout()
        self._macro_combo = QComboBox()
        self._macro_combo.setMinimumWidth(220)
        self._macro_combo.currentTextChanged.connect(self._open_selected_macro)
        self._new_btn = QPushButton("New")
        self._save_btn = QPushButton("Save")
        self._test_btn = QPushButton("Test run (dry)")
        self._yaml_btn = QPushButton("YAML editor")
        self._devices_btn = QPushButton("Devices")
        self._region_btn = QPushButton("Pick region")
        for widget in (
            self._macro_combo,
            self._new_btn,
            self._save_btn,
            self._test_btn,
            self._yaml_btn,
            self._devices_btn,
            self._region_btn,
        ):
            bar.addWidget(widget)
        bar.addStretch(1)
        layout.addLayout(bar)

        # -- main splitters ---------------------------------------------------
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)

        self._palette = TilePalette()
        split.addWidget(self._palette)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self._tree = TreeWidget()
        center_layout.addWidget(self._tree, 1)
        self._config_host = QWidget()
        self._config_layout = QHBoxLayout(self._config_host)
        self._config_layout.setContentsMargins(0, 4, 0, 0)
        center_layout.addWidget(self._config_host)
        split.addWidget(center)

        self._yaml_view = QPlainTextEdit()
        self._yaml_view.setReadOnly(True)
        split.addWidget(self._yaml_view)
        split.setSizes([200, 640, 360])

        self.setCentralWidget(central)
        self.statusBar().showMessage("Ready.")

        # -- wiring -----------------------------------------------------------
        self._new_btn.clicked.connect(self._new_macro)
        self._save_btn.clicked.connect(self._save_macro)
        self._test_btn.clicked.connect(self._test_run)
        self._yaml_btn.clicked.connect(self._open_yaml_editor)
        self._devices_btn.clicked.connect(self._open_devices)
        self._region_btn.clicked.connect(self._open_region_picker)
        self._tree.itemSelectionChanged.connect(self._build_config_panel)
        self._tree.on_tile_drop = self._insert_tile
        self._tree.on_moved = self._sync_from_tree
        self._palette.itemDoubleClicked.connect(
            lambda item: self._insert_tile(str(item.data(_TILE_DATA)), None)
        )

        self._build_tray()
        self._reload_macro_combo()

    # -- macro files ---------------------------------------------------------

    @staticmethod
    def _default_macro_provider(directory: Path) -> list[tuple[Path, Macro]]:
        macros: list[tuple[Path, Macro]] = []
        for path, macro in load_macros(directory):
            macros.append((path, macro))
        return macros

    def _reload_macro_combo(self) -> None:
        """Fill the macro combo from the macro directory (tolerant)."""
        self._macro_combo.blockSignals(True)
        self._macro_combo.clear()
        try:
            entries = self._macro_provider(self._macro_dir)
        except Exception as exc:  # noqa: BLE001 - surfaced in the status bar
            self.statusBar().showMessage(f"macro load failed: {exc!r}")
            entries = []
        for path, _macro in entries:
            self._macro_combo.addItem(path.name, str(path))
        self._macro_combo.blockSignals(False)
        if entries:
            self._open_selected_macro()
        else:
            # no macro files yet: open directly in "new macro" mode so the
            # tile tree is ready to receive dragged tiles
            self._new_macro()

    def _open_selected_macro(self, *_args: Any) -> None:
        name = self._macro_combo.currentData()
        if not name:
            return
        path = Path(str(name))
        try:
            macro = load_macro(path)
        except LoadError as exc:
            self.statusBar().showMessage(f"could not load {path.name}: {exc}")
            return
        self._path = path
        self._set_macro(macro)
        self.statusBar().showMessage(f"opened {path.name}")

    def _new_macro(self) -> None:
        self._path = None
        self._macro_combo.blockSignals(True)
        self._macro_combo.setCurrentIndex(-1)
        self._macro_combo.blockSignals(False)
        self._set_macro(new_macro_template())
        self.statusBar().showMessage(
            "New macro — Save asks for a file name (in ~/.config/input-automate/macros)."
        )

    # -- tree <-> model ------------------------------------------------------

    def _set_macro(self, macro: Macro) -> None:
        self._macro = macro
        self._populate_tree(macro)
        self._yaml_view.setPlainText(macro_to_yaml_text(macro))
        self._build_config_panel()

    def _populate_tree(self, macro: Macro) -> None:
        # the tree is rebuilt: every previously bound config widget is now
        # stale; guard callbacks via _config_item (rows get deleted here)
        self._config_item = None
        tree = self._tree
        tree.clear()
        triggers_root = QTreeWidgetItem(["Triggers"])
        triggers_root.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        tree.addTopLevelItem(triggers_root)
        for trigger in macro.triggers:
            data: dict[str, Any] = {
                "type": "trigger",
                "kind": str(trigger.kind),
                "device": trigger.device,
                "code": trigger.code,
                "state": trigger.state,
            }
            triggers_root.addChild(self._make_row(data, selectable=True))

        steps_root = QTreeWidgetItem(["Steps"])
        steps_root.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        tree.addTopLevelItem(steps_root)
        for step in macro.steps:
            self._add_step_row(steps_root, step)
        tree.expandAll()

    def _make_row(self, data: dict[str, Any], *, selectable: bool) -> QTreeWidgetItem:
        item = QTreeWidgetItem([_tile_label(data)])
        item.setData(0, _TILE_DATA, data)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled
        if selectable:
            flags |= Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        item.setFlags(flags)
        return item

    def _add_step_row(self, parent: QTreeWidgetItem, step: Step) -> None:
        if isinstance(step, Action):
            data: dict[str, Any] = {
                "type": "action",
                **{k: v for k, v in step.model_dump(by_alias=False).items()
                   if k != "kind"},
            }
            data = {k: v for k, v in data.items() if v is not None}
            parent.addChild(self._make_row(data, selectable=True))
            return
        if isinstance(step, SetStep):
            data = {"type": "set", "var": step.var, "value": step.value}
            parent.addChild(self._make_row(data, selectable=True))
            return
        condition = self._condition_data(step.condition)
        if isinstance(step, IfStep):
            data = {"type": "if", "condition": condition}
            row = self._make_row(data, selectable=True)
            then_branch = self._make_row(
                {"type": "branch", "which": "then"}, selectable=False
            )
            for child in step.then:
                self._add_step_row(then_branch, child)
            row.addChild(then_branch)
            else_branch = self._make_row(
                {"type": "branch", "which": "else"}, selectable=False
            )
            for child in step.else_:
                self._add_step_row(else_branch, child)
            row.addChild(else_branch)
            parent.addChild(row)
            return
        if isinstance(step, WhileStep):
            data = {
                "type": "while",
                "condition": condition,
                "max_iterations": step.max_iterations,
            }
            row = self._make_row(data, selectable=True)
            body_branch = self._make_row(
                {"type": "branch", "which": "body"}, selectable=False
            )
            for child in step.body:
                self._add_step_row(body_branch, child)
            row.addChild(body_branch)
            parent.addChild(row)

    @staticmethod
    def _condition_data(condition: Condition) -> dict[str, Any]:
        ops = ("equals", "not_equals", "greater_than", "less_than")
        op = next(o for o in ops if getattr(condition, o) is not None)
        return {"var": condition.var, "op": op, "value": getattr(condition, op)}

    def _macro_from_tree(self) -> Macro:
        """Rebuild the macro from the tree; raises on invalid structures."""
        tree = self._tree
        if tree.topLevelItemCount() < 2:
            raise ValueError("tree is missing sections")
        triggers_root, steps_root = _section(tree, 0), _section(tree, 1)

        triggers: list[Trigger] = []
        for i in range(triggers_root.childCount()):
            data = _tree_row(triggers_root, i).data(0, _TILE_DATA)
            triggers.append(
                Trigger(
                    kind=TriggerKind(data["kind"]),
                    device=data["device"],
                    code=data["code"],
                    state=data["state"],
                )
            )
        steps = [
            self._parse_step(_tree_row(steps_root, i))
            for i in range(steps_root.childCount())
        ]
        if not steps and self._macro is not None and self._macro.steps:
            # A macro needs >= 1 step (schema). Editing a trigger while the
            # step tree is still empty must not wipe/revert the macro —
            # keep the existing steps and tell the user why.
            steps = list(self._macro.steps)
            self.statusBar().showMessage(
                "A macro needs at least one step — kept the existing steps."
            )
        return Macro(name=self._macro_name(), description=self._macro_description(),
                     triggers=triggers, steps=steps)

    def _macro_name(self) -> str:
        """Macro name: file stem (sanitized) or from the current macro."""
        if self._path is not None:
            return self._stem_name(self._path)
        if self._macro is not None:
            return self._macro.name
        return "new-macro"

    @staticmethod
    def _stem_name(path: Path) -> str:
        """Sanitize a file stem into a valid macro name (schema pattern)."""
        stem = "".join(ch for ch in path.stem if ch.isalnum() or ch in "_-")
        if stem and stem[0].isalpha():
            return stem.lower()
        return "new-macro"

    def _macro_description(self) -> str:
        return self._macro.description if self._macro is not None else ""

    def _parse_step(self, item: QTreeWidgetItem) -> Step:
        data: dict[str, Any] = item.data(0, _TILE_DATA)
        t = data["type"]
        if t == "action":
            return Action(kind=StepKind.ACTION, **{k: v for k, v in data.items() if k != "type"})
        if t == "set":
            return SetStep(kind=StepKind.SET, var=data["var"], value=data["value"])
        condition = self._build_condition(data["condition"])
        if t == "if":
            branches: dict[str, list[Step]] = {"then": [], "else": []}
            for i in range(item.childCount()):
                child = _tree_row(item, i)
                cdata: dict[str, Any] = child.data(0, _TILE_DATA)
                if cdata["type"] != "branch":
                    continue
                which = cdata["which"]
                branches[which] = [
                    self._parse_step(_tree_row(child, j))
                    for j in range(child.childCount())
                ]
            return IfStep.model_validate(
                {
                    "kind": "if",
                    "condition": condition,
                    "then": branches["then"],
                    "else": branches["else"],
                }
            )
        if t == "while":
            body: list[Step] = []
            for i in range(item.childCount()):
                child = _tree_row(item, i)
                cdata = child.data(0, _TILE_DATA)
                if cdata["type"] == "branch" and cdata["which"] == "body":
                    body = [
                        self._parse_step(_tree_row(child, j))
                        for j in range(child.childCount())
                    ]
            return WhileStep(
                kind=StepKind.WHILE,
                condition=condition,
                body=body,
                max_iterations=data["max_iterations"],
            )
        raise ValueError(f"unexpected tree row type {t!r}")

    @staticmethod
    def _build_condition(cdata: dict[str, Any]) -> Condition:
        value: str | int | float = cdata["value"]
        if isinstance(value, str):
            for caster in (int, float):
                try:
                    value = caster(value)
                    break
                except ValueError:
                    continue
        return Condition.model_validate({"var": cdata["var"], cdata["op"]: value})

    # -- mutations -----------------------------------------------------------

    def _sync_from_tree(self) -> None:
        """Rebuild the macro from the tree; revert on validation failure."""
        try:
            macro = self._macro_from_tree()
        except Exception as exc:  # noqa: BLE001 - invalid mutation -> revert
            self.statusBar().showMessage(f"invalid structure ({exc!r}) — reverted")
            if self._macro is not None:
                self._populate_tree(self._macro)
            return
        self._macro = macro
        self._yaml_view.setPlainText(macro_to_yaml_text(macro))
        self.statusBar().showMessage("Macro updated.")

    def _insert_tile(self, tile_key: str, target: QTreeWidgetItem | None) -> None:
        """Insert a palette tile relative to ``target`` (or at the end)."""
        data = _tile_data(tile_key)
        if data["type"] == "trigger":
            triggers_root = _section(self._tree, 0)  # Triggers section
            triggers_root.addChild(self._make_row(data, selectable=True))
            self._sync_from_tree()
            return

        steps_root = _section(self._tree, 1)
        triggers_root = _section(self._tree, 0)
        parent: QTreeWidgetItem = steps_root
        if target is not None and target is not steps_root and target is not triggers_root:
            tdata: dict[str, Any] | None = target.data(0, _TILE_DATA)
            if tdata is not None and tdata["type"] == "branch":
                parent = target  # drop onto then/else/body -> child of it
            else:
                parent = target.parent() or steps_root

        if data["type"] in ("if", "while"):
            branch_which = "then" if data["type"] == "if" else "body"
            row = self._make_row(data, selectable=True)
            branch = self._make_row(
                {"type": "branch", "which": branch_which}, selectable=False
            )
            default_child = self._make_row(
                dict(TILE_TEMPLATES["wait"]), selectable=True
            )
            branch.addChild(default_child)
            row.addChild(branch)
            if data["type"] == "if":
                row.addChild(
                    self._make_row(
                        {"type": "branch", "which": "else"}, selectable=False
                    )
                )
            self._add_row_at(parent, row)
        else:
            self._add_row_at(parent, self._make_row(data, selectable=True))
        self._sync_from_tree()

    def _add_row_at(self, parent: QTreeWidgetItem, row: QTreeWidgetItem) -> None:
        """Insert ``row`` after the selected sibling (or append)."""
        selected = self._tree.currentItem()
        if selected is not None and selected.parent() is parent:
            index = parent.indexOfChild(selected) + 1
        else:
            index = parent.childCount()
        parent.insertChild(index, row)

    # -- config panel --------------------------------------------------------

    def _build_config_panel(self) -> None:
        """Rebuild the inline config form for the selected tile."""
        while True:
            child = self._config_layout.takeAt(0)
            if child is None:
                break
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        item = self._tree.currentItem()
        self._config_item = None  # rebuilt below only for editable tiles
        if item is None:
            self._config_layout.addWidget(QLabel("Drag tiles from the palette."))
            return
        data: dict[str, Any] | None = item.data(0, _TILE_DATA)
        if data is None or data["type"] == "branch":
            self._config_layout.addWidget(
                QLabel(_tile_label(data) if data else "Select a tile to configure.")
            )
            return
        self._config_item = item

        form = QWidget()
        layout = QHBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        t = data["type"]

        def text_field(label: str, value: Any, on_change: Any) -> None:
            layout.addWidget(QLabel(label))
            edit = QLineEdit("" if value is None else str(value))
            edit.editingFinished.connect(lambda: on_change(edit.text()))
            layout.addWidget(edit)

        def spin_field(label: str, value: int, on_change: Any) -> None:
            layout.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(-10_000_000, 10_000_000)
            spin.setValue(int(value))
            spin.valueChanged.connect(lambda v: on_change(v))
            layout.addWidget(spin)

        def combo_field(label: str, options: list[tuple[str, Any]],
                        value: Any, on_change: Any) -> None:
            """Data-driven combo: shows ``display``, passes ``data`` on change."""
            layout.addWidget(QLabel(label))
            combo = QComboBox()
            for display, option_data in options:
                combo.addItem(display, option_data)
            index = combo.findData(value)
            combo.setCurrentIndex(max(0, index))
            combo.currentIndexChanged.connect(
                lambda _i: on_change(combo.currentData())
            )
            layout.addWidget(combo)

        if t == "trigger":
            combo_field("kind", [("key", "key"), ("button", "button")],
                        data["kind"],
                        lambda v: self._update_tile(item, kind=v))
            combo_field("device", self._device_choices(), data["device"],
                        lambda v: self._update_tile(item, device=v))
            text_field("code", data["code"],
                       lambda v: self._update_tile(item, code=v.upper()))
            combo_field("state", [("down", "down"), ("up", "up")],
                        data["state"],
                        lambda v: self._update_tile(item, state=v))
        elif t == "action":
            op = data["op"]
            if op in ("tap", "press", "release"):
                text_field("target", data.get("target"),
                           lambda v: self._update_tile(item, target=v.upper()))
            elif op == "click":
                combo_field("button",
                            [("left", 1), ("middle", 2), ("right", 3)],
                            data["button"],
                            lambda v: self._update_tile(item, button=v))
            elif op in ("move", "scroll"):
                spin_field("dx", data.get("dx") or 0,
                           lambda v: self._update_tile(item, dx=v))
                spin_field("dy", data.get("dy") or 0,
                           lambda v: self._update_tile(item, dy=v))
            elif op == "wait":
                spin_field("ms", data["ms"],
                           lambda v: self._update_tile(item, ms=v))
        elif t in ("if", "while"):
            combo_field("condition", [(op, op) for op in _OPERATORS],
                        data["condition"]["op"],
                        lambda v: self._update_condition(item, op=v))
            text_field("var", data["condition"]["var"],
                       lambda v: self._update_condition(item, var=v))
            text_field("value", data["condition"]["value"],
                       lambda v: self._update_condition(item, value=v))
            if t == "while":
                spin_field("max", data["max_iterations"],
                           lambda v: self._update_tile(item, max_iterations=v))
        elif t == "set":
            text_field("var", data["var"],
                       lambda v: self._update_tile(item, var=v))
            text_field("value", data["value"],
                       lambda v: self._update_tile(item, value=v))

        remove = QPushButton("Remove tile")
        remove.clicked.connect(lambda: self._remove_tile(item))
        layout.addWidget(remove)
        layout.addStretch(1)
        self._config_layout.addWidget(form)

    def _device_choices(self) -> list[tuple[str, str]]:
        """(stored value, display) pairs; display shows the device NAME.

        Users cannot tell devices apart by event numbers — the combo shows
        ``"<name> (<path>)"`` while the stored value stays the path.
        """
        choices: list[tuple[str, str]] = [("any", "any device")]
        # device list is best-effort here: an empty combo still allows "any"
        with contextlib.suppress(Exception):
            for device in list(self._device_provider()):
                choices.append((device.path, f"{device.name} ({device.path})"))
        return choices

    def _stale_panel(self, item: QTreeWidgetItem) -> bool:
        """True when ``item`` is not the row this config panel is bound to.

        The panel survives tree rebuilds (save/reload, reverts) — its
        widgets then hold references to deleted QTreeWidgetItems. Guard
        every config callback through this so a stale edit is ignored
        instead of crashing on a dead C++ object.
        """
        return not shiboken6.isValid(item) or item is not self._config_item

    def _update_tile(self, item: QTreeWidgetItem, **changes: Any) -> None:
        if self._stale_panel(item):
            return  # panel was rebuilt (or its row deleted) — ignore silently
        data: dict[str, Any] = item.data(0, _TILE_DATA)
        data.update(changes)
        # item.data() returns a COPY — store the mutated dict back explicitly
        item.setData(0, _TILE_DATA, data)
        item.setText(0, _tile_label(data))
        self._sync_from_tree()

    def _update_condition(self, item: QTreeWidgetItem, **changes: Any) -> None:
        if self._stale_panel(item):
            return  # panel was rebuilt (or its row deleted) — ignore silently
        data: dict[str, Any] = item.data(0, _TILE_DATA)
        condition: dict[str, Any] = dict(data["condition"])
        condition.update(changes)
        data["condition"] = condition
        item.setData(0, _TILE_DATA, data)
        item.setText(0, _tile_label(data))
        self._sync_from_tree()

    def _remove_tile(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        if parent is None:
            return
        parent.removeChild(item)
        self._tree.setCurrentItem(parent)
        self._sync_from_tree()
        self._build_config_panel()

    # -- toolbar actions -----------------------------------------------------

    def _save_macro(self) -> None:
        if self._macro is None:
            self.statusBar().showMessage("Nothing to save.")
            return
        path = self._path
        if path is None:
            chosen, _filter = QFileDialog.getSaveFileName(
                self,
                "Save macro",
                str(self._macro_dir / f"{self._macro.name}.yaml"),
                "Macros (*.yaml *.yml)",
            )
            if not chosen:
                return
            path = Path(chosen)
        path.parent.mkdir(parents=True, exist_ok=True)
        # the macro name follows its file stem (schema pattern enforced)
        macro = self._macro.model_copy(update={"name": self._stem_name(path)})
        path.write_text(macro_to_yaml_text(macro), encoding="utf-8")
        self._macro = macro
        self._path = path
        self._reload_macro_combo()
        # keep the just-saved file selected (and open it)
        self._macro_combo.blockSignals(True)
        for i in range(self._macro_combo.count()):
            if self._macro_combo.itemData(i) == str(path):
                self._macro_combo.setCurrentIndex(i)
        self._macro_combo.blockSignals(False)
        self.statusBar().showMessage(f"saved {path}")

    def _test_run(self) -> None:
        if self._macro is None:
            self.statusBar().showMessage("Nothing to run.")
            return
        try:
            MacroOrchestrator(sink=NullSink(), dry_run=True).run(self._macro)
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            self.statusBar().showMessage(f"{self._macro.name}: error: {exc!r}")
            return
        self.statusBar().showMessage(
            f"{self._macro.name}: dry run ok ({len(self._macro.steps)} steps)"
        )

    def _open_yaml_editor(self) -> None:
        if self._macro is None:
            return
        editor = MacroEditorDialog(
            macro=self._macro, path=self._path, default_dir=self._macro_dir, parent=self
        )
        editor.exec()
        if self._path is not None:
            try:
                self._set_macro(load_macro(self._path))
            except LoadError as exc:
                self.statusBar().showMessage(f"could not reload {self._path.name}: {exc}")

    def _open_devices(self) -> None:
        dialog = DevicesDialog(device_provider=self._device_provider, parent=self)
        dialog.exec()

    def _open_region_picker(self) -> None:
        """Grab one real-screen frame and pick a region on the fullscreen overlay."""
        picker = RegionPickerDialog(
            frame_loader=lambda: grab_first_frame().image, parent=self
        )
        picker.exec()
        if picker.region is not None:
            x, y, w, h = picker.region
            QApplication.clipboard().setText(f"{x},{y},{w},{h}")
            self.statusBar().showMessage(
                f"Region {x},{y},{w},{h} copied (analyze --region / watch rules)."
            )
        else:
            self.statusBar().showMessage("Region picking cancelled.")

    # -- tray ----------------------------------------------------------------

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray: QSystemTrayIcon | None = None
            return
        style = self.style()
        tray = QSystemTrayIcon(style.standardIcon(style.StandardPixmap.SP_ComputerIcon))
        menu = QMenu()
        show_action = QAction("Show window", menu)
        show_action.triggered.connect(self.showNormal)
        quit_action = QAction("Quit", menu)
        app = QApplication.instance()
        if app is not None:
            quit_action.triggered.connect(app.quit)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.setToolTip("input-automation")
        tray.show()
        self._tray = tray


def run() -> None:
    """Create the application, show the tile editor, enter the event loop."""
    app = QApplication.instance() or QApplication([])
    window = TileEditorWindow()
    window.show()
    app.exec()
