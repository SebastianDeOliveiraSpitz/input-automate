"""Tests for the capture backend factory (T13) and frame fallback (picker fix)."""

import numpy as np
import pytest

from input_automation.capture.base import CaptureError, Frame
from input_automation.capture.factory import (
    BACKEND_NAMES,
    create_backend,
)


def _frame(size: tuple[int, int] = (64, 64)) -> Frame:
    return Frame(image=np.zeros((size[1], size[0], 3), dtype=np.uint8), ts=0.0)


class TestBackendFactory:
    def test_mock_backend(self) -> None:
        from input_automation.capture.mock import MockCaptureBackend

        backend = create_backend("mock")
        assert isinstance(backend, MockCaptureBackend)

    def test_mutter_backend_constructs_without_d_bus_calls(self) -> None:
        """Constructing the mutter backend must not touch D-Bus/GStreamer."""
        from input_automation.capture.screencast import ScreencastBackend

        backend = create_backend("mutter")
        assert isinstance(backend, ScreencastBackend)
        assert not backend._running

    def test_portal_backend(self) -> None:
        from input_automation.capture.screencast import ScreencastBackend

        backend = create_backend("portal")
        assert isinstance(backend, ScreencastBackend)

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown capture backend"):
            create_backend("x11grab")

    def test_names_tuple(self) -> None:
        assert BACKEND_NAMES == ("mock", "mutter", "portal")


class _ScriptedBackend:
    """Fake backend whose grab() behaves per script."""

    def __init__(self, name: str, result: Frame | Exception) -> None:
        self._name = name
        self._result = result

    def grab(self, timeout: float = 10.0) -> Frame:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class TestGrabFirstFrame:
    def test_mutter_failure_falls_back_to_mock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import input_automation.capture.factory as factory

        calls: list[str] = []

        def fake_create_backend(name: str) -> _ScriptedBackend:
            calls.append(name)
            result: Frame | Exception = (
                CaptureError("no GNOME session")
                if name == "mutter"
                else _frame()
            )
            return _ScriptedBackend(name, result)

        monkeypatch.setattr(factory, "create_backend", fake_create_backend)
        frame = factory.grab_first_frame()
        assert calls == ["mutter", "mock"]
        assert frame.image.shape == (64, 64, 3)

    def test_mutter_success_skips_mock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import input_automation.capture.factory as factory

        calls: list[str] = []

        def fake_create_backend(name: str) -> _ScriptedBackend:
            calls.append(name)
            return _ScriptedBackend(name, _frame((32, 32)))

        monkeypatch.setattr(factory, "create_backend", fake_create_backend)
        frame = factory.grab_first_frame()
        assert calls == ["mutter"]
        assert frame.image.shape == (32, 32, 3)

    def test_all_backends_fail_raises_capture_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import input_automation.capture.factory as factory

        def fake_create_backend(name: str) -> _ScriptedBackend:
            return _ScriptedBackend(name, CaptureError(f"{name} down"))

        monkeypatch.setattr(factory, "create_backend", fake_create_backend)
        with pytest.raises(
            CaptureError, match="no capture backend produced a frame"
        ):
            factory.grab_first_frame()
