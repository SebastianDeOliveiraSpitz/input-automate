"""Tests for the `macros` CLI group (T13 slice B): list, validate, run."""

import json
from pathlib import Path

from typer.testing import CliRunner

from input_automation.cli import app

runner = CliRunner()

VALID = """
name: valid-macro
description: test macro
triggers:
  - kind: key
    code: F10
steps:
  - action: { op: tap, target: a }
"""

INVALID = """
name: invalid-macro
triggers: []
steps: []
"""


class TestMacrosList:
    def test_lists_yaml_and_python_macros(self, tmp_path: Path) -> None:
        (tmp_path / "a.yaml").write_text(VALID)
        (tmp_path / "b.py").write_text(
            "from input_automation.macros.script import MacroBuilder\n"
            "def build(m):\n"
            "    m.key_trigger('F10')\n"
            "    m.tap('a')\n"
        )
        result = runner.invoke(app, ["macros", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "a.yaml" in result.output and "b.py" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        (tmp_path / "a.yaml").write_text(VALID)
        result = runner.invoke(app, ["macros", "list", str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list) and data[0]["name"] == "valid-macro"

    def test_skips_invalid_with_warning(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text(INVALID)
        (tmp_path / "good.yaml").write_text(VALID)
        result = runner.invoke(app, ["macros", "list", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "bad.yaml" in result.output  # warning mentions the file
        assert "good.yaml" in result.output


class TestMacrosValidate:
    def test_valid_macro_passes(self, tmp_path: Path) -> None:
        path = tmp_path / "v.yaml"
        path.write_text(VALID)
        result = runner.invoke(app, ["macros", "validate", str(path)])
        assert result.exit_code == 0, result.output
        assert "ok" in result.output.lower()

    def test_invalid_macro_fails_with_reason(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text(INVALID)
        result = runner.invoke(app, ["macros", "validate", str(path)])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()


class TestMacrosRun:
    def test_run_dry_run_does_not_inject(self, tmp_path: Path) -> None:
        path = tmp_path / "v.yaml"
        path.write_text(VALID)
        result = runner.invoke(
            app, ["macros", "run", str(path), "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "dry" in result.output.lower()
