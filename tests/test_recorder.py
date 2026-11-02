"""Tests for the event recorder (T12): evdev events -> replayable macro."""

from pathlib import Path

import pytest
from evdev import ecodes

from input_automation.devices.listener import InputEvent
from input_automation.macros.loader import dump_macro, load_macro
from input_automation.macros.models import Action
from input_automation.macros.recorder import Recorder


def key(ts: float, name: str, value: int) -> InputEvent:
    """A normalized key event: value 1 = press, 0 = release, 2 = repeat."""
    return InputEvent(
        ts=ts,
        path="/dev/input/event0",
        etype="key",
        code=getattr(ecodes, f"KEY_{name}"),
        code_name=f"KEY_{name}",
        value=value,
        action="press" if value == 1 else ("repeat" if value == 2 else "release"),
    )


def rel(ts: float, name: str, value: int) -> InputEvent:
    code = getattr(ecodes, name)
    action = "motion" if name.startswith("REL_X") or name.startswith("REL_Y") else "wheel"
    return InputEvent(
        ts=ts,
        path="/dev/input/event1",
        etype="rel",
        code=code,
        code_name=name,
        value=value,
        action=action,
    )


def btn(ts: float, name: str, value: int) -> InputEvent:
    return InputEvent(
        ts=ts,
        path="/dev/input/event1",
        etype="key",
        code=getattr(ecodes, f"BTN_{name}"),
        code_name=f"BTN_{name}",
        value=value,
        action="press" if value == 1 else "release",
    )


def ops(macro_steps: list) -> list[tuple]:
    """Flatten action steps into (op, payload) tuples for compact asserts."""
    result = []
    for step in macro_steps:
        assert isinstance(step, Action), f"expected action, got {step!r}"
        result.append((step.op, step.target, step.button, step.dx, step.dy, step.ms))
    return result


class TestKeyRecording:
    def test_quick_press_release_becomes_tap(self) -> None:
        rec = Recorder()
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.02, "A", 0))
        steps = rec.to_macro(name="tap-test").steps
        assert ops(steps) == [("tap", "A", None, None, None, 0)]

    def test_held_key_becomes_press_wait_release(self) -> None:
        rec = Recorder(min_gap_ms=50)
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.2, "A", 0))  # held 200ms -> too long for a tap
        steps = rec.to_macro(name="hold-test").steps
        assert ops(steps) == [
            ("press", "A", None, None, None, 0),
            ("wait", None, None, None, None, 200),
            ("release", "A", None, None, None, 0),
        ]

    def test_gap_becomes_wait(self) -> None:
        rec = Recorder(min_gap_ms=50)
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.01, "A", 0))
        rec.feed(key(1.2, "B", 1))  # 190ms gap after the tap
        rec.feed(key(1.21, "B", 0))
        steps = rec.to_macro(name="gap-test").steps
        assert ops(steps) == [
            ("tap", "A", None, None, None, 0),
            ("wait", None, None, None, None, 190),
            ("tap", "B", None, None, None, 0),
        ]

    def test_repeat_events_skipped(self) -> None:
        """Auto-repeat events are dropped, but the hold duration is preserved."""
        rec = Recorder(min_gap_ms=50)
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.05, "A", 2))  # auto-repeat
        rec.feed(key(1.06, "A", 2))
        rec.feed(key(1.08, "A", 0))  # held 80ms total -> not a tap
        steps = rec.to_macro(name="repeat-test").steps
        assert ops(steps) == [
            ("press", "A", None, None, None, 0),
            ("wait", None, None, None, None, 80),
            ("release", "A", None, None, None, 0),
        ]

    def test_events_sorted_by_timestamp(self) -> None:
        """Out-of-order feed is sorted; the 190ms gap between A and B remains."""
        rec = Recorder(min_gap_ms=50)
        rec.feed(key(1.21, "B", 0))
        rec.feed(key(1.20, "B", 1))
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.01, "A", 0))
        steps = rec.to_macro(name="sort-test").steps
        assert ops(steps) == [
            ("tap", "A", None, None, None, 0),
            ("wait", None, None, None, None, 190),
            ("tap", "B", None, None, None, 0),
        ]


class TestMouseRecording:
    def test_consecutive_motion_accumulates(self) -> None:
        rec = Recorder(min_gap_ms=50)
        rec.feed(rel(1.0, "REL_X", 5))
        rec.feed(rel(1.01, "REL_Y", -3))
        rec.feed(rel(1.02, "REL_X", 2))
        steps = rec.to_macro(name="move-test").steps
        assert ops(steps) == [("move", None, None, 7, -3, 0)]

    def test_wheel_becomes_scroll(self) -> None:
        rec = Recorder(min_gap_ms=50)
        rec.feed(rel(1.0, "REL_WHEEL", 1))
        steps = rec.to_macro(name="scroll-test").steps
        assert ops(steps) == [("scroll", None, None, None, 1, 0)]

    def test_wheel_hi_res_not_double_counted(self) -> None:
        rec = Recorder(min_gap_ms=50)
        rec.feed(rel(1.0, "REL_WHEEL", 1))
        rec.feed(rel(1.001, "REL_WHEEL_HI_RES", 120))
        steps = rec.to_macro(name="scroll-test").steps
        assert ops(steps) == [("scroll", None, None, None, 1, 0)]

    def test_button_click_becomes_click(self) -> None:
        rec = Recorder(min_gap_ms=50)
        rec.feed(btn(1.0, "LEFT", 1))
        rec.feed(btn(1.02, "LEFT", 0))
        steps = rec.to_macro(name="click-test").steps
        assert ops(steps) == [("click", None, 1, None, None, 0)]

    def test_drag_raises_clear_error(self) -> None:
        """A click with motion in between cannot be represented; fail loudly."""
        rec = Recorder(min_gap_ms=50)
        rec.feed(btn(1.0, "LEFT", 1))
        rec.feed(rel(1.01, "REL_X", 10))
        rec.feed(btn(1.05, "LEFT", 0))
        with pytest.raises(ValueError, match="drag"):
            rec.to_macro(name="drag-test")


class TestSpeedAndAssembly:
    def test_speed_factor_scales_waits(self) -> None:
        rec = Recorder(min_gap_ms=50, speed=2.0)
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.01, "A", 0))
        rec.feed(key(1.4, "B", 1))  # raw gap 390ms -> 195ms at 2x speed
        rec.feed(key(1.41, "B", 0))
        steps = rec.to_macro(name="speed-test").steps
        assert ops(steps)[1] == ("wait", None, None, None, None, 195)

    def test_macro_has_default_trigger_and_validates(self) -> None:
        rec = Recorder()
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.01, "A", 0))
        macro = rec.to_macro(name="recorded")
        assert macro.name == "recorded"
        assert macro.triggers[0].kind == "key"
        assert macro.triggers[0].code == "F10"

    def test_custom_trigger(self) -> None:
        from input_automation.macros.models import Trigger

        rec = Recorder()
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.01, "A", 0))
        macro = rec.to_macro(
            name="custom", trigger=Trigger(kind="key", code="F2", state="down")
        )
        assert macro.triggers[0].code == "F2"

    def test_dump_roundtrip(self, tmp_path: Path) -> None:
        rec = Recorder()
        rec.feed(key(1.0, "A", 1))
        rec.feed(key(1.01, "A", 0))
        macro = rec.to_macro(name="roundtrip")
        path = tmp_path / "recorded.yaml"
        dump_macro(macro, path)
        loaded = load_macro(path)
        assert loaded.name == macro.name
        assert len(loaded.steps) == len(macro.steps)
        assert len(loaded.triggers) == len(macro.triggers)
