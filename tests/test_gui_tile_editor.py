"""Tests for the tile editor main window (T17). Runs with QT_QPA_PLATFORM=offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import yaml  # noqa: E402
from PySide6.QtWidgets import QFileDialog  # noqa: E402

from input_automation.devices.discovery import DeviceInfo  # noqa: E402
from input_automation.gui.tile_editor import (  # noqa: E402
    DevicesDialog,
    TileEditorWindow,
    _tile_data,
)
from input_automation.macros.loader import load_macro  # noqa: E402
from input_automation.macros.models import (  # noqa: E402
    Action,
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
        SetStep(kind=StepKind.SET, var="hp", value=10),
        Action(kind=StepKind.ACTION, op="tap", target="a"),
        IfStep.model_validate(
            {
                "kind": "if",
                "condition": {"var": "hp", "less_than": 5},
                "then": [Action(kind=StepKind.ACTION, op="wait", ms=50)],
                "else": [],
            }
        ),
    ],
)


@pytest.fixture
def editor(qtbot, tmp_path: Path):  # type: ignore[no-untyped-def]
    macro_file = tmp_path / "demo.yaml"
    macro_file.write_text(
        "name: demo\ndescription: test macro\n"
        "triggers:\n- kind: key\n  code: F5\n"
        "steps:\n- set: {var: hp, value: 10}\n"
        "- action: {op: tap, target: a}\n"
        "- if:\n    condition: {var: hp, less_than: 5}\n"
        "    then:\n    - action: {op: wait, ms: 50}\n"
        "    else: []\n"
    )
    win = TileEditorWindow(
        device_provider=lambda: [],  # no devices needed offscreen
        macro_provider=lambda directory: [(macro_file, MACRO.model_copy(deep=True))],
        macro_dir=tmp_path,
    )
    qtbot.addWidget(win)
    return win, macro_file, tmp_path


def _steps_root(editor: TileEditorWindow):  # type: ignore[no-untyped-def]
    return editor._tree.topLevelItem(1)


def _triggers_root(editor: TileEditorWindow):  # type: ignore[no-untyped-def]
    return editor._tree.topLevelItem(0)


class TestWindowLayout:
    def test_opens_on_tile_tree_with_sections(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        assert win.windowTitle() == "input-automation"
        assert _triggers_root(win).text(0) == "Triggers"
        assert _steps_root(win).text(0) == "Steps"
        # the macro loaded from the provider is already in the tree
        assert _steps_root(win).childCount() == 3
        assert _triggers_root(win).childCount() == 1

    def test_palette_contains_all_groups(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        labels = [win._palette.item(i).text() for i in range(win._palette.count())]
        assert any("On key press" in label for label in labels)
        assert any("If condition" in label for label in labels)
        assert any("Wait ms" in label for label in labels)
        assert any("Tap key" in label for label in labels)

    def test_devices_dialog_lists_provider_output(self, qtbot, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        dialog = DevicesDialog(device_provider=lambda: [], parent=win)
        qtbot.addWidget(dialog)
        assert "No input devices found." in dialog._list.item(0).text()


class TestInsertion:
    def test_double_click_palette_appends_step(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        before = _steps_root(win).childCount()
        win._insert_tile("wait", None)
        assert _steps_root(win).childCount() == before + 1
        assert "wait" in _steps_root(win).child(before).text(0)
        data = yaml.safe_load(win._yaml_view.toPlainText())
        assert data["steps"][-1]["kind"] == "action"
        assert data["steps"][-1]["op"] == "wait"

    def test_trigger_tile_goes_to_trigger_section(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        win._insert_tile("trigger", None)
        assert _triggers_root(win).childCount() == 2
        data = yaml.safe_load(win._yaml_view.toPlainText())
        assert data["triggers"][1]["code"] == "F10"
        # tagged form: triggers are plain mappings
        assert data["triggers"][1]["kind"] == "key"

    def test_if_tile_creates_branch_rows_with_default_child(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        win._insert_tile("if", None)
        row = _steps_root(win).child(_steps_root(win).childCount() - 1)
        texts = [row.child(i).text(0) for i in range(row.childCount())]
        assert any("then" in t for t in texts)
        assert any("else" in t for t in texts)
        then_branch = row.child(0)
        assert then_branch.childCount() == 1  # default wait step
        macro = win._macro
        assert macro is not None
        last = macro.steps[-1]
        assert isinstance(last, IfStep) and last.then and last.else_ == []

    def test_insert_after_selected_step(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        win._tree.setCurrentItem(_steps_root(win).child(0))
        win._insert_tile("click", None)
        assert "click" in _steps_root(win).child(1).text(0)

    def test_invalid_mutation_reverts_tree(self, editor, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        before = _steps_root(win).childCount()

        def broken() -> Macro:
            raise ValueError("broken")

        monkeypatch.setattr(win, "_macro_from_tree", broken)
        win._sync_from_tree()
        assert "reverted" in win.statusBar().currentMessage()
        # tree rebuilt from the last good macro
        assert _steps_root(win).childCount() == before


class TestConfigPanel:
    def test_trigger_config_updates_macro_and_yaml(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        trigger_row = _triggers_root(win).child(0)
        win._tree.setCurrentItem(trigger_row)
        win._update_tile(trigger_row, code="F9")
        data = yaml.safe_load(win._yaml_view.toPlainText())
        assert data["triggers"][0]["code"] == "F9"
        assert "F9" in trigger_row.text(0)

    def test_action_config_updates_macro_and_yaml(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        row = _steps_root(win).child(1)  # tap
        win._tree.setCurrentItem(row)
        win._update_tile(row, target="Q")
        data = yaml.safe_load(win._yaml_view.toPlainText())
        assert data["steps"][1]["target"] == "Q"

    def test_condition_config_updates_macro_and_yaml(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        if_row = _steps_root(win).child(2)
        win._tree.setCurrentItem(if_row)
        win._update_condition(if_row, value=7)
        data = yaml.safe_load(win._yaml_view.toPlainText())
        assert data["steps"][2]["kind"] == "if"
        assert data["steps"][2]["condition"] == {"var": "hp", "less_than": 7}

    def test_remove_tile(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        win._remove_tile(_steps_root(win).child(2))  # the if row (with branches)
        assert _steps_root(win).childCount() == 2
        data = yaml.safe_load(win._yaml_view.toPlainText())
        assert data["steps"][0]["kind"] == "set"
        assert data["steps"][1]["kind"] == "action"


class TestPersistence:
    def test_save_writes_file(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, macro_file, _dir = editor
        win._save_macro()
        assert "saved" in win.statusBar().currentMessage()
        saved = load_macro(macro_file)
        assert saved.name == "demo"

    def test_save_new_macro_asks_for_path(self, qtbot, editor, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        win, _file, tmp_path = editor
        win._new_macro()
        target = tmp_path / "fresh.yaml"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **kw: (str(target), "")
        )
        win._save_macro()
        assert target.exists()
        assert load_macro(target).name == "fresh"

    def test_test_run_dry(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        win._test_run()
        assert "dry run ok" in win.statusBar().currentMessage()

    def test_round_trip_if_macro(self, editor) -> None:  # type: ignore[no-untyped-def]
        """The tree round-trips a full if/else macro without loss."""
        win, _file, _dir = editor
        rebuilt = win._macro_from_tree()
        assert len(rebuilt.steps) == 3
        if_step = rebuilt.steps[2]
        assert isinstance(if_step, IfStep)
        assert if_step.condition.less_than == 5
        assert if_step.then and if_step.then[0].op == "wait"

    def test_tile_templates_are_independent(self) -> None:
        """Two tiles from the same palette entry share no mutable state."""
        a, b = _tile_data("if"), _tile_data("if")
        assert a is not b and a["condition"] is not b["condition"]
        a["condition"]["value"] = 99
        assert b["condition"]["value"] == 10


class TestFedoraFeedbackRegressions:
    """Regressions from real fedora1 GUI testing (2026-11-03)."""

    def test_device_choices_show_names(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        win._device_provider = lambda: [
            DeviceInfo(
                path="/dev/input/event3",
                name="AT Keyboard",
                phys="",
                kinds=frozenset({"keyboard"}),
            )
        ]
        choices = win._device_choices()
        assert choices[0] == ("any", "any device")
        # display carries the NAME, stored value stays the path
        assert choices[1] == ("/dev/input/event3", "AT Keyboard (/dev/input/event3)")

    def test_trigger_edit_on_empty_steps_keeps_macro(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, _file, _dir = editor
        win._new_macro()
        triggers_root = _triggers_root(win)
        win._insert_tile("trigger", None)
        row = triggers_root.child(triggers_root.childCount() - 1)
        win._tree.setCurrentItem(row)
        win._update_tile(row, device="/dev/input/event3", code="F2")
        macro = win._macro
        assert macro is not None
        assert macro.triggers[-1].code == "F2"
        assert macro.triggers[-1].device == "/dev/input/event3"
        # the template's wait step is kept — no revert, no wipe
        assert len(macro.steps) == 1 and macro.steps[0].op == "wait"

    def test_stale_config_edit_is_ignored_not_crash(self, editor) -> None:  # type: ignore[no-untyped-def]
        win, macro_file, _dir = editor
        win._new_macro()
        triggers_root = _triggers_root(win)
        win._insert_tile("trigger", None)
        row = triggers_root.child(triggers_root.childCount() - 1)
        win._tree.setCurrentItem(row)
        win._update_tile(row, code="F2")
        old_row = row
        win._path = macro_file
        win._save_macro()  # rebuilds the tree: old_row's C++ object is deleted
        # the still-open config panel fires against the deleted row —
        # previously RuntimeError: libshiboken Internal C++ object deleted
        win._update_tile(old_row, code="F3")
        assert win._macro is not None
        assert win._macro.triggers[-1].code == "F2"  # untouched by stale edit
