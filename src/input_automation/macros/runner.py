"""MacroRunner: binds macro trigger definitions to device trigger events.

Each macro's trigger list is converted into :class:`TriggerEngine` bindings;
when a matching device event fires, the orchestrator executes the macro's
step tree. The runner is the glue between the device layer (T4/T6) and the
macro layer (T10).
"""

from __future__ import annotations

from collections.abc import Callable

from evdev import ecodes

from input_automation.devices.listener import InputEvent
from input_automation.macros.models import Macro
from input_automation.macros.orchestrator import (
    MacroOrchestrator,
    OutputSink,
    _resolve_key,
)
from input_automation.triggers.engine import Binding, TriggerEngine

__all__ = ["MacroRunner"]


def _resolve_trigger_code(kind: str, code: str) -> int:
    """Map a macro trigger's code string to its evdev numeric code."""
    if kind == "key":
        return _resolve_key(code)
    if kind == "button":
        name = code.strip().upper()
        if name.startswith("BTN_"):
            name = name[4:]
        value = getattr(ecodes, f"BTN_{name}", None)
        if value is None:
            raise ValueError(f"unknown button code {code!r}")
        return int(value)
    raise ValueError(
        f"unsupported trigger kind {kind!r} (key and button only for now)"
    )


class MacroRunner:
    """Register macros so device events matching their triggers run them."""

    def __init__(
        self,
        engine: TriggerEngine,
        sink: OutputSink,
        orchestrator: MacroOrchestrator | None = None,
    ) -> None:
        self._engine = engine
        self._orchestrator = orchestrator or MacroOrchestrator(sink=sink)
        self._macros: dict[str, Macro] = {}

    def register(self, macro: Macro) -> None:
        """Expose the macro under its name; wire its triggers to the engine."""
        self._macros[macro.name] = macro
        for trigger in macro.triggers:
            code = _resolve_trigger_code(trigger.kind, trigger.code)
            binding = Binding(
                name=macro.name,
                codes=(code,),
                kind="tap",
                state=trigger.state,
                device=trigger.device,
            )
            self._engine.register(binding, self._make_callback(macro.name))

    def run_macro(self, name: str) -> None:
        """Execute a registered macro by name."""
        macro = self._macros[name]
        self._orchestrator.run(macro)

    def handle_event(self, event: InputEvent) -> None:
        """Feed a device event through the trigger engine."""
        self._engine.handle_event(event)

    # -- internals ------------------------------------------------------------

    def _make_callback(self, name: str) -> Callable[[str], None]:
        def _run(_binding_name: str) -> None:
            self.run_macro(name)

        return _run
