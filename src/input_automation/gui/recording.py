"""Record the next physical key/button press from evdev devices.

Headless-testable: the listener factory is injectable, so tests can drive
the recorder with synthetic events instead of real /dev/input devices.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from input_automation.devices.listener import DeviceListener, InputEvent

__all__ = ["InputRecorder"]


class InputRecorder(QObject):
    """Capture the next EV_KEY press and report its evdev code name.

    Watches ``paths`` (all discoverable devices when ``None``) and emits
    :attr:`recorded` with the first key/button press's code name, e.g.
    ``"KEY_Q"`` or ``"BTN_LEFT"``. Mouse clicks are EV_KEY presses too, so a
    click is recorded as ``BTN_LEFT``/``BTN_RIGHT``/… . Releases, repeats
    and axis/motion events are ignored.

    The recorder stops itself after the first press; call :meth:`start`
    again to capture another event.
    """

    recorded = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        *,
        listener_factory: Callable[..., DeviceListener] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._listener_factory = listener_factory or DeviceListener
        self._listener: DeviceListener | None = None

    def start(self, paths: list[str] | None = None) -> None:
        """Begin watching ``paths`` (``None`` = every discoverable device)."""
        self.stop()
        try:
            self._listener = self._listener_factory(
                paths=paths, callback=self._on_event
            )
            self._listener.start()
        except OSError as exc:  # pragma: no cover - device layer already logged
            self.failed.emit(f"cannot start input capture: {exc}")

    def stop(self) -> None:
        """Stop watching and close any open device fds."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_event(self, event: InputEvent) -> None:
        if event.etype == "key" and event.action == "press":
            self.stop()
            self.recorded.emit(event.code_name)
