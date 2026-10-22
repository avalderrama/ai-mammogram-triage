"""Splitting images that place several views side by side, at any resolution."""
import numpy as np
import pytest

from mammo_infrastructure.ml.panels import equal_panels, find_panels


def photographed_double_line(scale: int) -> np.ndarray:
    """Two views separated by a viewer's double line, photographed off a monitor: the line's soft edges get wider with
    the resolution. Knots at scale 1, about the size of a small screenshot; scale 3 is a phone photo."""
    xs = np.array([0, 440, 443, 445, 448, 450, 454, 459, 464, 469, 900], np.float32)
    ys = np.array([150, 150, 185, 185, 100, 100, 215, 215, 150, 150, 150], np.float32)
    x = np.arange(900 * scale, dtype=np.float32) / scale
    return np.repeat(np.interp(x, xs, ys)[None, :], 1000 * scale, 0).astype(np.uint8)


@pytest.mark.parametrize("scale", [1, 2, 3])
def test_a_photographed_separator_line_is_found_at_any_resolution(scale):
    # At scale 3 the soft edges are wider than the 6 px the line rule compares against; the rule must still see them.
    left, right = find_panels(photographed_double_line(scale))
    assert 430 * scale <= left[1] < right[0] <= 475 * scale
    assert left[0] == 0 and right[1] == 900 * scale


def test_a_white_gap_separates_two_views():
    image = np.full((1000, 900), 40, np.uint8)
    image[:, 430:470] = 255
    assert find_panels(image) == [(0, 430), (470, 900)]


def test_a_tall_image_without_separators_stays_whole():
    assert find_panels(np.full((3000, 2400), 90, np.uint8)) == [(0, 2400)]


def test_equal_panels_splits_the_width_evenly():
    assert equal_panels(900, 2) == [(0, 450), (450, 900)]
