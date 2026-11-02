"""Tests for the `capture` CLI command (T13 slice A)."""

import json

from typer.testing import CliRunner

from input_automation.cli import app

runner = CliRunner()


class TestCaptureCommand:
    def test_mock_backend_writes_png(self, tmp_path) -> None:
        out = tmp_path / "frame.png"
        result = runner.invoke(app, ["capture", "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists() and out.stat().st_size > 0

    def test_json_output_prints_metadata(self, tmp_path) -> None:
        out = tmp_path / "frame.png"
        result = runner.invoke(
            app, ["capture", "--output", str(out), "--json"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["path"] == str(out)
        assert data["width"] > 0 and data["height"] > 0
        assert out.exists()

    def test_unknown_backend_fails_with_choices(self, tmp_path) -> None:
        out = tmp_path / "frame.png"
        result = runner.invoke(
            app, ["capture", "--backend", "x11grab", "--output", str(out)]
        )
        assert result.exit_code != 0
        assert "mock" in result.output and "mutter" in result.output

    def test_screenshot_alias_still_works(self, tmp_path) -> None:
        out = tmp_path / "frame.png"
        result = runner.invoke(app, ["screenshot", "--output", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()
