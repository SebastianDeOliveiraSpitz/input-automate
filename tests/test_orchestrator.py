"""Tests for the macro orchestrator (T10): step execution, dry-run, JSONL log."""

import json
from pathlib import Path

import pytest

from input_automation.macros.models import (
    Action,
    Condition,
    IfStep,
    Macro,
    SetStep,
    Trigger,
    WhileStep,
)
from input_automation.macros.orchestrator import MacroOrchestrator


class FakeSink:
    """Records every output call in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def key_tap(self, code: int) -> None:
        self.calls.append(("key_tap", (code,)))

    def key_press(self, code: int) -> None:
        self.calls.append(("key_press", (code,)))

    def key_release(self, code: int) -> None:
        self.calls.append(("key_release", (code,)))

    def mouse_move(self, dx: int, dy: int) -> None:
        self.calls.append(("mouse_move", (dx, dy)))

    def mouse_click(self, button: str) -> None:
        self.calls.append(("mouse_click", (button,)))

    def scroll(self, dy: int) -> None:
        self.calls.append(("scroll", (dy,)))


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make wait steps instantaneous in tests."""
    monkeypatch.setattr(
        "input_automation.macros.orchestrator.time.sleep", lambda _s: None
    )


def simple_macro(steps: list) -> Macro:
    return Macro(
        name="test-macro",
        triggers=[Trigger(kind="key", code="x", state="down")],
        steps=steps,
    )


class TestStepExecution:
    def test_sequence_of_actions_runs_in_order(self, no_sleep: None) -> None:
        sink = FakeSink()
        orch = MacroOrchestrator(sink=sink)
        macro = simple_macro(
            [
                Action(kind="action", op="tap", target="a"),
                Action(kind="action", op="wait", ms=50),
                Action(kind="action", op="click", button=1),
            ]
        )
        orch.run(macro)
        assert len(sink.calls) == 2
        assert sink.calls[0] == ("key_tap", (30,))  # KEY_A = 30 in evdev
        assert sink.calls[1] == ("mouse_click", ("left",))

    def test_press_release_produces_two_sink_calls(self, no_sleep: None) -> None:
        sink = FakeSink()
        orch = MacroOrchestrator(sink=sink)
        macro = simple_macro(
            [
                Action(kind="action", op="press", target="x"),
                Action(kind="action", op="release", target="x"),
            ]
        )
        orch.run(macro)
        assert sink.calls == [("key_press", (45,)), ("key_release", (45,))]


class TestConditionalExecution:
    def test_if_condition_true_runs_then_branch(self, no_sleep: None) -> None:
        sink = FakeSink()
        orch = MacroOrchestrator(sink=sink)
        macro = simple_macro(
            [
                SetStep(kind="set", var="count", value=2),
                IfStep(
                    kind="if",
                    condition=Condition(var="count", greater_than=1),
                    then=[Action(kind="action", op="tap", target="a")],
                ),
            ]
        )
        orch.run(macro)
        assert sink.calls == [("key_tap", (30,))]

    def test_if_condition_false_runs_else_branch(self, no_sleep: None) -> None:
        sink = FakeSink()
        orch = MacroOrchestrator(sink=sink)
        macro = simple_macro(
            [
                SetStep(kind="set", var="count", value=0),
                IfStep(
                    kind="if",
                    condition=Condition(var="count", greater_than=1),
                    then=[Action(kind="action", op="tap", target="a")],
                    **{"else": [Action(kind="action", op="tap", target="b")]},
                ),
            ]
        )
        orch.run(macro)
        assert sink.calls == [("key_tap", (48,))]  # KEY_B = 48

    def test_unknown_variable_raises_clear_error(self, no_sleep: None) -> None:
        sink = FakeSink()
        orch = MacroOrchestrator(sink=sink)
        macro = simple_macro(
            [
                IfStep(
                    kind="if",
                    condition=Condition(var="missing", equals=1),
                    then=[Action(kind="action", op="tap", target="a")],
                )
            ]
        )
        with pytest.raises(KeyError, match="missing"):
            orch.run(macro)


class TestLoopExecution:
    def test_while_loop_safety_cap(self, no_sleep: None) -> None:
        """A loop whose condition never becomes false stops at max_iterations."""
        sink = FakeSink()
        orch = MacroOrchestrator(sink=sink)
        macro = simple_macro(
            [
                SetStep(kind="set", var="n", value=1),
                WhileStep(
                    kind="while",
                    condition=Condition(var="n", greater_than=0),
                    body=[Action(kind="action", op="tap", target="a")],
                    max_iterations=5,
                ),
            ]
        )
        # NOTE: SetStep takes a literal value; arithmetic isn't expressible in
        # the schema, so this condition never turns false. The safety cap is
        # the loop's termination guarantee.
        with pytest.raises(RuntimeError, match="max_iterations"):
            orch.run(macro)
        assert len(sink.calls) == 5  # body ran exactly max_iterations times


class TestDryRun:
    def test_dry_run_executes_steps_without_sink_calls(
        self, no_sleep: None
    ) -> None:
        """dry_run=True walks the tree but never touches the output sink."""
        sink = FakeSink()
        orch = MacroOrchestrator(sink=sink, dry_run=True)
        macro = simple_macro(
            [
                Action(kind="action", op="tap", target="a"),
                Action(kind="action", op="click", button=2),
            ]
        )
        orch.run(macro)
        assert sink.calls == []


class TestJsonlLog:
    def test_run_writes_jsonl_events(self, no_sleep: None, tmp_path: Path) -> None:
        """Each run appends start/step/end events as JSON lines to the log."""
        sink = FakeSink()
        log_path = tmp_path / "logs" / "exec.jsonl"
        orch = MacroOrchestrator(sink=sink, log_path=log_path)
        macro = simple_macro(
            [
                Action(kind="action", op="tap", target="a"),
                Action(kind="action", op="wait", ms=10),
            ]
        )
        orch.run(macro)

        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert [e["event"] for e in events] == ["start", "step", "step", "end"]
        assert events[0]["macro"] == "test-macro"
        assert events[-1]["status"] == "ok"

    def test_failed_macro_logs_error_status(self, no_sleep: None, tmp_path: Path) -> None:
        """A step error still writes the end event with status=error."""
        sink = FakeSink()
        log_path = tmp_path / "logs" / "exec.jsonl"
        orch = MacroOrchestrator(sink=sink, log_path=log_path)
        macro = simple_macro(
            [
                Action(kind="action", op="tap", target="zzz"),  # no KEY_ZZZ exists
            ]
        )
        with pytest.raises(ValueError, match="unknown key target"):
            orch.run(macro)

        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert events[-1]["event"] == "end"
        assert events[-1]["status"] == "error"
