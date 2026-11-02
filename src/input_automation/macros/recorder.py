"""Event recorder: capture evdev input events into a replayable macro (T12).

Feed normalized :class:`~input_automation.devices.listener.InputEvent` objects
into :meth:`Recorder.feed`, then convert the accumulated recording into a
validated :class:`~input_automation.macros.models.Macro` with
:meth:`Recorder.to_macro`. The conversion is deterministic:

- quick press+release of a key (nothing between, gap < ``min_gap_ms``)
  becomes a ``tap`` step; anything longer becomes ``press`` / ``wait`` /
  ``release`` — the gap logic naturally distinguishes tap from hold
- consecutive relative-motion events accumulate into one ``move(dx, dy)``
- ``REL_WHEEL`` becomes ``scroll(dy=...)`` (``*_HI_RES`` duplicates are
  dropped so wheels are not double-counted)
- mouse button press+release without motion between becomes ``click``
- gaps between events >= ``min_gap_ms`` become ``wait`` steps, scaled by
  ``speed`` (2.0 = replay twice as fast)

Recordings cannot express mouse drags (button held while motion occurs) —
that combination raises a :class:`ValueError` instead of silently mangling
the recording.

Auto-repeat events (value 2) are dropped; absolute events are ignored.
"""

from __future__ import annotations

from evdev import ecodes

from input_automation.devices.listener import InputEvent
from input_automation.macros.models import (
    Action,
    Macro,
    Step,
    StepKind,
    Trigger,
    TriggerKind,
)

__all__ = ["Recorder"]

_BTN_NUMBERS = {"BTN_LEFT": 1, "BTN_MIDDLE": 2, "BTN_RIGHT": 3}
_HI_RES_CODES = {ecodes.REL_WHEEL_HI_RES, ecodes.REL_HWHEEL_HI_RES}


class Recorder:
    """Accumulate input events and convert them into a macro."""

    def __init__(self, min_gap_ms: int = 50, speed: float = 1.0) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._min_gap_ms = min_gap_ms
        self._speed = speed
        self._events: list[InputEvent] = []

    def feed(self, event: InputEvent) -> None:
        """Add one normalized event to the recording."""
        self._events.append(event)

    def to_macro(
        self, name: str, trigger: Trigger | None = None
    ) -> Macro:
        """Convert the recording into a validated macro.

        ``trigger`` defaults to a F10-down binding (change it for real use).
        """
        steps = self._convert(self._sorted_events())
        return Macro(
            name=name,
            description=f"recorded ({len(steps)} steps, {self._speed:g}x speed)",
            triggers=[trigger] if trigger is not None else [
                Trigger(kind=TriggerKind.KEY, code="F10")
            ],
            steps=steps,
        )

    # -- conversion -----------------------------------------------------------

    def _sorted_events(self) -> list[InputEvent]:
        events = sorted(self._events, key=lambda e: e.ts)
        # drop key auto-repeat events (value 2); other event types with
        # value 2 (e.g. a REL_X delta of 2) are perfectly valid
        return [e for e in events if e.action != "repeat"]

    def _convert(self, events: list[InputEvent]) -> list[Step]:
        steps: list[Step] = []
        last_ts: float | None = None
        move: Action | None = None
        pending_click: str | None = None  # BTN name of a click not yet released

        def flush_move() -> None:
            nonlocal move
            if move is not None:
                steps.append(move)
                move = None

        def reject_drag() -> None:
            if pending_click is not None:
                raise ValueError(
                    f"mouse drag (motion/scroll while {pending_click} is held) "
                    "cannot be represented in the macro schema; use a Python "
                    "macro (see input_automation.macros.script) instead"
                )

        for event in events:
            if last_ts is not None:
                gap_ms = round((event.ts - last_ts) * 1000 / self._speed)
                if gap_ms >= self._min_gap_ms:
                    flush_move()
                    steps.append(Action(kind=StepKind.ACTION, op="wait", ms=gap_ms))
            last_ts = event.ts

            if event.etype == "key" and event.code_name.startswith("KEY_"):
                target = event.code_name.removeprefix("KEY_")
                if event.value == 1:
                    steps.append(Action(kind=StepKind.ACTION, op="press", target=target))
                elif event.value == 0 and steps:
                    last = steps[-1]
                    if (
                        isinstance(last, Action)
                        and last.op == "press"
                        and last.target == target
                    ):
                        steps[-1] = Action(kind=StepKind.ACTION, op="tap", target=target)
                    else:
                        steps.append(
                            Action(kind=StepKind.ACTION, op="release", target=target)
                        )
            elif event.etype == "key" and event.code_name in _BTN_NUMBERS:
                if event.value == 1:
                    pending_click = event.code_name
                    steps.append(
                        Action(
                            kind=StepKind.ACTION,
                            op="click",
                            button=_BTN_NUMBERS[event.code_name],
                        )
                    )
                else:
                    pending_click = None  # released; the click stands
            elif event.etype == "rel" and event.code in _HI_RES_CODES:
                continue  # duplicate of REL_WHEEL/REL_HWHEEL
            elif event.etype == "rel" and event.action == "wheel":
                reject_drag()
                flush_move()
                steps.append(Action(kind=StepKind.ACTION, op="scroll", dy=event.value))
            elif event.etype == "rel" and event.action == "motion":
                reject_drag()
                move = self._accumulate_move(move, event)
            # abs events and anything else are ignored

        flush_move()
        return steps

    def _accumulate_move(self, move: Action | None, event: InputEvent) -> Action:
        if event.code == ecodes.REL_X:
            dx, dy = event.value, 0
        else:
            dx, dy = 0, event.value
        if move is None:
            return Action(kind=StepKind.ACTION, op="move", dx=dx, dy=dy)
        return Action(
            kind=StepKind.ACTION,
            op="move",
            dx=(move.dx or 0) + dx,
            dy=(move.dy or 0) + dy,
        )
