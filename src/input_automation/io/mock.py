"""Mock IO providers: programmable state without /dev/input or /dev/uinput.

Headless testing seam for the evdev (T7) and uinput (T8) providers: neither
class opens a real device.

- :class:`MockEvdevInputProvider` drives ``INPUT`` block pins from an
  in-memory key/button/axis state (:meth:`press`, :meth:`release`,
  :meth:`set_axis`).
- :class:`MockUinputOutputProvider` records every event an ``OUTPUT`` block
  would emit instead of creating a virtual uinput device.

Both keep the ``read_inputs(graph)`` / ``write_outputs(graph)`` callable
interface injected into :class:`input_automation.engine.ScanExecutor`, so a
full scan cycle runs end to end on a headless box with no hardware and no
privileges. The capture side already ships
:class:`input_automation.capture.mock.MockCaptureBackend` (T9).
"""

from __future__ import annotations

from evdev import ecodes

from input_automation.io.evdev import DeviceState, EvdevInputProvider
from input_automation.io.uinput import UinputOutputProvider

__all__ = ["MockEvdevInputProvider", "MockUinputOutputProvider"]


class _MockDeviceState:
    """Programmable state of one simulated evdev device."""

    def __init__(self) -> None:
        self.pressed: set[int] = set()
        self.axes: dict[int, int] = {}


class _AxisValue:
    """evdev absinfo surface: ``.value`` holds the axis position."""

    def __init__(self, value: int) -> None:
        self.value = value


class _MockDeviceReader:
    """evdev-like device surface backed by :class:`_MockDeviceState`."""

    def __init__(self, state: _MockDeviceState) -> None:
        self._state = state

    def active_keys(self) -> set[int]:
        return set(self._state.pressed)

    def absinfo(self, axis: int) -> _AxisValue:
        return _AxisValue(self._state.axes.get(axis, 0))

    def capabilities(self, verbose: bool = False) -> dict[int, list[int]]:
        del verbose
        caps: dict[int, list[int]] = {}
        if self._state.pressed:
            caps[ecodes.EV_KEY] = sorted(self._state.pressed)
        if self._state.axes:
            caps[ecodes.EV_ABS] = sorted(self._state.axes)
        return caps

    def close(self) -> None:
        return None


class MockEvdevInputProvider(EvdevInputProvider):
    """Drive ``INPUT`` blocks from a programmable key/button/axis state.

    Call :meth:`press`, :meth:`release` or :meth:`set_axis` between scan
    cycles to simulate device events; the next cycle picks the changes up.
    No evdev device is ever opened.
    """

    def __init__(self) -> None:
        self._states: dict[str, _MockDeviceState] = {}
        super().__init__(open_device_fn=self._open_mock)

    def _open_mock(self, path: str) -> _MockDeviceReader:
        state = self._states.setdefault(path, _MockDeviceState())
        return _MockDeviceReader(state)

    def _device_state(self, path: str) -> DeviceState | None:
        """Build fresh state every cycle so presses/releases apply immediately."""
        state = self._states.get(path)
        if state is None:
            return None
        device = self._open_mock(path)
        return DeviceState(
            device=device,
            key_codes=frozenset(state.pressed),
            abs_codes=frozenset(state.axes),
        )

    def press(self, device_path: str, code: int) -> None:
        """Simulate ``code`` going down on ``device_path``."""
        self._state(device_path).pressed.add(int(code))

    def release(self, device_path: str, code: int) -> None:
        """Simulate ``code`` coming up on ``device_path``."""
        self._state(device_path).pressed.discard(int(code))

    def set_axis(self, device_path: str, code: int, value: int) -> None:
        """Simulate absolute axis ``code`` reporting ``value`` on ``device_path``."""
        self._state(device_path).axes[int(code)] = int(value)

    def clear(self) -> None:
        """Drop all simulated device state."""
        self._states.clear()

    def _state(self, device_path: str) -> _MockDeviceState:
        return self._states.setdefault(device_path, _MockDeviceState())


class _MockUinputDevice:
    """Recording virtual device: appends every emit() instead of driving the kernel."""

    def __init__(self, name: str, specs: list[tuple[int, object]]) -> None:
        self.name = name
        self.specs = specs
        self.events: list[tuple[tuple[int, int], int, bool]] = []
        self.closed = False

    def emit(self, event: tuple[int, int], value: int, syn: bool = True) -> None:
        self.events.append((event, value, syn))

    def close(self) -> None:
        self.closed = True


class MockUinputOutputProvider(UinputOutputProvider):
    """Record ``OUTPUT`` block events instead of creating uinput devices.

    Every event the real provider would emit is collected in :attr:`events`;
    inspect :meth:`device_events` per virtual device name. No /dev/uinput is
    ever opened.
    """

    def __init__(self) -> None:
        super().__init__(open_device_fn=self._open_mock)

    def _open_mock(
        self, name: str, specs: list[tuple[int, object]]
    ) -> _MockUinputDevice:
        return _MockUinputDevice(name, specs)

    @property
    def events(self) -> list[tuple[tuple[int, int], int, bool]]:
        """Every emitted event, devices in creation order."""
        collected: list[tuple[tuple[int, int], int, bool]] = []
        for device in self._devices.values():
            if isinstance(device, _MockUinputDevice):
                collected.extend(device.events)
        return collected

    def device_events(self, name: str) -> list[tuple[tuple[int, int], int, bool]]:
        """Events emitted on the virtual device ``name``."""
        device = self._devices.get(name)
        if isinstance(device, _MockUinputDevice):
            return list(device.events)
        return []

    def clear(self) -> None:
        """Drop all recorded events."""
        for device in self._devices.values():
            if isinstance(device, _MockUinputDevice):
                device.events.clear()
