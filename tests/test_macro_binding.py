"""Tests for trigger-to-macro binding: device events fire macro runs (T10)."""


from input_automation.devices.listener import InputEvent
from input_automation.macros.models import Action, Macro, Trigger
from input_automation.macros.runner import MacroRunner
from input_automation.triggers.engine import Binding, TriggerEngine


class FakeSink:
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


PATH = "/dev/input/event0"


def key_event(ts: float, code: int, code_name: str, value: int) -> InputEvent:
    """A normalized key event: value 1 = press, 0 = release."""
    return InputEvent(
        ts=ts,
        path=PATH,
        etype="key",
        code=code,
        code_name=code_name,
        value=value,
        action="press" if value == 1 else "release",
    )


def macro_one(name: str, key_target: str) -> Macro:
    return Macro(
        name=name,
        triggers=[Trigger(kind="key", code="x", state="down")],
        steps=[Action(kind="action", op="tap", target=key_target)],
    )


class TestDeviceTriggerBinding:
    def test_key_down_event_runs_macro(self) -> None:
        """A key event matching the macro's trigger fires the macro's steps."""
        sink = FakeSink()
        engine = TriggerEngine()
        runner = MacroRunner(engine=engine, sink=sink)
        runner.register(macro_one("m1", "a"))

        runner.handle_event(key_event(100.0, 45, "KEY_X", 1))  # KEY_X down
        assert sink.calls == [("key_tap", (30,))]

    def test_non_matching_event_runs_nothing(self) -> None:
        """Key events that don't match the trigger leave the sink untouched."""
        sink = FakeSink()
        engine = TriggerEngine()
        runner = MacroRunner(engine=engine, sink=sink)
        runner.register(macro_one("m1", "a"))

        runner.handle_event(key_event(100.0, 30, "KEY_A", 1))  # KEY_A down
        assert sink.calls == []

    def test_key_up_only_macro_ignores_down(self) -> None:
        """A state='up' trigger fires on release, not press."""
        sink = FakeSink()
        engine = TriggerEngine()
        runner = MacroRunner(engine=engine, sink=sink)
        macro = Macro(
            name="m2",
            triggers=[Trigger(kind="key", code="x", state="up")],
            steps=[Action(kind="action", op="tap", target="a")],
        )
        runner.register(macro)

        runner.handle_event(key_event(100.0, 45, "KEY_X", 1))  # press
        assert sink.calls == []
        runner.handle_event(key_event(101.0, 45, "KEY_X", 0))  # release
        assert sink.calls == [("key_tap", (30,))]


class TestBindingExposure:
    def test_register_exposes_engine_bindings(self) -> None:
        """Registration converts macro triggers into TriggerEngine bindings."""
        sink = FakeSink()
        engine = TriggerEngine()
        runner = MacroRunner(engine=engine, sink=sink)
        runner.register(macro_one("m1", "a"))

        assert engine._bindings["m1"]  # a binding named after the macro exists
        binding, _ = engine._bindings["m1"][0]
        assert isinstance(binding, Binding)
        assert binding.codes == (45,)  # KEY_X
