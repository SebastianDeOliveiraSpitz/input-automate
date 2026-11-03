"""Macro editor dialog: YAML <-> form, region picking, dry-run (T15).

Two-way editing with the YAML text as the source of truth:

- Form edits regenerate the YAML text immediately (name, description,
  first trigger, and the *action* steps).
- Manual YAML edits re-enter the form via "Reload from YAML".
- Macros that contain complex steps (if/while/set) keep the step form
  read-only — regenerating YAML from the form would silently drop them;
  those macros are edited through the YAML pane.

Saving validates the current YAML against the schema before writing.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from input_automation.capture.factory import grab_first_frame
from input_automation.gui.region_picker import RegionPickerDialog
from input_automation.macros.loader import macro_to_yaml_text
from input_automation.macros.models import (
    Action,
    Macro,
    Step,
    StepKind,
    Trigger,
    TriggerKind,
)
from input_automation.macros.orchestrator import MacroOrchestrator, NullSink

__all__ = ["MacroEditorDialog", "new_macro_template"]

_ACTIONS = ("tap", "press", "release", "click", "move", "scroll", "wait")


def new_macro_template() -> Macro:
    """A minimal valid macro to start a new file from."""
    return Macro(
        name="new-macro",
        description="",
        triggers=[Trigger(kind=TriggerKind.KEY, code="F10")],
        steps=[Action(kind=StepKind.ACTION, op="wait", ms=100)],
    )


def _action_summary(step: Step) -> str:
    """One-line description of an action step for the list widget."""
    if not isinstance(step, Action):
        return f"{step.kind.value} step (edit via YAML)"
    fields = []
    if step.target is not None:
        fields.append(f"target={step.target}")
    if step.button is not None:
        fields.append(f"button={step.button}")
    if step.dx is not None:
        fields.append(f"dx={step.dx}")
    if step.dy is not None:
        fields.append(f"dy={step.dy}")
    if step.ms:
        fields.append(f"ms={step.ms}")
    return f"{step.op} " + " ".join(fields)


class StepDialog(QDialog):
    """Edit one action step's fields (op-dependent)."""

    def __init__(
        self,
        step: Action | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit step" if step is not None else "Add step")
        self.step: Action | None = None

        form = QFormLayout(self)
        self._op = QComboBox()
        self._op.addItems(_ACTIONS)
        self._op.currentTextChanged.connect(self._refresh_fields)
        form.addRow("op", self._op)
        self._target = QLineEdit()
        self._button = QComboBox()
        self._button.addItems(("1", "2", "3"))  # left, middle, right
        self._dx = QLineEdit()
        self._dy = QLineEdit()
        self._ms = QLineEdit()
        for label, widget in (
            ("target", self._target),
            ("button", self._button),
            ("dx", self._dx),
            ("dy", self._dy),
            ("ms", self._ms),
        ):
            form.addRow(label, widget)
        ok = QPushButton("OK")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self._confirm)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        holder = QWidget()
        holder.setLayout(buttons)
        form.addRow(holder)

        if step is not None:
            self._load(step)
        self._refresh_fields()

    def _load(self, step: Action) -> None:
        self._op.setCurrentText(step.op)
        self._target.setText(step.target or "")
        if step.button is not None:
            self._button.setCurrentText(str(step.button))
        self._dx.setText("" if step.dx is None else str(step.dx))
        self._dy.setText("" if step.dy is None else str(step.dy))
        self._ms.setText("" if step.ms == 0 else str(step.ms))

    def _refresh_fields(self) -> None:
        """Show only the fields the selected op uses."""
        op = self._op.currentText()
        needs = {
            "tap": ("target",),
            "press": ("target",),
            "release": ("target",),
            "click": ("button",),
            "move": ("dx", "dy"),
            "scroll": ("dx", "dy"),
            "wait": ("ms",),
        }.get(op, ())
        for label, widget in (
            ("target", self._target),
            ("button", self._button),
            ("dx", self._dx),
            ("dy", self._dy),
            ("ms", self._ms),
        ):
            widget.setVisible(label in needs)

    def _confirm(self) -> None:
        def text(widget: QLineEdit) -> str:
            return widget.text().strip()

        op = self._op.currentText()
        try:
            kwargs: dict[str, object] = {"op": op}
            if op in ("tap", "press", "release"):
                kwargs["target"] = text(self._target)
            elif op == "click":
                kwargs["button"] = int(self._button.currentText())
            elif op in ("move", "scroll"):
                if text(self._dx):
                    kwargs["dx"] = int(text(self._dx))
                if text(self._dy):
                    kwargs["dy"] = int(text(self._dy))
            elif op == "wait":
                kwargs["ms"] = int(text(self._ms) or "0")
            self.step = Action(kind=StepKind.ACTION, **kwargs)  # type: ignore[arg-type]
        except (ValueError, ValidationError) as exc:
            QMessageBox.warning(self, "Invalid step", str(exc))
            return
        self.accept()


class MacroEditorDialog(QDialog):
    """Edit a macro as YAML and/or form; save validated to disk."""

    def __init__(
        self,
        *,
        macro: Macro | None = None,
        path: Path | None = None,
        default_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Macro editor")
        self.resize(900, 600)
        self._path = path
        self._default_dir = default_dir or Path("macros")
        macro = macro or new_macro_template()
        self._yaml_text = macro_to_yaml_text(macro)

        layout = QVBoxLayout(self)
        self._status = QLabel("")
        layout.addWidget(self._status)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)

        # left: YAML pane (source of truth; the form follows only via reload)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._yaml_edit = QPlainTextEdit()
        left_layout.addWidget(self._yaml_edit)
        reload_btn = QPushButton("Reload form from YAML")
        reload_btn.clicked.connect(self._reload_from_yaml)
        left_layout.addWidget(reload_btn)
        split.addWidget(left)

        # right: form pane
        right = QWidget()
        form_layout = QFormLayout(right)
        self._name = QLineEdit(macro.name)
        self._description = QLineEdit(macro.description)
        self._trigger_code = QLineEdit(macro.triggers[0].code)
        self._trigger_kind = QComboBox()
        self._trigger_kind.addItems(("key", "button"))
        self._trigger_kind.setCurrentText(macro.triggers[0].kind)
        self._trigger_state = QComboBox()
        self._trigger_state.addItems(("down", "up"))
        self._trigger_state.setCurrentText(macro.triggers[0].state)
        form_layout.addRow("name", self._name)
        form_layout.addRow("description", self._description)
        form_layout.addRow("trigger kind", self._trigger_kind)
        form_layout.addRow("trigger code", self._trigger_code)
        form_layout.addRow("trigger state", self._trigger_state)
        self._steps = QListWidget()
        form_layout.addRow("steps", self._steps)
        step_buttons = QHBoxLayout()
        self._add = QPushButton("Add")
        self._edit = QPushButton("Edit")
        self._remove = QPushButton("Remove")
        self._up = QPushButton("Up")
        self._down = QPushButton("Down")
        for btn in (self._add, self._edit, self._remove, self._up, self._down):
            step_buttons.addWidget(btn)
        holder = QWidget()
        holder.setLayout(step_buttons)
        form_layout.addRow(holder)
        self._complex_hint = QLabel("")
        form_layout.addRow(self._complex_hint)
        split.addWidget(right)

        # bottom buttons
        bottom = QHBoxLayout()
        self._test_run = QPushButton("Test run (dry)")
        self._pick_region = QPushButton("Pick region")
        save = QPushButton("Save")
        cancel = QPushButton("Cancel")
        save.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)
        self._test_run.clicked.connect(self._dry_run)
        self._pick_region.clicked.connect(self._pick_region_clicked)
        for btn in (self._add, self._edit, self._remove, self._up, self._down):
            self._connect_step_buttons(btn)
        bottom.addWidget(self._test_run)
        bottom.addWidget(self._pick_region)
        bottom.addStretch(1)
        bottom.addWidget(save)
        bottom.addWidget(cancel)
        layout.addLayout(bottom)

        self._macro = macro
        self._sync_form_from_macro()
        self._yaml_edit.setPlainText(self._yaml_text)

    # -- YAML pane -----------------------------------------------------------

    def _reload_from_yaml(self) -> None:
        """Parse the YAML pane back into the form (two-way entry point)."""
        text = self._yaml_edit.toPlainText()
        try:
            data = yaml.safe_load(text)
            macro = Macro.model_validate(data)
        except (yaml.YAMLError, ValidationError) as exc:
            self._status.setText(f"invalid YAML: {exc}")
            return
        self._macro = macro
        self._sync_form_from_macro()
        self._status.setText("Form reloaded from YAML.")

    # -- form ----------------------------------------------------------------

    def _sync_form_from_macro(self) -> None:
        """Fill all form widgets from ``self._macro``."""
        macro = self._macro
        assert macro is not None  # noqa: S101 - set in __init__/reload
        self._name.setText(macro.name)
        self._description.setText(macro.description)
        self._trigger_code.setText(macro.triggers[0].code)
        self._trigger_kind.setCurrentText(macro.triggers[0].kind)
        self._trigger_state.setCurrentText(macro.triggers[0].state)
        self._steps.clear()
        for step in macro.steps:
            self._steps.addItem(QListWidgetItem(_action_summary(step)))
        complex_steps = any(not isinstance(s, Action) for s in macro.steps)
        for widget in (
            self._add,
            self._edit,
            self._remove,
            self._up,
            self._down,
        ):
            widget.setEnabled(not complex_steps)
        self._complex_hint.setText(
            "contains if/while/set steps — edit them via the YAML pane"
            if complex_steps
            else ""
        )

    def _connect_step_buttons(self, btn: QPushButton) -> None:
        name = btn.text()
        if name == "Add":
            btn.clicked.connect(self._step_add)
        elif name == "Edit":
            btn.clicked.connect(self._step_edit)
        elif name == "Remove":
            btn.clicked.connect(self._step_remove)
        elif name == "Up":
            btn.clicked.connect(lambda: self._step_move(-1))
        else:
            btn.clicked.connect(lambda: self._step_move(1))

    def _apply_form_to_macro(self) -> None:
        """Push trigger/name/description form edits into ``self._macro``."""
        assert self._macro is not None  # noqa: S101
        self._macro = self._macro.model_copy(
            update={
                "name": self._name.text().strip(),
                "description": self._description.text(),
                "triggers": [
                    Trigger(
                        kind=TriggerKind(self._trigger_kind.currentText()),
                        code=self._trigger_code.text().strip(),
                        state=(
                            "down"
                            if self._trigger_state.currentText() == "down"
                            else "up"
                        ),
                    )
                ],
            }
        )
        self._yaml_text = macro_to_yaml_text(self._macro)
        self._yaml_edit.setPlainText(self._yaml_text)

    def _steps_as_actions(self) -> list[Action] | None:
        """Current steps if all are Actions, else None."""
        assert self._macro is not None  # noqa: S101
        if any(not isinstance(s, Action) for s in self._macro.steps):
            return None
        return [s for s in self._macro.steps if isinstance(s, Action)]

    def _steps_update(self, actions: list[Action]) -> None:
        assert self._macro is not None  # noqa: S101
        self._macro = self._macro.model_copy(
            update={"steps": [s for s in actions]}
        )
        self._sync_form_from_macro()
        self._apply_form_to_macro()

    def _step_add(self) -> None:
        dialog = StepDialog(parent=self)
        if dialog.exec():
            assert dialog.step is not None  # noqa: S101 - set on accept
            actions = self._steps_as_actions() or []
            actions.append(dialog.step)
            self._steps_update(actions)

    def _step_edit(self) -> None:
        row = self._steps.currentRow()
        actions = self._steps_as_actions()
        if row < 0 or actions is None:
            return
        dialog = StepDialog(step=actions[row], parent=self)
        if dialog.exec():
            assert dialog.step is not None  # noqa: S101
            actions[row] = dialog.step
            self._steps_update(actions)

    def _step_remove(self) -> None:
        row = self._steps.currentRow()
        actions = self._steps_as_actions()
        if row < 0 or actions is None or len(actions) <= 1:
            if actions is not None and len(actions) <= 1:
                self._status.setText("A macro needs at least one step.")
            return
        del actions[row]
        self._steps_update(actions)

    def _step_move(self, delta: int) -> None:
        row = self._steps.currentRow()
        actions = self._steps_as_actions()
        if row < 0 or actions is None:
            return
        target = row + delta
        if not 0 <= target < len(actions):
            return
        actions[row], actions[target] = actions[target], actions[row]
        self._steps_update(actions)
        self._steps.setCurrentRow(target)

    # -- actions -------------------------------------------------------------

    def _pick_region_clicked(self) -> None:
        """Grab one real-screen frame and pick a region on the fullscreen overlay."""
        picker = RegionPickerDialog(
            frame_loader=lambda: grab_first_frame().image, parent=self
        )
        result = picker.exec()
        if result and picker.region is not None:
            x, y, w, h = picker.region
            QApplication.clipboard().setText(f"{x},{y},{w},{h}")
            self._status.setText(
                f"Region {x},{y},{w},{h} copied to clipboard "
                "(use with `analyze --region` or a watch rule)."
            )

    def _current_macro(self) -> Macro:
        """Validate the YAML pane; raises ValidationError/YAMLError on error."""
        data = yaml.safe_load(self._yaml_edit.toPlainText())
        return Macro.model_validate(data)

    def _dry_run(self) -> None:
        """Execute the YAML pane's macro without injecting anything."""
        try:
            macro = self._current_macro()
        except (yaml.YAMLError, ValidationError) as exc:
            self._status.setText(f"invalid YAML: {exc}")
            return
        try:
            MacroOrchestrator(sink=NullSink(), dry_run=True).run(macro)
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            self._status.setText(f"{macro.name}: error: {exc!r}")
            return
        self._status.setText(
            f"{macro.name}: dry run ok ({len(macro.steps)} steps)"
        )

    def _save(self) -> None:
        """Validate the YAML pane and write it to disk (asks when pathless)."""
        try:
            macro = self._current_macro()
        except (yaml.YAMLError, ValidationError) as exc:
            self._status.setText(f"invalid YAML: {exc}")
            return
        path = self._path
        if path is None:
            chosen, _filter = QFileDialog.getSaveFileName(
                self,
                "Save macro",
                str(self._default_dir / f"{macro.name}.yaml"),
                "Macros (*.yaml *.yml)",
            )
            if not chosen:
                return  # keep the dialog open, nothing written
            path = Path(chosen)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(macro_to_yaml_text(macro), encoding="utf-8")
        self._path = path
        self._macro = macro
        self.saved_path = path
        self._status.setText(f"saved {path}")
        self.accept()

    def result_macro(self) -> Macro:
        """The macro currently in the editor (last saved or form state)."""
        assert self._macro is not None  # noqa: S101 - set in __init__
        return self._macro
