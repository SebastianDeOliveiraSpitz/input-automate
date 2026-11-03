"""Always-on macro daemon (M7 T19).

Watches the input devices continuously and fires macros whenever one of
their trigger bindings matches. Semantics per macro: the triggers are
OR-combined — *any* firing trigger executes the macro's full step tree.

- All macros in the macro directory are armed (YAML and ``.py``).
- Macro files are hot-reloaded: changed/added/deleted files are picked up
  within ``RELOAD_INTERVAL_S`` (mtime polling; simple and reliable).
- Every firing is appended to a JSONL execution log (AGENTS.md rule).
- Headless-testable: the device listener and the output sink are
  injectable; :meth:`reload`, :meth:`check_reload` and
  :meth:`handle_event` are public.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from input_automation.devices.listener import DeviceListener, InputEvent
from input_automation.macros.loader import load_macro, load_python_macro
from input_automation.macros.orchestrator import MacroOrchestrator, OutputSink
from input_automation.macros.runner import MacroRunner
from input_automation.output.virtual import VirtualDevice
from input_automation.triggers.engine import TriggerEngine

__all__ = ["MacroDaemon", "run_daemon", "DEFAULT_MACRO_DIR", "RELOAD_INTERVAL_S"]

# Macro home per AGENTS.md: ~/.config/input-automate/macros/
DEFAULT_MACRO_DIR = Path.home() / ".config" / "input-automate" / "macros"
RELOAD_INTERVAL_S = 2.0

_EXECUTION_LOG = Path.home() / ".config" / "input-automate" / "logs" / "executions.jsonl"


class MacroDaemon:
    """Arms all macros in a directory and fires them on device events."""

    def __init__(
        self,
        macro_dir: Path = DEFAULT_MACRO_DIR,
        *,
        sink: OutputSink | None = None,
        device_paths: Sequence[str] | None = None,
        listener_factory: Callable[..., DeviceListener] | None = None,
        log_path: Path | None = _EXECUTION_LOG,
    ) -> None:
        self._macro_dir = macro_dir
        self._sink = sink
        self._device_paths = list(device_paths) if device_paths else None
        self._listener_factory = listener_factory
        self._log_path = log_path
        self._lock = threading.Lock()
        self._runner: MacroRunner | None = None
        self._mtimes: dict[Path, float] = {}
        self.armed: list[str] = []
        self.skipped: list[str] = []

    # -- arming ----------------------------------------------------------------

    def reload(self) -> None:
        """(Re)load every macro file; replaces the trigger bindings wholesale.

        A fresh engine/runner per reload keeps de-registration trivial.
        """
        sink = self._sink or VirtualDevice()
        runner = MacroRunner(
            engine=TriggerEngine(),
            sink=sink,
            orchestrator=MacroOrchestrator(sink=sink, log_path=self._log_path),
        )
        armed: list[str] = []
        skipped: list[str] = []
        mtimes: dict[Path, float] = {}
        for path in sorted(self._macro_dir.glob("*")):
            if path.suffix not in (".yaml", ".yml", ".py"):
                continue
            try:
                if path.suffix in (".yaml", ".yml"):
                    macro = load_macro(path)
                else:
                    macro = load_python_macro(path)
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the daemon
                skipped.append(f"{path.name}: {exc}")
                continue
            try:
                runner.register(macro)
            except Exception as exc:  # noqa: BLE001 - unknown trigger code etc.
                skipped.append(f"{path.name}: {exc}")
                continue
            armed.append(macro.name)
            with contextlib.suppress(OSError):
                mtimes[path] = path.stat().st_mtime
        with self._lock:
            self._runner = runner
            self._mtimes = mtimes
            self.armed = armed
            self.skipped = skipped

    def check_reload(self) -> bool:
        """Re-arm when macro files changed/appeared/disappeared. True if reloaded."""
        current: dict[Path, float] = {}
        with contextlib.suppress(OSError):
            for path in self._macro_dir.iterdir():
                if path.suffix in (".yaml", ".yml", ".py"):
                    current[path] = path.stat().st_mtime
        if current != self._mtimes:
            self.reload()
            return True
        return False

    # -- event flow ------------------------------------------------------------

    def handle_event(self, event: InputEvent) -> None:
        """Feed one device event into the armed trigger bindings."""
        with self._lock:
            runner = self._runner
        if runner is None:
            return
        runner.handle_event(event)

    # -- long-running loop -------------------------------------------------------

    def run(self) -> None:  # pragma: no cover - live loop, verified on real hardware
        """Block and run the daemon: device events in, macros out."""
        if self._runner is None:
            self.reload()
        listener = (
            self._listener_factory(paths=self._device_paths, callback=self.handle_event)
            if self._listener_factory is not None
            else DeviceListener(paths=self._device_paths, callback=self.handle_event)
        )

        def _watch_files() -> None:
            while True:
                time.sleep(RELOAD_INTERVAL_S)
                with contextlib.suppress(Exception):
                    self.check_reload()

        threading.Thread(target=_watch_files, daemon=True).start()
        with listener:
            while True:
                time.sleep(3600)


def run_daemon(
    macro_dir: Path = DEFAULT_MACRO_DIR,
    *,
    sink: OutputSink | None = None,
) -> None:  # pragma: no cover - live loop
    """Create and run the daemon (blocking; Ctrl+C stops it)."""
    daemon = MacroDaemon(macro_dir, sink=sink)
    daemon.reload()
    print(  # noqa: T201 - CLI feedback for a foreground daemon
        f"armed {len(daemon.armed)} macro(s) from {macro_dir}"
        + (f" (skipped: {', '.join(daemon.skipped)})" if daemon.skipped else ""),
        flush=True,
    )
    try:
        daemon.run()
    except KeyboardInterrupt:
        print("daemon stopped", flush=True)  # noqa: T201
