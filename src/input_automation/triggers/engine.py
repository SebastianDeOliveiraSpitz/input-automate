"""Trigger engine: matches normalized device events against trigger bindings.

Supported binding kinds (KISS — keyboard/button events only for now):

- ``tap``: fire when one code transitions to a state (down/up)
- ``combo``: fire once when all codes are held simultaneously
- ``sequence``: fire when the codes are pressed in order within a time window
- ``hold``: fire when a code stays down for ``hold_ms``

Headless-testable: the clock and the hold timer are injectable.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from input_automation.devices.listener import InputEvent

__all__ = ["Binding", "TriggerEngine"]

BindingKind = Literal["tap", "combo", "sequence", "hold"]
TriggerCallback = Callable[[str], None]
Clock = Callable[[], float]


@dataclass(frozen=True)
class Binding:
    """One trigger pattern and the name it reports when it fires."""

    name: str
    codes: tuple[int, ...]
    kind: BindingKind = "tap"
    state: Literal["down", "up"] = "down"
    device: str = "any"  # evdev path restriction; "any" = all devices
    hold_ms: int = 500
    window_ms: int = 500

    def __post_init__(self) -> None:
        if not self.codes:
            raise ValueError("binding needs at least one code")
        if self.kind == "hold" and self.hold_ms < 1:
            raise ValueError("hold binding needs hold_ms >= 1")
        if self.kind == "sequence" and self.window_ms < 1:
            raise ValueError("sequence binding needs window_ms >= 1")
        if self.kind not in ("tap", "combo", "sequence", "hold"):
            raise ValueError(f"unknown binding kind {self.kind!r}")


class TimerLike(Protocol):
    """The surface of threading.Timer the engine needs."""

    def start(self) -> None: ...

    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], TimerLike]


def _default_timer(interval: float, fn: Callable[[], None]) -> TimerLike:
    timer = threading.Timer(interval, fn)
    timer.daemon = True
    return cast(TimerLike, timer)


class TriggerEngine:
    """Match an ``InputEvent`` stream against registered ``Binding`` patterns.

    The engine is synchronous: a device-listener callback calls
    :meth:`handle_event` for every event. Callbacks fire in the listener
    thread — keep them fast, or dispatch to your own worker.
    """

    def __init__(
        self,
        clock: Clock = time.monotonic,
        timer_factory: TimerFactory | None = None,
    ) -> None:
        self._clock = clock
        self._timer_factory = timer_factory or _default_timer
        self._bindings: dict[str, list[tuple[Binding, TriggerCallback]]] = {}
        self._held: set[int] = set()
        self._combo_fired: dict[str, bool] = {}
        self._seq_progress: dict[tuple[str, int], tuple[float, ...]] = {}
        self._hold_timers: dict[tuple[str, int], TimerLike] = {}

    # -- registration ---------------------------------------------------------

    def register(self, binding: Binding, callback: TriggerCallback) -> None:
        self._bindings.setdefault(binding.name, []).append((binding, callback))
        if binding.kind == "combo":
            self._combo_fired.setdefault(binding.name, False)

    def remove(self, name: str) -> None:
        for key, timer in list(self._hold_timers.items()):
            if key[0] == name:
                timer.cancel()
                del self._hold_timers[key]
        self._bindings.pop(name, None)
        self._combo_fired.pop(name, None)
        for key in [k for k in self._seq_progress if k[0] == name]:
            del self._seq_progress[key]

    # -- event handling -------------------------------------------------------

    def handle_event(self, event: InputEvent) -> None:
        """Feed one normalized input event into the matcher."""
        if event.etype != "key" or event.value not in (0, 1):
            return  # only key down/up events drive triggers for now
        if event.value == 1:
            self._held.add(event.code)
        else:
            self._held.discard(event.code)
        for name, items in list(self._bindings.items()):
            for idx, (binding, callback) in enumerate(items):
                if binding.device != "any" and event.path != binding.device:
                    continue  # binding is restricted to another device
                if binding.kind == "tap":
                    self._check_tap(name, binding, callback, event)
                elif binding.kind == "combo":
                    self._check_combo(name, binding, callback)
                elif binding.kind == "sequence":
                    self._check_sequence(name, idx, binding, callback, event)
                elif binding.kind == "hold":
                    self._check_hold(name, idx, binding, callback, event)

    # -- kinds ----------------------------------------------------------------

    def _check_tap(
        self, name: str, binding: Binding, callback: TriggerCallback, event: InputEvent
    ) -> None:
        wanted = 1 if binding.state == "down" else 0
        if event.code == binding.codes[0] and event.value == wanted:
            callback(name)

    def _check_combo(
        self, name: str, binding: Binding, callback: TriggerCallback
    ) -> None:
        if not set(binding.codes).issubset(self._held):
            self._combo_fired[name] = False
            return
        if not self._combo_fired.get(name):
            self._combo_fired[name] = True
            callback(name)

    def _check_sequence(
        self,
        name: str,
        idx: int,
        binding: Binding,
        callback: TriggerCallback,
        event: InputEvent,
    ) -> None:
        if event.value == 0:
            return  # sequence counts presses only
        key = (name, idx)
        now = self._clock()
        times = self._seq_progress.get(key, ())
        if times and now - times[0] > binding.window_ms / 1000:
            times = ()  # window expired — restart
        position = len(times)
        if event.code != binding.codes[position]:
            # a press of the first code restarts the sequence
            times = (now,) if event.code == binding.codes[0] else ()
            self._seq_progress[key] = times
            return
        times = times + (now,)
        if len(times) == len(binding.codes):
            self._seq_progress[key] = ()
            callback(name)
        else:
            self._seq_progress[key] = times

    def _check_hold(
        self,
        name: str,
        idx: int,
        binding: Binding,
        callback: TriggerCallback,
        event: InputEvent,
    ) -> None:
        code = binding.codes[0]
        key = (name, idx)
        if event.value == 1 and event.code == code:
            old = self._hold_timers.get(key)
            if old is not None:
                old.cancel()

            def fire() -> None:
                if code in self._held:
                    callback(name)

            self._hold_timers[key] = self._timer_factory(
                binding.hold_ms / 1000, fire
            )
            self._hold_timers[key].start()
        elif event.value == 0 and event.code == code:
            timer = self._hold_timers.pop(key, None)
            if timer is not None:
                timer.cancel()
