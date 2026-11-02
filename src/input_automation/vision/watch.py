"""Visual trigger engine: fire callbacks on rising-edge frame conditions.

A :class:`WatchRule` describes a rectangular region of a BGR frame and a
condition to evaluate inside it (currently ``color``). The
:class:`VisualTriggerEngine` evaluates every registered rule on each
incoming frame and fires the rule's callback on the *rising edge* of the
condition (false → true). A rule that stays true does not re-fire until
its condition drops back to false.

Headless-testable: the clock is injectable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np

__all__ = ["WatchRule", "VisualTriggerEngine"]

Region = tuple[int, int, int, int]  # (x, y, w, h)
Clock = Callable[[], float]
TriggerCallback = Callable[[str], None]

WatchKind = Literal["color"]


@dataclass
class WatchRule:
    """One visual condition to watch on every frame.

    ``region`` is ``(x, y, w, h)`` in frame coordinates. ``kind`` selects
    the condition evaluator; for ``"color"`` the evaluator checks that the
    fraction of pixels within the BGR range defined by
    ``params["lower"]``/``params["upper"]`` is at least ``threshold``.
    """

    name: str
    region: Region
    kind: WatchKind
    params: dict[str, Any]
    threshold: float
    debounce_ms: float = 0.0  # suppress re-fires within this window (0 = off)

    def evaluate(self, frame: np.ndarray) -> bool:
        """Check whether this rule's condition holds in ``frame``."""
        if self.kind != "color":
            raise ValueError(f"unknown watch kind {self.kind!r}")
        x, y, w, h = self.region
        cropped = frame[y : y + h, x : x + w]
        if cropped.size == 0:
            return False
        lower = np.array(self.params["lower"], dtype=np.uint8)
        upper = np.array(self.params["upper"], dtype=np.uint8)
        mask = cv2.inRange(cropped, lower, upper)
        total = float(cropped.shape[0] * cropped.shape[1])
        if total == 0:
            return False
        fraction = mask.sum() / (255.0 * total)
        return bool(fraction >= self.threshold)


@dataclass
class VisualTriggerEngine:
    """Evaluate :class:`WatchRule`s on frames and fire rising-edge triggers."""

    clock: Clock = field(default=time.time)
    rules: dict[str, WatchRule] = field(default_factory=dict)
    callbacks: dict[str, TriggerCallback] = field(default_factory=dict)
    _active: dict[str, bool] = field(default_factory=dict)
    _last_fire: dict[str, float] = field(default_factory=dict)

    def watch(self, rule: WatchRule, callback: TriggerCallback) -> None:
        """Register a rule and the callback to fire on its rising edge."""
        self.rules[rule.name] = rule
        self.callbacks[rule.name] = callback
        self._active[rule.name] = False
        self._last_fire[rule.name] = float("-inf")

    def handle_frame(self, frame: np.ndarray) -> None:
        """Evaluate every registered rule against ``frame``."""
        for name, rule in self.rules.items():
            active = rule.evaluate(frame)
            was_active = self._active[name]
            self._active[name] = active
            if active and not was_active:
                now = self.clock()
                if now - self._last_fire[name] >= rule.debounce_ms / 1000.0:
                    self._last_fire[name] = now
                    self.callbacks[name](name)
