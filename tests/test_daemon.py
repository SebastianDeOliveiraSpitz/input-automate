"""Tests for the always-on macro daemon (M7 T19). Headless, synthetic events."""

import threading
import time
from pathlib import Path

import pytest

from input_automation.daemon import RELOAD_INTERVAL_S, MacroDaemon
from input_automation.devices.listener import InputEvent


class RecordingSink:
    """Records orchestrator emissions instead of injecting."""

    def __init__(self) -> None:
        self.taps: list[int] = []

    def key_tap(self, code: int) -> None:
        self.taps.append(code)

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


def _key_event(code: int, value: int, code_name: str = "KEY_X") -> InputEvent:
    return InputEvent(
        ts=time.time(),
        path="/dev/input/event0",
        etype="key",
        code=code,
        code_name=code_name,
        value=value,
        action="press" if value == 1 else "release",
    )


def _write_macro(tmp_path: Path, name: str, trigger_code: str, target: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(
        f"name: {name}\ndescription: test\n"
        f"triggers:\n- kind: key\n  code: {trigger_code}\n"
        f"steps:\n- action: {{op: tap, target: {target}}}\n"
    )
    return path


KEY_Q, KEY_A, KEY_B = 16, 30, 48  # evdev KEY_Q=16, KEY_A=30, KEY_B=48


class TestArming:
    def test_arms_all_macros_and_reports_skips(self, tmp_path: Path) -> None:
        _write_macro(tmp_path, "one", "Q", "a")
        _write_macro(tmp_path, "two", "B", "b")
        (tmp_path / "broken.yaml").write_text("name: [not a macro")
        daemon = MacroDaemon(tmp_path, sink=RecordingSink())
        daemon.reload()
        assert sorted(daemon.armed) == ["one", "two"]
        assert any("broken.yaml" in s for s in daemon.skipped)

    def test_trigger_fires_macro_steps(self, tmp_path: Path) -> None:
        _write_macro(tmp_path, "q-to-a", "Q", "a")
        sink = RecordingSink()
        daemon = MacroDaemon(tmp_path, sink=sink)
        daemon.reload()
        daemon.handle_event(_key_event(KEY_Q, 1))  # press Q
        assert sink.taps == [KEY_A]  # macro tapped A exactly once
        daemon.handle_event(_key_event(KEY_Q, 0))  # release Q: no re-fire
        assert sink.taps == [KEY_A]

    def test_triggers_are_or_combined(self, tmp_path: Path) -> None:
        path = tmp_path / "multi.yaml"
        path.write_text(
            "name: multi\ndescription: test\n"
            "triggers:\n- kind: key\n  code: Q\n- kind: key\n  code: B\n"
            "steps:\n- action: {op: tap, target: a}\n"
        )
        sink = RecordingSink()
        daemon = MacroDaemon(tmp_path, sink=sink)
        daemon.reload()
        daemon.handle_event(_key_event(KEY_Q, 1))
        daemon.handle_event(_key_event(KEY_B, 1))
        assert sink.taps == [KEY_A, KEY_A]  # either trigger runs the same steps

    def test_device_specific_trigger_ignores_other_devices(self, tmp_path: Path) -> None:
        """The 'device' field restricts which device fires the macro."""
        path = tmp_path / "dev.yaml"
        path.write_text(
            "name: dev\ndescription: test\n"
            "triggers:\n- kind: key\n  device: /dev/input/event5\n  code: Q\n"
            "steps:\n- action: {op: tap, target: a}\n"
        )
        sink = RecordingSink()
        daemon = MacroDaemon(tmp_path, sink=sink)
        daemon.reload()
        event = _key_event(KEY_Q, 1, "KEY_Q")
        event = InputEvent(
            ts=event.ts, path="/dev/input/event0", etype="key", code=KEY_Q,
            code_name="KEY_Q", value=1, action="press",
        )
        daemon.handle_event(event)
        assert sink.taps == []  # wrong device -> ignored

        event_on_5 = InputEvent(
            ts=event.ts, path="/dev/input/event5", etype="key", code=KEY_Q,
            code_name="KEY_Q", value=1, action="press",
        )
        daemon.handle_event(event_on_5)
        assert sink.taps == [KEY_A]  # matching device -> fired


class TestHotReload:
    def test_file_change_re_arms(self, tmp_path: Path) -> None:
        path = _write_macro(tmp_path, "swap", "Q", "a")
        sink = RecordingSink()
        daemon = MacroDaemon(tmp_path, sink=sink)
        daemon.reload()
        daemon.handle_event(_key_event(KEY_Q, 1))
        assert sink.taps == [KEY_A]

        # user edits the macro: Q now taps B
        path.write_text(
            "name: swap\ndescription: test\n"
            "triggers:\n- kind: key\n  code: Q\n"
            "steps:\n- action: {op: tap, target: b}\n"
        )
        # guarantee a different mtime even on coarse filesystems
        with pytest.MonkeyPatch.context():
            import os
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000))
            assert daemon.check_reload() is True

        sink.taps.clear()
        daemon.handle_event(_key_event(KEY_Q, 1))
        assert sink.taps == [KEY_B]

    def test_removed_file_disarms(self, tmp_path: Path) -> None:
        path = _write_macro(tmp_path, "gone", "Q", "a")
        daemon = MacroDaemon(tmp_path, sink=RecordingSink())
        daemon.reload()
        assert daemon.armed == ["gone"]
        path.unlink()
        assert daemon.check_reload() is True
        assert daemon.armed == []

    def test_check_reload_without_change_is_noop(self, tmp_path: Path) -> None:
        _write_macro(tmp_path, "stable", "Q", "a")
        daemon = MacroDaemon(tmp_path, sink=RecordingSink())
        daemon.reload()
        assert daemon.check_reload() is False
        assert daemon.armed == ["stable"]

    def test_reload_interval_is_small(self) -> None:
        """Macro edits must be picked up quickly (Basti edits live)."""
        assert RELOAD_INTERVAL_S <= 5.0


class TestThreadSafety:
    def test_handle_event_during_reload_never_crashes(
        self, tmp_path: Path
    ) -> None:
        _write_macro(tmp_path, "safe", "Q", "a")
        daemon = MacroDaemon(tmp_path, sink=RecordingSink())
        daemon.reload()
        errors: list[Exception] = []

        def hammer() -> None:
            for _ in range(200):
                try:
                    daemon.handle_event(_key_event(KEY_Q, 1))
                    time.sleep(0.001)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        thread = threading.Thread(target=hammer)
        thread.start()
        for _ in range(10):
            daemon.reload()
        thread.join()
        assert errors == []
