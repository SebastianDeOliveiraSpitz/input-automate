"""Tests for the Python macro escape hatch (T11): macros as Python modules."""

from pathlib import Path

import pytest
from tests.test_macro_binding import FakeSink, key_event

from input_automation.macros.loader import (
    LoadError,
    load_macros,
    load_python_macro,
)
from input_automation.macros.runner import MacroRunner
from input_automation.triggers.engine import TriggerEngine

GOOD_MACRO = '''
"""A Python macro: fires three taps of key A on F10 down."""

from input_automation.macros.script import MacroBuilder


def build(m: MacroBuilder) -> None:
    m.description("triple tap demo")
    m.key_trigger("F10")
    for _ in range(3):
        m.tap("a")
        m.wait(20)
'''


class TestPythonMacroLoading:
    def test_valid_module_builds_macro(self, tmp_path: Path) -> None:
        """A module with build(builder) yields a validated Macro."""
        path = tmp_path / "triple-tap.py"
        path.write_text(GOOD_MACRO)
        macro = load_python_macro(path)
        assert macro.name == "triple-tap"  # defaults to the file stem
        assert macro.description == "triple tap demo"
        assert macro.triggers[0].code == "F10"
        assert len(macro.steps) == 6  # 3 x (tap, wait)

    def test_builder_name_override(self, tmp_path: Path) -> None:
        """The builder can override the default (stem) macro name."""
        path = tmp_path / "anything.py"
        path.write_text(
            "from input_automation.macros.script import MacroBuilder\n"
            "def build(m):\n"
            "    m.name = 'custom-name'\n"
            "    m.key_trigger('a')\n"
            "    m.tap('b')\n"
        )
        assert load_python_macro(path).name == "custom-name"

    def test_module_without_build_is_rejected(self, tmp_path: Path) -> None:
        """A module with no build() function is a LoadError."""
        path = tmp_path / "broken.py"
        path.write_text("value = 42\n")
        with pytest.raises(LoadError, match="build"):
            load_python_macro(path)

    def test_build_exception_is_loaderror(self, tmp_path: Path) -> None:
        """An exception inside build() becomes a LoadError with the message."""
        path = tmp_path / "explodes.py"
        path.write_text(
            "def build(m):\n"
            "    raise RuntimeError('kaboom')\n"
        )
        with pytest.raises(LoadError, match="kaboom"):
            load_python_macro(path)

    def test_invalid_macro_is_loaderror(self, tmp_path: Path) -> None:
        """Schema violations (e.g. bad macro name) surface as LoadError."""
        path = tmp_path / "Bad-Name.py"  # uppercase fails the name pattern
        path.write_text(
            "from input_automation.macros.script import MacroBuilder\n"
            "def build(m):\n"
            "    m.key_trigger('a')\n"
            "    m.tap('b')\n"
        )
        with pytest.raises(LoadError, match="invalid macro"):
            load_python_macro(path)


class TestDirectoryLoading:
    def test_load_macros_picks_up_python_files(self, tmp_path: Path) -> None:
        """load_macros yields .py macros alongside YAML ones."""
        (tmp_path / "py-macro.py").write_text(GOOD_MACRO)
        (tmp_path / "yaml-macro.yaml").write_text(
            "name: yaml-macro\n"
            "triggers:\n"
            "  - { kind: key, code: a }\n"
            "steps:\n"
            "  - action: { op: tap, target: b }\n"
        )
        loaded = {m.name for _, m in load_macros(tmp_path)}
        assert loaded == {"py-macro", "yaml-macro"}

    def test_python_macro_fires_end_to_end(self, tmp_path: Path) -> None:
        """A Python macro registered in the runner fires on its trigger."""
        path = tmp_path / "py-macro.py"
        path.write_text(GOOD_MACRO)
        sink = FakeSink()
        runner = MacroRunner(engine=TriggerEngine(), sink=sink)
        runner.register(load_python_macro(path))

        runner.handle_event(key_event(100.0, 68, "KEY_F10", 1))
        assert sink.calls.count(("key_tap", (30,))) == 3  # KEY_A = 30
