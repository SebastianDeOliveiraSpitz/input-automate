"""IO layer: evdev input, uinput output, PipeWire capture, OpenCV primitives, mock providers."""

from input_automation.io.evdev import EvdevInputProvider

__all__ = ["EvdevInputProvider"]
