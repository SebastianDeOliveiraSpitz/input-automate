"""Tests for the capture backend factory (T13)."""

import pytest

from input_automation.capture.factory import create_backend


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
