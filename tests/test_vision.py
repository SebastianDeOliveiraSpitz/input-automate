"""Tests for the vision primitives (pure OpenCV functions, headless)."""

import numpy as np
import pytest

from input_automation.vision.primitives import (
    TemplateMatchResult,
    bar_fill_fraction,
    color_fraction,
    color_mask,
    region_crop,
    template_match,
)


@pytest.fixture
def white_block_image() -> np.ndarray:
    """8x8 black image with a solid white 4x4 block at (x=2, y=2)."""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img[2:6, 2:6] = 255
    return img


class TestColorMask:
    def test_white_block_is_masked(self, white_block_image: np.ndarray) -> None:
        mask = color_mask(white_block_image, lower=(200, 200, 200), upper=(255, 255, 255))
        assert mask.dtype == np.bool_
        assert mask.shape == (8, 8)
        assert mask[3, 3]  # inside the block
        assert not mask[0, 0]  # outside the block

    def test_mask_matches_block_extent(self, white_block_image: np.ndarray) -> None:
        mask = color_mask(white_block_image, lower=(200, 200, 200), upper=(255, 255, 255))
        assert mask.sum() == 16
        assert mask[2:6, 2:6].all()
        assert not mask[:2].any() and not mask[6:].any()

    def test_inclusive_bounds(self) -> None:
        """inRange bounds are inclusive: values exactly at lower/upper match."""
        img = np.zeros((1, 3, 3), dtype=np.uint8)
        img[0, 0] = 100
        img[0, 1] = 101
        img[0, 2] = 99
        mask = color_mask(img, lower=(100, 100, 100), upper=(100, 100, 100))
        assert mask[0, 0] and not mask[0, 1] and not mask[0, 2]

    def test_empty_mask_when_nothing_matches(self) -> None:
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        mask = color_mask(img, lower=(10, 10, 10), upper=(255, 255, 255))
        assert not mask.any()


class TestColorFraction:
    def test_half_white_image_is_half(self, white_block_image: np.ndarray) -> None:
        """Half the pixels white, half black → fraction 0.5 for a white range."""
        img = np.zeros((8, 4, 3), dtype=np.uint8)
        img[:4] = 255
        white = ((200, 200, 200), (255, 255, 255))
        assert color_fraction(img, *white) == pytest.approx(0.5)

    def test_full_block_is_one(self, white_block_image: np.ndarray) -> None:
        block = white_block_image[2:6, 2:6]
        white = ((200, 200, 200), (255, 255, 255))
        assert color_fraction(block, *white) == pytest.approx(1.0)

    def test_nothing_matches_is_zero(self) -> None:
        img = np.zeros((4, 4, 3), dtype=np.uint8)
        assert color_fraction(img, lower=(10, 10, 10), upper=(255, 255, 255)) == 0.0


class TestBarFillFraction:
    """Progress-bar measurement: which fraction of the bar's width is filled."""

    WHITE = ((200, 200, 200), (255, 255, 255))

    def test_partial_bar(self) -> None:
        """10-wide bar, fill covers the first 4 columns → 0.4."""
        bar = np.zeros((4, 10, 3), dtype=np.uint8)
        bar[:, :4] = 255
        assert bar_fill_fraction(bar, *self.WHITE) == pytest.approx(0.4)

    def test_full_bar(self) -> None:
        bar = np.full((4, 10, 3), 255, dtype=np.uint8)
        assert bar_fill_fraction(bar, *self.WHITE) == pytest.approx(1.0)

    def test_empty_bar(self) -> None:
        bar = np.zeros((4, 10, 3), dtype=np.uint8)
        assert bar_fill_fraction(bar, *self.WHITE) == pytest.approx(0.0)

    def test_minority_filled_column_not_counted(self) -> None:
        """A column that is only 1/4 filled (edge blur) does not count as filled."""
        bar = np.zeros((4, 10, 3), dtype=np.uint8)
        bar[:, :4] = 255
        bar[0, 4] = 255  # single pixel of 4 → 25% < 50%
        assert bar_fill_fraction(bar, *self.WHITE) == pytest.approx(0.4)

    def test_half_filled_column_counts(self) -> None:
        """A column with exactly half its pixels filled counts as filled."""
        bar = np.zeros((4, 10, 3), dtype=np.uint8)
        bar[:, :4] = 255
        bar[:2, 4] = 255  # 2 of 4 pixels → 50%
        assert bar_fill_fraction(bar, *self.WHITE) == pytest.approx(0.5)


@pytest.fixture
def gradient_image() -> np.ndarray:
    """6x8 BGR image where each pixel encodes (row, col) — easy crop math.

    pixel[row, col] = (row, col, 7)
    """
    rows, cols = 6, 8
    img = np.zeros((rows, cols, 3), dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            img[r, c] = (r, c, 7)
    return img


@pytest.fixture
def canvas_with_target() -> tuple[np.ndarray, np.ndarray, int, int]:
    """10x10 black canvas with a solid white 4x4 target pasted at (x=3, y=2)."""
    canvas = np.zeros((10, 10, 3), dtype=np.uint8)
    target = np.full((4, 4, 3), 255, dtype=np.uint8)
    canvas[2:6, 3:7] = target
    return canvas, target, 3, 2


class TestRegionCrop:
    def test_crops_interior_region(self, gradient_image: np.ndarray) -> None:
        crop = region_crop(gradient_image, x=2, y=1, w=3, h=2)
        # 2 rows tall, 3 cols wide, pixel (1,2) at the top-left corner
        assert crop.shape == (2, 3, 3)
        assert crop[0, 0].tolist() == [1, 2, 7]
        assert crop[-1, -1].tolist() == [2, 4, 7]

    def test_crops_full_image_region(self, gradient_image: np.ndarray) -> None:
        crop = region_crop(gradient_image, x=0, y=0, w=8, h=6)
        assert crop.shape == (6, 8, 3)
        np.testing.assert_array_equal(crop, gradient_image)

    def test_crop_is_a_copy(self, gradient_image: np.ndarray) -> None:
        """Mutating the crop must not touch the source image."""
        crop = region_crop(gradient_image, x=0, y=0, w=4, h=4)
        crop[:] = 255
        assert gradient_image[0, 0].tolist() == [0, 0, 7]

    @pytest.mark.parametrize(
        ("x", "y", "w", "h"),
        [(-1, 0, 2, 2), (0, -1, 2, 2), (7, 0, 2, 2), (0, 5, 2, 2), (0, 0, 0, 2)],
    )
    def test_out_of_bounds_region_raises(
        self, gradient_image: np.ndarray, x: int, y: int, w: int, h: int
    ) -> None:
        with pytest.raises(ValueError, match="positive|outside"):
            region_crop(gradient_image, x=x, y=y, w=w, h=h)


class TestTemplateMatch:
    def test_finds_exact_target_location(
        self, canvas_with_target: tuple[np.ndarray, np.ndarray, int, int]
    ) -> None:
        canvas, target, x, y = canvas_with_target
        result = template_match(canvas, target)
        assert isinstance(result, TemplateMatchResult)
        assert (result.x, result.y) == (x, y)
        assert result.score == pytest.approx(1.0, abs=1e-3)

    def test_target_coordinates_are_top_left_corner(
        self, canvas_with_target: tuple[np.ndarray, np.ndarray, int, int]
    ) -> None:
        """The reported (x, y) is the target's top-left, not its center."""
        canvas, target, x, y = canvas_with_target
        result = template_match(canvas, target)
        assert (result.x, result.y) == (x, y)
        center_x, center_y = x + target.shape[1] // 2, y + target.shape[0] // 2
        assert (result.x, result.y) != (center_x, center_y)

    def test_unrelated_template_scores_low(self) -> None:
        """A template with no counterpart in the image yields a low score."""
        white = np.full((10, 10, 3), 255, dtype=np.uint8)
        checker = np.zeros((4, 4, 3), dtype=np.uint8)
        checker[::2, ::2] = 255  # a checkerboard can't appear in a solid image
        result = template_match(white, checker)
        assert result.score < 0.5

    def test_first_of_identical_targets_reported(
        self, canvas_with_target: tuple[np.ndarray, np.ndarray, int, int]
    ) -> None:
        canvas, target, x, y = canvas_with_target
        canvas[6:10, 6:10] = target  # second identical target at (6, 6)
        result = template_match(canvas, target)
        assert (result.x, result.y) in {(x, y), (6, 6)}
        assert result.score == pytest.approx(1.0, abs=1e-3)

    def test_template_larger_than_image_raises(
        self, canvas_with_target: tuple[np.ndarray, np.ndarray, int, int]
    ) -> None:
        canvas, _, _, _ = canvas_with_target
        huge = np.full((16, 16, 3), 255, dtype=np.uint8)
        with pytest.raises(ValueError, match="larger than the image"):
            template_match(canvas, huge)
