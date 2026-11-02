"""Tests for the trigger engine with synthetic events (headless)."""

from collections.abc import Callable

import pytest
from evdev import ecodes

from input_automation.devices.listener import InputEvent
from input_automation.triggers.engine import Binding, TimerLike, TriggerEngine


class FakeClock:
    """Injectable clock for deterministic sequence-window tests."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTimer:
    """Injectable timer: fire() runs the callback unless cancelled."""

    def __init__(self, interval: float, fn: Callable[[], None]) -> None:
        self.interval = interval
        self.fn = fn
        self.started = False
        self.canceled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.canceled = True

    def fire(self) -> None:
        if self.started and not self.canceled:
            self.fn()


def _event(code: int, value: int) -> InputEvent:
    return InputEvent(
        ts=1700000000.0,
        path="/dev/input/fake",
        etype="key",
        code=code,
        code_name=f"code{code}",
        value=value,
        action="press" if value == 1 else "release",
    )


def _down(engine: TriggerEngine, *codes: int) -> None:
    for code in codes:
        engine.handle_event(_event(code, 1))


def _up(engine: TriggerEngine, *codes: int) -> None:
    for code in codes:
        engine.handle_event(_event(code, 0))


class TestTap:
    def test_fires_on_press(self) -> None:
        fired: list[str] = []
        engine = TriggerEngine()
        engine.register(Binding("macro", codes=(ecodes.KEY_A,)), fired.append)

        _down(engine, ecodes.KEY_A)
        assert fired == ["macro"]

    def test_does_not_fire_on_release_by_default(self) -> None:
        fired: list[str] = []
        engine = TriggerEngine()
        engine.register(Binding("macro", codes=(ecodes.KEY_A,)), fired.append)

        _down(engine, ecodes.KEY_A)
        _up(engine, ecodes.KEY_A)
        assert fired == ["macro"]  # only the press

    def test_fires_on_release_when_state_up(self) -> None:
        fired: list[str] = []
        engine = TriggerEngine()
        engine.register(Binding("macro", codes=(ecodes.KEY_A,), state="up"), fired.append)

        _down(engine, ecodes.KEY_A)
        assert fired == []
        _up(engine, ecodes.KEY_A)
        assert fired == ["macro"]

    def test_other_keys_do_not_trigger(self) -> None:
        fired: list[str] = []
        engine = TriggerEngine()
        engine.register(Binding("macro", codes=(ecodes.KEY_A,)), fired.append)

        _down(engine, ecodes.KEY_B)
        assert fired == []


class TestCombo:
    def test_fires_once_when_all_held(self) -> None:
        fired: list[str] = []
        engine = TriggerEngine()
        engine.register(
            Binding("combo", codes=(ecodes.KEY_A, ecodes.KEY_B), kind="combo"),
            fired.append,
        )

        _down(engine, ecodes.KEY_A)
        assert fired == []
        _down(engine, ecodes.KEY_B)
        assert fired == ["combo"]
        _up(engine, ecodes.KEY_B)
        _down(engine, ecodes.KEY_B)  # A still held -> refire allowed
        assert fired == ["combo", "combo"]

    def test_no_refire_while_still_held(self) -> None:
        fired: list[str] = []
        engine = TriggerEngine()
        engine.register(
            Binding("combo", codes=(ecodes.KEY_A, ecodes.KEY_B), kind="combo"),
            fired.append,
        )

        _down(engine, ecodes.KEY_A, ecodes.KEY_B)
        _down(engine, ecodes.KEY_A, ecodes.KEY_B)
        assert fired == ["combo"]


class TestSequence:
    def test_fires_in_order(self) -> None:
        fired: list[str] = []
        clock = FakeClock()
        engine = TriggerEngine(clock=clock)
        engine.register(
            Binding(
                "seq",
                codes=(ecodes.KEY_A, ecodes.KEY_B, ecodes.KEY_C),
                kind="sequence",
            ),
            fired.append,
        )

        _down(engine, ecodes.KEY_A)
        clock.advance(0.1)
        _down(engine, ecodes.KEY_B)
        clock.advance(0.1)
        _down(engine, ecodes.KEY_C)
        assert fired == ["seq"]

    def test_wrong_order_does_not_fire(self) -> None:
        fired: list[str] = []
        clock = FakeClock()
        engine = TriggerEngine(clock=clock)
        engine.register(
            Binding("seq", codes=(ecodes.KEY_A, ecodes.KEY_B), kind="sequence"),
            fired.append,
        )

        _down(engine, ecodes.KEY_B)
        _down(engine, ecodes.KEY_A)
        assert fired == []

    def test_window_expiry_resets(self) -> None:
        fired: list[str] = []
        clock = FakeClock()
        engine = TriggerEngine(clock=clock)
        engine.register(
            Binding(
                "seq",
                codes=(ecodes.KEY_A, ecodes.KEY_B),
                kind="sequence",
                window_ms=500,
            ),
            fired.append,
        )

        _down(engine, ecodes.KEY_A)
        clock.advance(0.6)  # beyond the 500ms window
        _down(engine, ecodes.KEY_B)
        assert fired == []

    def test_repeat_presses_ignored(self) -> None:
        fired: list[str] = []
        clock = FakeClock()
        engine = TriggerEngine(clock=clock)
        engine.register(
            Binding("seq", codes=(ecodes.KEY_A, ecodes.KEY_B), kind="sequence"),
            fired.append,
        )

        _down(engine, ecodes.KEY_A)
        engine.handle_event(_event(ecodes.KEY_A, 2))  # key repeat
        _down(engine, ecodes.KEY_B)
        assert fired == ["seq"]


class TestHold:
    def test_fires_after_hold_duration(self) -> None:
        fired: list[str] = []
        timers: list[FakeTimer] = []
        engine = TriggerEngine(
            timer_factory=lambda interval, fn: _make_timer(timers, interval, fn)
        )
        engine.register(
            Binding("hold", codes=(ecodes.KEY_A,), kind="hold", hold_ms=500),
            fired.append,
        )

        _down(engine, ecodes.KEY_A)
        assert len(timers) == 1
        timers[0].fire()
        assert fired == ["hold"]

    def test_release_cancels(self) -> None:
        fired: list[str] = []
        timers: list[FakeTimer] = []
        engine = TriggerEngine(
            timer_factory=lambda interval, fn: _make_timer(timers, interval, fn)
        )
        engine.register(
            Binding("hold", codes=(ecodes.KEY_A,), kind="hold", hold_ms=500),
            fired.append,
        )

        _down(engine, ecodes.KEY_A)
        _up(engine, ecodes.KEY_A)
        timers[0].fire()  # cancelled — must not fire
        assert fired == []

    def test_removed_binding_cancels_timer(self) -> None:
        fired: list[str] = []
        timers: list[FakeTimer] = []
        engine = TriggerEngine(
            timer_factory=lambda interval, fn: _make_timer(timers, interval, fn)
        )
        engine.register(
            Binding("hold", codes=(ecodes.KEY_A,), kind="hold", hold_ms=500),
            fired.append,
        )
        _down(engine, ecodes.KEY_A)
        engine.remove("hold")
        timers[0].fire()
        assert fired == []


class TestValidation:
    def test_empty_codes_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one code"):
            Binding("x", codes=())

    def test_hold_needs_hold_ms(self) -> None:
        with pytest.raises(ValueError, match="hold_ms"):
            Binding("x", codes=(1,), kind="hold", hold_ms=0)

    def test_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown binding kind"):
            Binding("x", codes=(1,), kind="combo2")  # type: ignore[arg-type]


def _make_timer(
    timers: list[FakeTimer], interval: float, fn: Callable[[], None]
) -> TimerLike:
    timer = FakeTimer(interval, fn)
    timers.append(timer)
    return timer
