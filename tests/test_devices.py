"""Tests for device discovery with a fake evdev backend (headless)."""

from pathlib import Path

from evdev import ecodes

from input_automation.devices.discovery import DeviceInfo, classify_device, list_devices


class FakeDevice:
    """Mimics the surface of evdev.InputDevice used by discovery."""

    def __init__(self, name: str, phys: str, caps: dict[int, list[int]]) -> None:
        self.name = name
        self.phys = phys
        self._caps = caps

    def capabilities(self, verbose: bool = False) -> dict[int, list[int]]:
        return self._caps


def _caps(*ev_types: int, keys: list[int]) -> dict[int, list[int]]:
    caps: dict[int, list[int]] = {}
    for t in ev_types:
        caps.setdefault(t, [])
    caps[ecodes.EV_KEY] = list(keys)
    return caps


def _keyboard() -> dict[int, list[int]]:
    return _caps(ecodes.EV_KEY, keys=[ecodes.KEY_A, ecodes.KEY_ENTER, ecodes.KEY_SPACE])


def _mouse() -> dict[int, list[int]]:
    return _caps(ecodes.EV_KEY, ecodes.EV_REL, keys=[ecodes.BTN_LEFT, ecodes.BTN_RIGHT])


def _gamepad() -> dict[int, list[int]]:
    return _caps(ecodes.EV_KEY, ecodes.EV_ABS, keys=[ecodes.BTN_SOUTH, ecodes.BTN_START])


def _touchpad() -> dict[int, list[int]]:
    return _caps(ecodes.EV_KEY, ecodes.EV_ABS, keys=[ecodes.BTN_TOUCH])


class TestClassify:
    def test_keyboard(self) -> None:
        assert classify_device(_keyboard()) == frozenset({"keyboard"})

    def test_mouse(self) -> None:
        assert classify_device(_mouse()) == frozenset({"mouse"})

    def test_gamepad(self) -> None:
        assert classify_device(_gamepad()) == frozenset({"gamepad"})

    def test_touchpad(self) -> None:
        assert classify_device(_touchpad()) == frozenset({"touchpad"})

    def test_unknown_is_other(self) -> None:
        keys = [ecodes.BTN_TRIGGER_HAPPY1]
        assert classify_device(_caps(ecodes.EV_KEY, keys=keys)) == frozenset({"other"})


class TestListDevices:
    def _fake_dir(self, tmp_path: Path, *names: str) -> Path:
        for n in names:
            (tmp_path / n).touch()
        return tmp_path

    def test_lists_events_only(self, tmp_path: Path) -> None:
        d = self._fake_dir(tmp_path, "event0", "event3", "mouse0", "something")
        devices = list_devices(d, open_device_fn=lambda p: FakeDevice("kb", "", _keyboard()))
        assert [dev.path for dev in devices] == [str(d / "event0"), str(d / "event3")]

    def test_skips_unreadable(self, tmp_path: Path) -> None:
        self._fake_dir(tmp_path, "event0", "event1")

        def flaky(path: Path) -> FakeDevice:
            if path.name == "event0":
                raise PermissionError(path)
            return FakeDevice("kb", "", _keyboard())

        devices = list_devices(tmp_path, open_device_fn=flaky)
        assert len(devices) == 1
        assert devices[0].name == "kb"

    def test_info_dict_shape(self, tmp_path: Path) -> None:
        d = self._fake_dir(tmp_path, "event0")
        devices = list_devices(d, open_device_fn=lambda p: FakeDevice("pad", "usb-1", _gamepad()))
        info = devices[0]
        assert isinstance(info, DeviceInfo)
        assert info.as_dict() == {
            "path": str(d / "event0"),
            "name": "pad",
            "phys": "usb-1",
            "kinds": ["gamepad"],
        }
