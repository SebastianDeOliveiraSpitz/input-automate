"""IO layer: evdev input, uinput output, PipeWire capture, OpenCV primitives, mock providers."""

from input_automation.io.capture import CaptureInputProvider
from input_automation.io.evdev import EvdevInputProvider
from input_automation.io.mock import MockEvdevInputProvider, MockUinputOutputProvider
from input_automation.io.uinput import UinputOutputProvider

__all__ = [
    "CaptureInputProvider",
    "EvdevInputProvider",
    "MockEvdevInputProvider",
    "MockUinputOutputProvider",
    "UinputOutputProvider",
]
