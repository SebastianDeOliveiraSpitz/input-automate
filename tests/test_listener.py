"""Tests for the device listener with fake evdev devices (headless)."""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from evdev import ecodes
from typer.testing import CliRunner

import input_automation.cli as cli_module
from input_automation.devices.listener import DeviceListener, InputEvent


class FakeDevice:
    """Fake evdev device that yields a fixed event list."""

    def __init__(self, events: list[tuple[int, int, int]], name: str = "fake") -> None:
        self.name = name
        self._events = events
        self._closed = False

    def read_loop(self) -> Iterator[tuple[int, int, int]]:
        for event in self._events:
            if self._closed:
                return
            yield event

    def close(self) -> None:
        self._closed = True


class BlockingFakeDevice:
    """Fake device whose read_loop blocks until close() is called."""

    def __init__(self, name: str = "blocking") -> None:
        self.name = name
        self._close_evt = threading.Event()
        self.closed = False

    def read_loop(self) -> Iterator[tuple[int, int, int]]:
        yield (ecodes.EV_KEY, ecodes.KEY_A, 1)
        self._close_evt.wait(timeout=5)

    def close(self) -> None:
        self.closed = True
        self._close_evt.set()


class FakeFullDevice(FakeDevice):
    """Fake device satisfying both discovery (capabilities) and the listener."""

    def __init__(self, events: list[tuple[int, int, int]]) -> None:
        super().__init__(events)
        self.phys = "fake-phys"

    def capabilities(self) -> dict[int, list[int]]:
        # ecodes.keys maps int code -> name; its keys are the KEY_* codes
        return {ecodes.EV_KEY: [k for k in ecodes.keys if isinstance(k, int)]}


def _opener_for(devices: dict[str, object]) -> "object":
    def opener(path: Path) -> object:
        return devices[str(path)]

    return opener


def _collect(listener: DeviceListener, count: int) -> list[InputEvent]:
    events: list[InputEvent] = []
    for event in listener:
        events.append(event)
        if len(events) >= count:
            break
    return events


def test_key_press_release_repeat(tmp_path: Path) -> None:
    path = str(tmp_path / "event0")
    device = FakeDevice(
        [
            (ecodes.EV_KEY, ecodes.KEY_A, 1),
            (ecodes.EV_SYN, ecodes.SYN_REPORT, 0),
            (ecodes.EV_KEY, ecodes.KEY_A, 2),
            (ecodes.EV_KEY, ecodes.KEY_A, 0),
        ]
    )
    with DeviceListener(
        [path], open_device_fn=_opener_for({path: device})  # type: ignore[arg-type]
    ) as listener:
        events = _collect(listener, 3)

    # SYN_REPORT is filtered out
    assert len(events) == 3
    assert [e.action for e in events] == ["press", "repeat", "release"]
    assert [e.code_name for e in events] == ["KEY_A", "KEY_A", "KEY_A"]
    assert all(e.etype == "key" for e in events)
    assert all(e.path == path for e in events)


def test_motion_and_wheel_actions(tmp_path: Path) -> None:
    path = str(tmp_path / "event1")
    device = FakeDevice(
        [
            (ecodes.EV_REL, ecodes.REL_X, 10),
            (ecodes.EV_REL, ecodes.REL_Y, -5),
            (ecodes.EV_REL, ecodes.REL_WHEEL, 1),
        ]
    )
    with DeviceListener(
        [path], open_device_fn=_opener_for({path: device})  # type: ignore[arg-type]
    ) as listener:
        events = _collect(listener, 3)

    assert [e.action for e in events] == ["motion", "motion", "wheel"]
    assert [e.code_name for e in events] == ["REL_X", "REL_Y", "REL_WHEEL"]


def test_abs_change_action(tmp_path: Path) -> None:
    path = str(tmp_path / "event2")
    device = FakeDevice([(ecodes.EV_ABS, ecodes.ABS_X, 512)])
    with DeviceListener(
        [path], open_device_fn=_opener_for({path: device})  # type: ignore[arg-type]
    ) as listener:
        events = _collect(listener, 1)

    assert events[0].action == "change"
    assert events[0].code_name == "ABS_X"


def test_unknown_type_maps_to_raw(tmp_path: Path) -> None:
    path = str(tmp_path / "event3")
    device = FakeDevice([(ecodes.EV_MSC, ecodes.MSC_SCAN, 30)])
    with DeviceListener(
        [path], open_device_fn=_opener_for({path: device})  # type: ignore[arg-type]
    ) as listener:
        events = _collect(listener, 1)

    assert events[0].etype == "type4"  # EV_MSC
    assert events[0].action == "raw"


def test_callback_receives_events(tmp_path: Path) -> None:
    path = str(tmp_path / "event0")
    device = FakeDevice([(ecodes.EV_KEY, ecodes.KEY_B, 1)])
    received: list[InputEvent] = []
    with DeviceListener(
        [path],
        callback=received.append,
        open_device_fn=_opener_for({path: device}),  # type: ignore[arg-type]
    ):
        pass

    assert len(received) == 1
    assert received[0].code_name == "KEY_B"
    assert received[0].action == "press"


def test_stop_terminates_blocked_reader(tmp_path: Path) -> None:
    path = str(tmp_path / "event0")
    device = BlockingFakeDevice()
    listener = DeviceListener(
        [path], open_device_fn=_opener_for({path: device})  # type: ignore[arg-type]
    )
    listener.start()
    events = _collect(listener, 1)
    listener.stop()

    assert events[0].action == "press"
    assert device.closed is True
    # queue is drained: iteration ends via the None sentinel
    assert list(listener) == []


def test_default_paths_from_discovery(tmp_path: Path) -> None:
    event_path = tmp_path / "event0"
    event_path.touch()  # discovery globs the filesystem — the file must exist
    device = FakeFullDevice([(ecodes.EV_KEY, ecodes.KEY_C, 1)])
    devices = {str(event_path): device}

    with DeviceListener(
        open_device_fn=_opener_for(devices),  # type: ignore[arg-type]
        directory=tmp_path,
    ) as listener:
        events = _collect(listener, 1)

    assert len(events) == 1
    assert events[0].path == str(event_path)
    assert events[0].code_name == "KEY_C"


def test_describe_format(tmp_path: Path) -> None:
    event = InputEvent(
        ts=1700000000.123,
        path="/dev/input/event0",
        etype="key",
        code=30,
        code_name="KEY_A",
        value=1,
        action="press",
    )
    text = event.describe()
    assert "KEY_A" in text
    assert "press" in text
    assert "/dev/input/event0" in text
    assert event.as_dict()["action"] == "press"


def test_monitor_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    event = InputEvent(
        ts=1700000000.0,
        path="/dev/input/event0",
        etype="key",
        code=30,
        code_name="KEY_A",
        value=1,
        action="press",
    )

    class FakeListenerForCli:
        def __init__(self, paths: list[str] | None, on_error: object = None) -> None:
            self.paths = paths
            self.on_error = on_error

        def __enter__(self) -> "FakeListenerForCli":
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            pass

        def __iter__(self) -> Iterator[InputEvent]:
            return iter([event])

    monkeypatch.setattr(cli_module, "DeviceListener", FakeListenerForCli)
    result = CliRunner().invoke(cli_module.app, ["monitor", "/dev/input/event0"])

    assert result.exit_code == 0
    assert "KEY_A" in result.output
    assert "press" in result.output

    json_result = CliRunner().invoke(cli_module.app, ["monitor", "--json"])
    assert json_result.exit_code == 0
    assert '"code_name": "KEY_A"' in json_result.output
