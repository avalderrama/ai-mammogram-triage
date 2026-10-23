"""Splitting images that place several views side by side, at any resolution."""
import numpy as np
import pytest

from mammo_infrastructure.ml.panels import equal_boxes, equal_panels, find_panel_boxes, find_panels


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


def test_a_2x2_figure_gives_four_views_left_to_right_and_top_to_bottom():
    image = np.full((1000, 900), 90, np.uint8)
    image[:, 448:452] = 245  # vertical line, full height
    image[498:502, :] = 245  # horizontal line, full width
    assert find_panel_boxes(image) == [(0, 0, 448, 498), (452, 0, 900, 498), (0, 502, 448, 1000),
                                       (452, 502, 900, 1000)]


def test_a_grid_whose_vertical_lines_stop_at_the_horizontal_one():
    image = np.full((1000, 900), 90, np.uint8)
    image[498:502, :] = 245
    image[:498, 300:304] = 245  # top row split in two
    image[502:, 600:604] = 245  # bottom row split elsewhere
    boxes = find_panel_boxes(image)
    assert [(x0, y0) for x0, y0, _, _ in boxes] == [(0, 0), (304, 0), (0, 502), (604, 502)]


def test_views_side_by_side_and_a_single_view_are_unchanged():
    two = np.full((1000, 900), 90, np.uint8)
    two[:, 448:452] = 245
    assert find_panel_boxes(two) == [(0, 0, 448, 1000), (452, 0, 900, 1000)]
    assert find_panel_boxes(np.full((1000, 900), 90, np.uint8)) == [(0, 0, 900, 1000)]


def test_equal_boxes_side_by_side_or_in_a_2x2_grid():
    assert equal_boxes((1000, 900), 2) == [(0, 0, 450, 1000), (450, 0, 900, 1000)]
    assert equal_boxes((1000, 900), 4, grid=True) == [(0, 0, 450, 500), (450, 0, 900, 500), (0, 500, 450, 1000),
                                                     (450, 500, 900, 1000)]
