"""Tests for the CLI stub."""

from typer.testing import CliRunner

from input_automation import __version__
from input_automation.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"input-automation {__version__}" in result.output
