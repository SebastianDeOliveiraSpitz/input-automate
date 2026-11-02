"""Tests for the visual trigger engine: watch rules on frames, headless."""

import numpy as np

from input_automation.vision.watch import (
    VisualTriggerEngine,
    WatchRule,
)


def frame_with_white_block(x: int, y: int, w: int, h: int, size: int = 10) -> np.ndarray:
    """`size`x`size` black frame with a white w x h block at (x, y)."""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[y : y + h, x : x + w] = 255
    return img


WHITE = ((200, 200, 200), (255, 255, 255))


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestColorWatchRule:
    def test_fires_when_condition_becomes_true(self) -> None:
        """Black frames produce nothing; a white block appearing fires once."""
        clock = FakeClock()
        engine = VisualTriggerEngine(clock=clock)
        fired: list[str] = []
        rule = WatchRule(
            name="block-appeared",
            region=(2, 2, 4, 4),
            kind="color",
            params={"lower": (200, 200, 200), "upper": (255, 255, 255)},
            threshold=0.9,
        )
        engine.watch(rule, lambda name: fired.append(name))

        engine.handle_frame(frame_with_white_block(0, 0, 0, 0))  # black frame
        assert fired == []

        engine.handle_frame(frame_with_white_block(2, 2, 4, 4))  # block appears
        assert fired == ["block-appeared"]

    def test_stays_silent_while_condition_remains_true(self) -> None:
        """Rising edge only: consecutive true frames do not re-fire."""
        clock = FakeClock()
        engine = VisualTriggerEngine(clock=clock)
        fired: list[str] = []
        rule = WatchRule(
            name="block",
            region=(2, 2, 4, 4),
            kind="color",
            params={"lower": (200, 200, 200), "upper": (255, 255, 255)},
            threshold=0.9,
        )
        engine.watch(rule, lambda name: fired.append(name))

        block = frame_with_white_block(2, 2, 4, 4)
        engine.handle_frame(block)
        clock.advance(5.0)
        engine.handle_frame(block)  # still true — no new edge
        assert fired == ["block"]

    def test_fires_again_after_condition_drops_and_rises(self) -> None:
        """False → true edge fires again once the condition reset."""
        clock = FakeClock()
        engine = VisualTriggerEngine(clock=clock)
        fired: list[str] = []
        rule = WatchRule(
            name="blink",
            region=(2, 2, 4, 4),
            kind="color",
            params={"lower": (200, 200, 200), "upper": (255, 255, 255)},
            threshold=0.9,
        )
        engine.watch(rule, lambda name: fired.append(name))

        on = frame_with_white_block(2, 2, 4, 4)
        off = frame_with_white_block(0, 0, 0, 0)
        engine.handle_frame(on)  # fires (1st)
        clock.advance(1.0)
        engine.handle_frame(off)  # resets
        clock.advance(1.0)
        engine.handle_frame(on)  # fires (2nd)
        assert fired == ["blink", "blink"]


class TestDebounce:
    def test_rapid_flicker_within_window_fires_once(self) -> None:
        """Condition flickering rapidly fires only the first edge."""
        clock = FakeClock()
        engine = VisualTriggerEngine(clock=clock)
        fired: list[str] = []
        rule = WatchRule(
            name="flicker",
            region=(2, 2, 4, 4),
            kind="color",
            params={"lower": (200, 200, 200), "upper": (255, 255, 255)},
            threshold=0.9,
            debounce_ms=500,
        )
        engine.watch(rule, lambda name: fired.append(name))

        on = frame_with_white_block(2, 2, 4, 4)
        off = frame_with_white_block(0, 0, 0, 0)
        engine.handle_frame(on)  # fires (t=0)
        clock.advance(0.05)
        engine.handle_frame(off)
        clock.advance(0.05)
        engine.handle_frame(on)  # would fire, but inside debounce window
        clock.advance(0.05)
        engine.handle_frame(off)
        clock.advance(0.05)
        engine.handle_frame(on)  # still inside 500 ms window
        assert fired == ["flicker"]

    def test_fires_again_after_window_elapsed(self) -> None:
        """After the debounce window passes, a new rising edge fires."""
        clock = FakeClock()
        engine = VisualTriggerEngine(clock=clock)
        fired: list[str] = []
        rule = WatchRule(
            name="debounced",
            region=(2, 2, 4, 4),
            kind="color",
            params={"lower": (200, 200, 200), "upper": (255, 255, 255)},
            threshold=0.9,
            debounce_ms=100,
        )
        engine.watch(rule, lambda name: fired.append(name))

        on = frame_with_white_block(2, 2, 4, 4)
        off = frame_with_white_block(0, 0, 0, 0)
        engine.handle_frame(on)  # fires (t=0)
        clock.advance(0.05)
        engine.handle_frame(off)
        engine.handle_frame(on)  # suppressed (t=0.05)
        clock.advance(0.2)  # now 0.25s since fire > 100ms window
        engine.handle_frame(off)
        engine.handle_frame(on)  # fires again
        assert fired == ["debounced", "debounced"]
