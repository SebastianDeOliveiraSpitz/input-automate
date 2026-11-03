"""Tests for the `record` CLI command (T13 slice C)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from input_automation.cli import app
from input_automation.macros.loader import load_macro

runner = CliRunner()

EVENTS = [
    {"ts": 1.0, "path": "/dev/input/event0", "type": "key", "code": 30,
     "code_name": "KEY_A", "value": 1, "action": "press"},
    {"ts": 1.02, "path": "/dev/input/event0", "type": "key", "code": 30,
     "code_name": "KEY_A", "value": 0, "action": "release"},
    {"ts": 1.5, "path": "/dev/input/event0", "type": "key", "code": 48,
     "code_name": "KEY_B", "value": 1, "action": "press"},
    {"ts": 1.51, "path": "/dev/input/event0", "type": "key", "code": 48,
     "code_name": "KEY_B", "value": 0, "action": "release"},
]


def _write_events(path: Path) -> None:
    path.write_text("\n".join(json.dumps(e) for e in EVENTS))


class TestRecordCommand:
    def test_records_events_into_yaml(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        _write_events(events)
        out = tmp_path / "recorded.yaml"
        result = runner.invoke(
            app, ["record", "--from-events", str(events), "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        macro = load_macro(out)
        assert macro.name == "recorded"  # defaults to the output stem
        # tap A, wait 480, tap B
        ops = [s.op for s in macro.steps]  # type: ignore[union-attr]
        assert ops == ["tap", "wait", "tap"]

    def test_custom_trigger(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        _write_events(events)
        out = tmp_path / "rec.yaml"
        result = runner.invoke(
            app,
            ["record", "--from-events", str(events), "--output", str(out),
             "--trigger", "F5"],
        )
        assert result.exit_code == 0, result.output
        macro = load_macro(out)
        assert macro.triggers[0].code == "F5"

    def test_custom_speed(self, tmp_path: Path) -> None:
        events = tmp_path / "events.jsonl"
        _write_events(events)
        out = tmp_path / "rec.yaml"
        result = runner.invoke(
            app,
            ["record", "--from-events", str(events), "--output", str(out),
             "--speed", "2.0"],
        )
        assert result.exit_code == 0, result.output
        macro = load_macro(out)
        waits = [s.ms for s in macro.steps if s.op == "wait"]  # type: ignore[union-attr]
        assert waits == [240]  # 480ms raw gap / 2.0 speed

    def test_missing_events_file_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "rec.yaml"
        result = runner.invoke(
            app,
            ["record", "--from-events", str(tmp_path / "nope.jsonl"), "--output", str(out)],
        )
        assert result.exit_code != 0
