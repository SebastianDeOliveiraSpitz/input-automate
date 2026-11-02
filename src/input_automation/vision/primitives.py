"""Pure OpenCV image primitives for the visual trigger layer.

All functions take and return plain numpy BGR arrays (as produced by
:class:`~input_automation.capture.base.Frame`) and are side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

__all__ = [
    "TemplateMatchResult",
    "bar_fill_fraction",
    "color_fraction",
    "color_mask",
    "region_crop",
    "template_match",
]


@dataclass(frozen=True)
class TemplateMatchResult:
    """Best template match: top-left corner and normalized similarity."""

    x: int
    y: int
    score: float


def region_crop(image: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
    """Crop ``w``x``h`` pixels at ``(x, y)`` from a BGR image.

    Raises ValueError if the region is out of bounds or degenerate.
    """
    height, width = image.shape[:2]
    if w <= 0 or h <= 0:
        raise ValueError(f"region size must be positive, got w={w}, h={h}")
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise ValueError(
            f"region (x={x}, y={y}, w={w}, h={h}) outside image {width}x{height}"
        )
    return image[y : y + h, x : x + w].copy()


def color_mask(
    image: np.ndarray,
    lower: tuple[int, int, int],
    upper: tuple[int, int, int],
) -> np.ndarray:
    """Boolean mask of BGR pixels within the inclusive ``[lower, upper]`` range."""
    mask = cv2.inRange(
        image,
        np.array(lower, dtype=np.uint8),
        np.array(upper, dtype=np.uint8),
    )
    return mask > 0


def color_fraction(
    image: np.ndarray,
    lower: tuple[int, int, int],
    upper: tuple[int, int, int],
) -> float:
    """Fraction of BGR pixels within the inclusive ``[lower, upper]`` range."""
    return float(color_mask(image, lower, upper).mean())


def bar_fill_fraction(
    image: np.ndarray,
    lower: tuple[int, int, int],
    upper: tuple[int, int, int],
) -> float:
    """Fraction of a progress bar's width that is filled.

    A column counts as filled when at least half of its pixels lie within
    the inclusive ``[lower, upper]`` color range.
    """
    per_column = color_mask(image, lower, upper).mean(axis=0)
    return float((per_column >= 0.5).mean())


def template_match(image: np.ndarray, template: np.ndarray) -> TemplateMatchResult:
    """Locate ``template`` inside ``image``; return the best match.

    The reported ``(x, y)`` is the template's top-left corner in image
    coordinates and ``score`` is the similarity in ``[0, 1]`` (1.0 = exact
    match). Uses TM_SQDIFF_NORMED: unlike the correlation methods it stays
    well-defined for solid-color (zero-variance) templates.
    """
    if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1]:
        raise ValueError(
            f"template {template.shape[1]}x{template.shape[0]} is larger than "
            f"the image {image.shape[1]}x{image.shape[0]}"
        )
    result = cv2.matchTemplate(image, template, cv2.TM_SQDIFF_NORMED)
    sqdiff, _, location, _ = cv2.minMaxLoc(result)
    return TemplateMatchResult(
        x=location[0], y=location[1], score=float(max(0.0, 1.0 - sqdiff))
    )
