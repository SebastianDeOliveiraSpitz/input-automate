"""CLI-facing recording orchestration: JSONL conversion and live recording.

Keeps :mod:`input_automation.cli` thin — this module owns the glue between
the CLI options and the T12 :class:`Recorder` (event conversion) plus the
T4 :class:`DeviceListener` (live capture).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from input_automation.devices.listener import InputEvent
from input_automation.macros.loader import dump_macro
from input_automation.macros.models import Macro, Trigger, TriggerKind
from input_automation.macros.recorder import Recorder

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["events_from_jsonl", "record_macro"]


def events_from_jsonl(path: Path) -> list[InputEvent]:
    """Parse a JSONL file of ``InputEvent.as_dict()`` dicts into events."""
    events: list[InputEvent] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(
                    InputEvent(
                        ts=data["ts"],
                        path=data.get("path", ""),
                        etype=data["type"],
                        code=data["code"],
                        code_name=data.get("code_name", ""),
                        value=data["value"],
                        action=data.get("action", ""),
                    )
                )
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise typer.BadParameter(
                    f"{path.name}:{lineno}: invalid event record: {exc}"
                ) from exc
    return events


def record_macro(
    *,
    output: Path,
    from_events: Path | None,
    trigger: str,
    speed: float,
    min_gap_ms: int,
    devices: Sequence[str] | None,
    duration: float | None,
) -> Macro:
    """Record into a macro and write it as YAML; returns the macro."""
    recorder = Recorder(min_gap_ms=min_gap_ms, speed=speed)
    if from_events is not None:
        for event in events_from_jsonl(from_events):
            recorder.feed(event)
    else:
        _record_live(
            recorder, devices=devices, trigger=trigger, duration=duration
        )
    macro = recorder.to_macro(
        name=output.stem, trigger=Trigger(kind=TriggerKind.KEY, code=trigger)
    )
    if not macro.steps:
        raise ValueError(
            "no input events were recorded — nothing to convert; "
            "check device permissions (add your user to the 'input' group) "
            "and try again"
        )
    dump_macro(macro, output)
    return macro


def _record_live(
    recorder: Recorder,
    *,
    devices: Sequence[str] | None,
    trigger: str,
    duration: float | None,
) -> None:
    """Capture events from real devices until the trigger key or duration.

    The recording ends when the trigger key (``KEY_<trigger>``) is pressed
    (itself excluded from the recording) or after ``duration`` seconds.
    """
    from input_automation.devices.listener import DeviceListener

    paths = [str(p) for p in devices] if devices else None
    trigger_name = f"KEY_{trigger}"
    done = threading.Event()

    def on_event(event: InputEvent) -> None:
        if (
            event.etype == "key"
            and event.value == 1
            and event.code_name == trigger_name
        ):
            done.set()
            return
        recorder.feed(event)

    with DeviceListener(paths=paths, callback=on_event):
        done.wait(duration)
