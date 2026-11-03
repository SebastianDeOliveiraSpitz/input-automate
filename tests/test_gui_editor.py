"""Tests for the macro editor (T15). Runs with QT_QPA_PLATFORM=offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import yaml  # noqa: E402
from PySide6.QtWidgets import QFileDialog  # noqa: E402

from input_automation.gui.editor import (  # noqa: E402
    MacroEditorDialog,
    new_macro_template,
)
from input_automation.macros.loader import load_macro, macro_to_yaml_text
from input_automation.macros.models import (  # noqa: E402
    Action,
    Condition,
    IfStep,
    Macro,
    SetStep,
    StepKind,
    Trigger,
    TriggerKind,
)

MACRO = Macro(
    name="demo",
    description="test macro",
    triggers=[Trigger(kind=TriggerKind.KEY, code="F5")],
    steps=[
        Action(kind=StepKind.ACTION, op="tap", target="a"),
        Action(kind=StepKind.ACTION, op="wait", ms=120),
    ],
)

COMPLEX = Macro(
    name="complex",
    description="has conditional steps",
    triggers=[Trigger(kind=TriggerKind.KEY, code="F6")],
    steps=[
        SetStep(kind=StepKind.SET, var="hp", value=10),
        IfStep(
            kind=StepKind.IF,
            condition=Condition(var="hp", less_than=5),
            then=[Action(kind=StepKind.ACTION, op="tap", target="h")],
        ),
    ],
)


class TestFormEditing:
    def test_fields_populate_from_macro(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        assert editor._name.text() == "demo"
        assert editor._description.text() == "test macro"
        assert editor._trigger_code.text() == "F5"
        assert editor._steps.count() == 2
        assert "tap" in editor._steps.item(0).text()

    def test_name_edit_regenerates_yaml(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        editor._name.setText("renamed")
        editor._apply_form_to_macro()
        data = yaml.safe_load(editor._yaml_edit.toPlainText())
        assert data["name"] == "renamed"
        # round-trips through the schema
        assert Macro.model_validate(data).name == "renamed"

    def test_step_add_and_remove(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        # bypass the modal dialog: insert a step directly through the API
        actions = editor._steps_as_actions()
        assert actions is not None
        actions.append(Action(kind=StepKind.ACTION, op="click", button=3))
        editor._steps_update(actions)
        assert editor._steps.count() == 3
        editor._steps.setCurrentRow(2)
        editor._step_remove()
        assert editor._steps.count() == 2

    def test_step_move(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        editor._steps.setCurrentRow(1)
        editor._step_move(-1)
        assert "wait" in editor._steps.item(0).text()
        assert "tap" in editor._steps.item(1).text()

    def test_cannot_remove_last_step(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(
            macro=Macro(
                name="single",
                description="",
                triggers=[Trigger(kind=TriggerKind.KEY, code="F7")],
                steps=[Action(kind=StepKind.ACTION, op="wait", ms=50)],
            )
        )
        qtbot.addWidget(editor)
        editor._steps.setCurrentRow(0)
        editor._step_remove()
        assert "at least one step" in editor._status.text()

    def test_complex_macro_locks_step_form(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=COMPLEX)
        qtbot.addWidget(editor)
        assert not editor._add.isEnabled()
        assert "YAML pane" in editor._complex_hint.text()

    def test_reload_from_yaml(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        editor._yaml_edit.setPlainText(
            macro_to_yaml_text(
                MACRO.model_copy(update={"name": "from-yaml", "description": "x"})
            )
        )
        editor._reload_from_yaml()
        assert editor._name.text() == "from-yaml"

    def test_reload_reports_invalid_yaml(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        editor._yaml_edit.setPlainText("name: [not a mapping")
        editor._reload_from_yaml()
        assert "invalid YAML" in editor._status.text()

    def test_template_is_valid(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=new_macro_template())
        qtbot.addWidget(editor)
        Macro.model_validate(yaml.safe_load(editor._yaml_edit.toPlainText()))


class TestSavingAndDryRun:
    def test_save_writes_file(self, qtbot, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)  # path=None normally asks; patch it
        qtbot.addWidget(editor)
        target = tmp_path / "saved.yaml"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **kw: (str(target), "")
        )
        editor._save()
        assert editor.result() == editor.DialogCode.Accepted
        saved = load_macro(target)
        assert saved.name == "demo"

    def test_save_cancelled_keeps_dialog(self, qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: ("", ""))
        editor._save()
        assert editor.result() == editor.DialogCode.Rejected
        assert "saved" not in editor._status.text()

    def test_save_with_path(self, qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        target = tmp_path / "direct.yaml"
        editor = MacroEditorDialog(macro=MACRO, path=target)
        qtbot.addWidget(editor)
        editor._save()
        assert load_macro(target).name == "demo"

    def test_dry_run_ok_and_error_paths(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        editor = MacroEditorDialog(macro=MACRO)
        qtbot.addWidget(editor)
        editor._dry_run()
        assert "dry run ok" in editor._status.text()
        editor._yaml_edit.setPlainText(
            macro_to_yaml_text(MACRO).replace("target: a", "target: '!!'x'")
        )
        editor._dry_run()
        assert "invalid YAML" in editor._status.text()
