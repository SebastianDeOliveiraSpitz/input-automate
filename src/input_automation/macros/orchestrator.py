"""Macro orchestrator: executes a Macro's step tree against an output sink.

The orchestrator walks ``action``/``if``/``while``/``set`` steps, resolves
key names to evdev codes, and drives an output sink
(:class:`~input_automation.output.virtual.VirtualDevice` or a test fake).

``dry_run=True`` walks the full tree but never touches the sink — useful to
validate a macro without side effects. When ``log_path`` is set, every run
appends JSONL events (``start``, per-``step``, optional ``error``, ``end``)
to the file, creating parent directories as needed.

Headless-testable: the sink is injectable.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Protocol, TextIO

from evdev import ecodes

from input_automation.macros.models import (
    Action,
    Condition,
    IfStep,
    Macro,
    SetStep,
    Step,
    WhileStep,
)

__all__ = ["MacroOrchestrator", "NullSink", "OutputSink"]

_BUTTON_NAMES = {1: "left", 2: "middle", 3: "right"}


class NullSink:
    """No-op output sink for dry runs (the orchestrator never touches it)."""

    def key_tap(self, code: int) -> None:
        del code

    def key_press(self, code: int) -> None:
        del code

    def key_release(self, code: int) -> None:
        del code

    def mouse_move(self, dx: int, dy: int) -> None:
        del dx, dy

    def mouse_click(self, button: str) -> None:
        del button

    def scroll(self, dy: int) -> None:
        del dy


class OutputSink(Protocol):
    """Surface of VirtualDevice the orchestrator drives."""

    def key_tap(self, code: int) -> None: ...
    def key_press(self, code: int) -> None: ...
    def key_release(self, code: int) -> None: ...
    def mouse_move(self, dx: int, dy: int) -> None: ...
    def mouse_click(self, button: str) -> None: ...
    def scroll(self, dy: int) -> None: ...


def _resolve_key(target: str) -> int:
    """Map a key name like ``'a'`` to its evdev code (``KEY_A`` = 30)."""
    name = target.strip().upper()
    if not name.isidentifier():
        raise ValueError(f"invalid key target {target!r}")
    code = getattr(ecodes, f"KEY_{name}", None)
    if code is None:
        raise ValueError(f"unknown key target {target!r}")
    return int(code)


def _resolve_button(button: int) -> str:
    try:
        return _BUTTON_NAMES[button]
    except KeyError:
        raise ValueError(f"unknown mouse button {button!r}") from None


class MacroOrchestrator:
    """Execute a :class:`~input_automation.macros.models.Macro` step tree."""

    def __init__(
        self,
        sink: OutputSink,
        dry_run: bool = False,
        log_path: Path | None = None,
    ) -> None:
        self._sink = sink
        self._dry_run = dry_run
        self._log_path = log_path
        self._log_fh: TextIO | None = None

    # -- public API ------------------------------------------------------------

    def run(self, macro: Macro, repeat: int = 1) -> None:
        """Execute the macro's steps, ``repeat`` times, with a fresh scope."""
        if repeat < 1:
            raise ValueError("repeat must be >= 1")
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_fh = self._log_path.open("a", encoding="utf-8")
        status = "ok"
        try:
            self._log({"event": "start", "macro": macro.name, "repeat": repeat})
            for _ in range(repeat):
                self._execute_steps(macro.steps, scope={})
        except Exception as exc:  # noqa: BLE001 - logged then re-raised
            status = "error"
            self._log({"event": "error", "macro": macro.name, "error": repr(exc)})
            raise
        finally:
            self._log({"event": "end", "macro": macro.name, "status": status})
            if self._log_fh is not None:
                self._log_fh.close()
                self._log_fh = None

    # -- step dispatch ---------------------------------------------------------

    def _execute_steps(self, steps: list[Step], scope: dict[str, Any]) -> None:
        for step in steps:
            self._execute_step(step, scope)

    def _execute_step(self, step: Step, scope: dict[str, Any]) -> None:
        if isinstance(step, Action):
            self._log({"event": "step", **self._action_summary(step)})
            self._execute_action(step)
        elif isinstance(step, SetStep):
            self._log({"event": "step", "kind": "set", "var": step.var, "value": step.value})
            scope[step.var] = step.value
        elif isinstance(step, IfStep):
            taken = "then" if self._evaluate(step.condition, scope) else "else"
            self._log({"event": "step", "kind": "if", "taken": taken})
            branch = step.then if taken == "then" else step.else_
            self._execute_steps(branch, scope)
        elif isinstance(step, WhileStep):
            iteration = 0
            while self._evaluate(step.condition, scope):
                iteration += 1
                if iteration > step.max_iterations:
                    raise RuntimeError(
                        f"while loop exceeded max_iterations ({step.max_iterations})"
                    )
                self._log({"event": "step", "kind": "while", "iteration": iteration})
                self._execute_steps(step.body, scope)
        else:
            raise TypeError(f"unknown step kind {type(step).__name__}")

    # -- action execution --------------------------------------------------------

    def _execute_action(self, action: Action) -> None:
        op = action.op
        if op == "tap":
            code = _resolve_key(action.target)  # type: ignore[arg-type]
            if not self._dry_run:
                self._sink.key_tap(code)
        elif op == "press":
            code = _resolve_key(action.target)  # type: ignore[arg-type]
            if not self._dry_run:
                self._sink.key_press(code)
        elif op == "release":
            code = _resolve_key(action.target)  # type: ignore[arg-type]
            if not self._dry_run:
                self._sink.key_release(code)
        elif op == "wait":
            time.sleep(action.ms / 1000)
        elif op == "click":
            button = _resolve_button(action.button)  # type: ignore[arg-type]
            if not self._dry_run:
                self._sink.mouse_click(button)
        elif op == "move":
            if not self._dry_run:
                self._sink.mouse_move(action.dx or 0, action.dy or 0)
        elif op == "scroll":
            if not self._dry_run:
                self._sink.scroll(action.dy or 0)
        else:
            raise ValueError(f"unknown op {op!r}")

    # -- helpers -----------------------------------------------------------------

    def _evaluate(self, condition: Condition, scope: dict[str, Any]) -> bool:
        if condition.var not in scope:
            raise KeyError(condition.var)
        value = scope[condition.var]
        if condition.equals is not None:
            return bool(value == condition.equals)
        if condition.not_equals is not None:
            return bool(value != condition.not_equals)
        if condition.greater_than is not None:
            return bool(value > condition.greater_than)
        if condition.less_than is not None:
            return bool(value < condition.less_than)
        raise ValueError(f"condition on {condition.var!r} has no operator")

    def _action_summary(self, action: Action) -> dict[str, Any]:
        summary: dict[str, Any] = {"kind": "action", "op": action.op}
        for attr in ("target", "button", "dx", "dy", "x", "y", "ms"):
            value = getattr(action, attr)
            if value is not None:
                summary[attr] = value
        return summary

    def _log(self, event: dict[str, Any]) -> None:
        if self._log_fh is None:
            return
        event["ts"] = time.time()
        self._log_fh.write(json.dumps(event) + "\n")
        self._log_fh.flush()
