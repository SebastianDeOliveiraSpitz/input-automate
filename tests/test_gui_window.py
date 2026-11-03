"""Tests for the PySide6 app shell (T14). Runs with QT_QPA_PLATFORM=offscreen."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QSystemTrayIcon  # noqa: E402

from input_automation.devices.discovery import DeviceInfo  # noqa: E402
from input_automation.gui.window import MainWindow  # noqa: E402
from input_automation.macros.models import (  # noqa: E402
    Action,
    Macro,
    StepKind,
    Trigger,
    TriggerKind,
)

VALID_MACRO = Macro(
    name="demo",
    description="test",
    triggers=[Trigger(kind=TriggerKind.KEY, code="F10")],
    steps=[
        Action(kind=StepKind.ACTION, op="tap", target="a"),
        Action(kind=StepKind.ACTION, op="wait", ms=5),
    ],
)

MACRO_DIR = "m.yaml"


def _fake_device(name: str = "Fake Keyboard") -> DeviceInfo:
    return DeviceInfo(
        path="/dev/input/event0", name=name, phys="", kinds=frozenset({"keyboard"})
    )


def _providers() -> dict[str, list[object]]:
    return {
        "devices": [_fake_device()],
        "macros": [(Path(MACRO_DIR), VALID_MACRO)],
    }


@pytest.fixture
def window(qtbot):  # type: ignore[no-untyped-def]
    """MainWindow with fake providers and no real devices or macros."""
    providers = _providers()
    win = MainWindow(
        device_provider=lambda *a, **kw: providers["devices"],  # type: ignore[arg-type,return-value]
        macro_provider=lambda directory: providers["macros"],
        macro_dir=Path(MACRO_DIR),
    )
    qtbot.addWidget(win)
    return win, providers


class TestMainWindow:
    def test_lists_devices_and_macros(self, window) -> None:  # type: ignore[no-untyped-def]
        win, _ = window
        assert win.windowTitle() == "input-automation"
        assert win._device_list.count() == 1
        assert "event0" in win._device_list.item(0).text()
        assert win._macro_list.count() == 1
        assert "demo" in win._macro_list.item(0).text()

    def test_refresh_buttons_repopulate(self, window) -> None:  # type: ignore[no-untyped-def]
        win, providers = window
        providers["devices"].append(_fake_device("Fake Mouse"))
        providers["macros"].append((Path("n.yaml"), VALID_MACRO.model_copy(deep=True)))
        win.refresh_devices()
        win.refresh_macros()
        assert win._device_list.count() == 2
        assert win._macro_list.count() == 2

    def test_provider_error_shows_in_status_bar(self, qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        def boom(*args: object, **kwargs: object) -> list[object]:
            raise PermissionError("no /dev/input access")

        win = MainWindow(device_provider=boom)  # type: ignore[arg-type]
        qtbot.addWidget(win)
        assert "PermissionError" in win.statusBar().currentMessage()

    def test_test_run_dry_no_selection(self, window) -> None:  # type: ignore[no-untyped-def]
        win, _ = window
        win.test_run_selected()
        assert "select" in win.statusBar().currentMessage().lower()

    def test_test_run_dry_selected(self, window) -> None:  # type: ignore[no-untyped-def]
        win, _ = window
        win._macro_list.setCurrentRow(0)
        win.test_run_selected()
        assert "dry run ok" in win.statusBar().currentMessage()

    def test_tray_icon_builds_on_offscreen(self, window) -> None:  # type: ignore[no-untyped-def]
        win, _ = window
        if QSystemTrayIcon.isSystemTrayAvailable():
            assert win._tray is not None
        else:
            assert win._tray is None


class TestCaptureBackendFallback:
    def _patch_factory(self, monkeypatch, order: list[str]) -> dict[str, object]:
        """Replace create_backend with scripted fakes; returns start log."""
        started: list[str] = []

        class FakeBackend:
            def __init__(self, name: str) -> None:
                self._name = name
                self.stopped = False

            def start(self) -> None:
                order.append(self._name)
                if self._name == "mutter":
                    raise RuntimeError("no GNOME session")
                started.append(self._name)

            def stop(self) -> None:
                self.stopped = True

        import input_automation.gui.window as window_mod

        monkeypatch.setattr(window_mod, "create_backend", FakeBackend)
        return {"started": started}

    def test_prefers_mutter_falls_back_to_mock(
        self, qtbot, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        order: list[str] = []
        self._patch_factory(monkeypatch, order)
        providers = _providers()
        win = MainWindow(
            device_provider=lambda *a, **kw: providers["devices"],  # type: ignore[arg-type,return-value]
            macro_provider=lambda directory: providers["macros"],
            macro_dir=Path(MACRO_DIR),
        )
        qtbot.addWidget(win)
        backend = win._start_capture_backend()
        assert order == ["mutter", "mock"]
        assert backend.stopped is False
        backend.stop()

    def test_mutter_success_skips_mock(
        self, qtbot, monkeypatch  # type: ignore[no-untyped-def]
    ) -> None:
        order: list[str] = []
        self._patch_factory(monkeypatch, order)
        providers = _providers()
        win = MainWindow(
            device_provider=lambda *a, **kw: providers["devices"],  # type: ignore[arg-type,return-value]
            macro_provider=lambda directory: providers["macros"],
            macro_dir=Path(MACRO_DIR),
        )
        qtbot.addWidget(win)
        # only the mutter failure path raises in the fake; patch start to pass
        import input_automation.gui.window as window_mod

        factory = window_mod.create_backend

        class PassingMutter(factory):  # type: ignore[misc,valid-type]
            def start(self) -> None:
                order.append(self._name)

        window_mod.create_backend = (  # type: ignore[assignment]
            lambda name: PassingMutter(name)
        )
        win._start_capture_backend()
        assert order == ["mutter"]
