"""End-to-end test: the example YAML macro loads, registers, and fires (T10)."""

from pathlib import Path

from evdev import ecodes
from tests.test_macro_binding import FakeSink, key_event

from input_automation.macros.loader import load_macro
from input_automation.macros.runner import MacroRunner
from input_automation.triggers.engine import TriggerEngine

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "volume.yaml"


class TestExampleMacroEndToEnd:
    def test_example_loads_and_validates(self) -> None:
        """The shipped example macro passes schema validation."""
        macro = load_macro(EXAMPLE)
        assert macro.name == "volume-down"
        assert macro.triggers[0].code == "F10"
        # flat sequence: 3x (press, release) with 2 waits between
        assert [s.kind for s in macro.steps] == ["action"] * 8

    def test_example_fires_on_f10_and_repeats_three_times(self) -> None:
        """KEY_F10 down runs the macro: 3 presses of KEY_VOLUMEDOWN."""
        sink = FakeSink()
        runner = MacroRunner(engine=TriggerEngine(), sink=sink)
        runner.register(load_macro(EXAMPLE))

        runner.handle_event(key_event(100.0, ecodes.KEY_F10, "KEY_F10", 1))

        volume_presses = [c for c in sink.calls if c == ("key_press", (114,))]
        volume_releases = [c for c in sink.calls if c == ("key_release", (114,))]
        assert len(volume_presses) == 3
        assert len(volume_releases) == 3

    def test_example_does_not_fire_on_other_key(self) -> None:
        """KEY_A down does not trigger the volume macro."""
        sink = FakeSink()
        runner = MacroRunner(engine=TriggerEngine(), sink=sink)
        runner.register(load_macro(EXAMPLE))

        runner.handle_event(key_event(100.0, ecodes.KEY_A, "KEY_A", 1))
        assert sink.calls == []
