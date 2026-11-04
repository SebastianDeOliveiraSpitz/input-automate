"""Always-on macro arming (M7 T19).

Arms all macros in the macro directory and fires them on device events.
Semantics per macro: the triggers are OR-combined — *any* firing trigger
executes the macro's full step tree. A trigger's ``device`` field
restricts the binding to that evdev path.

The same :class:`MacroDaemon` serves both front ends:

- **GUI first citizen**: the tile editor arms/disarms the cluster
  in-process (:meth:`start` / :meth:`stop`) — no background process
  needed while the GUI is open.
- **Headless daemon**: ``input-automation daemon`` (CLI/systemd) runs the
  same engine blocking (:meth:`run`).

A lock file guarantees only ONE armed instance at a time (a GUI and a
daemon running side by side would inject every event twice). Macro files
are hot-reloaded (mtime polling). Every firing is appended to a JSONL
execution log (AGENTS.md rule).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from input_automation.devices.listener import DeviceListener, InputEvent
from input_automation.macros.loader import load_macro, load_python_macro
from input_automation.macros.orchestrator import MacroOrchestrator, OutputSink
from input_automation.macros.runner import MacroRunner
from input_automation.output.virtual import VirtualDevice
from input_automation.triggers.engine import TriggerEngine

__all__ = [
    "AlreadyArmed",
    "MacroDaemon",
    "run_daemon",
    "DEFAULT_MACRO_DIR",
    "LOCK_PATH",
    "RELOAD_INTERVAL_S",
]

# Macro home per AGENTS.md: ~/.config/input-automate/macros/
DEFAULT_MACRO_DIR = Path.home() / ".config" / "input-automate" / "macros"
LOCK_PATH = Path.home() / ".config" / "input-automate" / "armed.lock"
RELOAD_INTERVAL_S = 2.0

_EXECUTION_LOG = Path.home() / ".config" / "input-automate" / "logs" / "executions.jsonl"


class AlreadyArmed(RuntimeError):
    """Another process already holds the arming lock (GUI or daemon)."""


class MacroDaemon:
    """Arms all macros in a directory and fires them on device events.

    Lifecycle (GUI): :meth:`start` arms everything on background threads,
    :meth:`stop` disarms. Lifecycle (CLI): :meth:`run` blocks. Reload
    semantics: :meth:`reload` re-reads all macro files (per-file tolerant
    — one broken file cannot kill the cluster); :meth:`check_reload`
    re-arms when files changed.
    """

    def __init__(
        self,
        macro_dir: Path = DEFAULT_MACRO_DIR,
        *,
        sink: OutputSink | None = None,
        device_paths: Sequence[str] | None = None,
        listener_factory: Callable[..., DeviceListener] | None = None,
        log_path: Path | None = _EXECUTION_LOG,
        lock_path: Path = LOCK_PATH,
    ) -> None:
        self._macro_dir = macro_dir
        self._sink = sink
        self._device_paths = list(device_paths) if device_paths else None
        self._listener_factory = listener_factory
        self._log_path = log_path
        self._lock_path = lock_path
        self._lock: threading.Lock = threading.Lock()
        self._lock_file: Any = None  # the flock-held file object
        self._runner: MacroRunner | None = None
        self._listener: DeviceListener | None = None
        self._watcher_stop: threading.Event | None = None
        self._running = False
        self.armed: list[str] = []
        self.skipped: list[str] = []
        self.last_error: str | None = None

    # -- arming ----------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

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
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the cluster
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

    # -- lifecycle (GUI + CLI share this) ---------------------------------------

    def _acquire_lock(self) -> None:
        """Exclusive flock: exactly one armed instance process-wide."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self._lock_path.open("w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            self._lock_file.close()
            self._lock_file = None
            raise AlreadyArmed(
                "another armed instance is already running "
                "(the GUI or 'input-automation daemon')"
            ) from exc
        self._lock_file.write(str(os.getpid()))
        self._lock_file.flush()

    def _release_lock(self) -> None:
        if self._lock_file is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def _make_listener(self) -> DeviceListener:
        factory = self._listener_factory
        if factory is not None:
            return factory(paths=self._device_paths, callback=self.handle_event)
        return DeviceListener(paths=self._device_paths, callback=self.handle_event)

    def start(self) -> None:
        """Arm the cluster in the background (GUI path). Raises AlreadyArmed.

        Never leaves the daemon half-started: on any failure everything
        allocated so far is torn down again.
        """
        if self._running:
            return
        try:
            self._acquire_lock()
        except AlreadyArmed:
            raise
        except Exception as exc:  # noqa: BLE001 - never crash the GUI on arming
            self.last_error = f"could not start: {exc}"
            raise RuntimeError(self.last_error) from exc
        try:
            if self._runner is None:
                self.reload()
            self._listener = self._make_listener()
            self._watcher_stop = threading.Event()
            self._running = True
            self._listener.start()
            threading.Thread(target=self._watch_files_loop, daemon=True).start()
        except Exception:  # noqa: BLE001 - roll back on any start failure
            self.stop()
            raise

    def stop(self) -> None:
        """Disarm the cluster (listener threads stop, lock released).

        Never raises: every step is wrapped so a misbehaving evdev device
        or stale lock file can never crash the GUI.
        """
        if not self._running:
            return
        self._running = False
        if self._watcher_stop is not None:
            self._watcher_stop.set()
        # stop the listener first: close evdev devices, join reader threads.
        # Wrap in try/except — closing a fd another thread is blocked reading
        # can raise, and we must never let that reach the GUI.
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception as exc:  # noqa: BLE001 - listener teardown must not kill the GUI
                self.last_error = f"listener stop failed: {exc}"
        # close the virtual uinput device so no injected key can stay stuck
        # after disarm (e.g. a macro that pressed a key without releasing)
        runner = self._runner
        self._runner = None
        if runner is not None:
            try:
                runner.close()
            except Exception as exc:  # noqa: BLE001 - never crash on teardown
                self.last_error = (self.last_error or "") + f"; sink close failed: {exc}"
        self._release_lock()

    def _watch_files_loop(self) -> None:
        stop = self._watcher_stop
        if stop is None:  # defensive: start() always sets this
            return
        while not stop.is_set():
            stop.wait(RELOAD_INTERVAL_S)
            with contextlib.suppress(Exception):
                self.check_reload()

    def run(self) -> None:  # pragma: no cover - live loop, verified on real hardware
        """Block and run the daemon: device events in, macros out."""
        self.start()
        try:
            while self._running:
                time.sleep(3600)
        finally:
            self.stop()

    def detect_input(
        self,
        callback: Callable[[InputEvent], None],
        timeout_s: float = 10.0,
    ) -> bool:
        """Listen for the next key/button event on any device, call
        ``callback(event)`` with it, then stop. Returns ``True`` if an
        event was captured, ``False`` on timeout.

        Used by the GUI's "detect" mode so users don't have to look up
        evdev key codes by hand — they press the key and the code is
        filled in automatically.
        """
        event_received = threading.Event()

        def wrapped(event: InputEvent) -> None:
            try:
                callback(event)
            finally:
                event_received.set()

        listener = DeviceListener(
            paths=self._device_paths,
            callback=wrapped,
        )
        try:
            listener.start()
        except Exception:  # noqa: BLE001 - no devices available
            return False
        try:
            return event_received.wait(timeout_s)
        finally:
            with contextlib.suppress(Exception):
                listener.stop()


def run_daemon(
    macro_dir: Path = DEFAULT_MACRO_DIR,
    *,
    sink: OutputSink | None = None,
) -> None:  # pragma: no cover - live loop
    """Create and run the daemon (blocking; Ctrl+C stops it)."""
    daemon = MacroDaemon(macro_dir, sink=sink)
    try:
        daemon.reload()
        print(  # noqa: T201 - CLI feedback for a foreground daemon
            f"armed {len(daemon.armed)} macro(s) from {macro_dir}"
            + (f" (skipped: {', '.join(daemon.skipped)})" if daemon.skipped else ""),
            flush=True,
        )
        daemon.run()
    except KeyboardInterrupt:
        print("daemon stopped", flush=True)  # noqa: T201
    except AlreadyArmed as exc:
        print(f"error: {exc}", flush=True)  # noqa: T201
        raise SystemExit(1) from None
