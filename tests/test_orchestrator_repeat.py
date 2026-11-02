"""Additional orchestrator behavior for T12: repeat replay."""

from input_automation.macros.models import Action, Macro, Trigger
from input_automation.macros.orchestrator import MacroOrchestrator


class CountingSink:
    def __init__(self) -> None:
        self.taps: list[int] = []

    def key_tap(self, code: int) -> None:
        self.taps.append(code)

    def key_press(self, code: int) -> None:
        raise AssertionError("not used in this test")

    def key_release(self, code: int) -> None:
        raise AssertionError("not used in this test")

    def mouse_move(self, dx: int, dy: int) -> None:
        raise AssertionError("not used in this test")

    def mouse_click(self, button: str) -> None:
        raise AssertionError("not used in this test")

    def scroll(self, dy: int) -> None:
        raise AssertionError("not used in this test")


def test_repeat_runs_macro_n_times() -> None:
    sink = CountingSink()
    orch = MacroOrchestrator(sink=sink)
    macro = Macro(
        name="repeat-test",
        triggers=[Trigger(kind="key", code="x")],
        steps=[Action(kind="action", op="tap", target="a")],
    )
    orch.run(macro, repeat=3)
    assert sink.taps == [30, 30, 30]


def test_repeat_defaults_to_one() -> None:
    sink = CountingSink()
    orch = MacroOrchestrator(sink=sink)
    macro = Macro(
        name="repeat-default",
        triggers=[Trigger(kind="key", code="x")],
        steps=[Action(kind="action", op="tap", target="a")],
    )
    orch.run(macro)
    assert sink.taps == [30]
