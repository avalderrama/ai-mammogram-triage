"""Split images that place several mammogram views side by side, or in a grid (e.g. a 2 x 2 figure)."""
from __future__ import annotations

import numpy as np
from PIL import Image

MIN_PANEL_WIDTH = 150
# The separator rules below measure in pixels (a line is brighter than the columns 6 px away, a view is at least
# MIN_PANEL_WIDTH wide), so they look at a copy of this height: a high-resolution photo, where the separator line is
# 20 px wide, gives the same answer as a small screenshot.
DETECTION_HEIGHT = 1024


def find_panels(gray: np.ndarray, min_width: int = MIN_PANEL_WIDTH) -> list[tuple[int, int]]:
    """Columns that are almost white for >90% of the height, or a thin line much brighter than its
    neighbours, are separators. Returns [(x0, x1), ...] in columns of `gray`; one panel when no separator is found."""
    height, width = gray.shape
    if height <= DETECTION_HEIGHT:
        return _find_panels(gray, min_width)
    small_width = max(1, round(width * DETECTION_HEIGHT / height))
    small = np.asarray(Image.fromarray(gray).resize((small_width, DETECTION_HEIGHT), Image.BOX))
    scale = width / small_width
    return [(round(x0 * scale), min(width, round(x1 * scale))) for x0, x1 in _find_panels(small, min_width)]


def _find_panels(gray: np.ndarray, min_width: int) -> list[tuple[int, int]]:
    white = (gray > 220).mean(0) > 0.9
    m = gray.astype(np.float32).mean(0)
    left, right = np.roll(m, 6), np.roll(m, -6)
    thin = (m - np.minimum(left, right) > 60) & ((gray > 150).mean(0) > 0.6)
    thin[:10] = thin[-10:] = False
    cols = np.where(~(white | thin))[0]
    if len(cols) == 0:
        return [(0, gray.shape[1])]
    runs = [r for r in np.split(cols, np.where(np.diff(cols) > 1)[0] + 1) if len(r) >= min_width]
    return [(int(r[0]), int(r[-1]) + 1) for r in runs] or [(0, gray.shape[1])]


Box = tuple[int, int, int, int]  # x0, y0, x1, y1


def find_panel_boxes(gray: np.ndarray) -> list[Box]:
    """The views of an image, left to right and top to bottom: columns are split first, then the rows inside each
    column, then the columns inside each of those (a grid whose vertical lines stop at the horizontal one). Row
    separators follow the same rules as column separators, on the transposed image."""
    boxes: list[Box] = []
    for x0, x1 in find_panels(gray):
        for y0, y1 in find_panels(np.ascontiguousarray(gray[:, x0:x1].T)):
            piece = gray[y0:y1, x0:x1]
            # Columns that only show up inside one row; nothing new to look for if nothing was split yet.
            inner = find_panels(piece) if piece.shape != gray.shape else [(0, x1 - x0)]
            boxes += [(x0 + a, y0, x0 + b, y1) for a, b in inner]
    return _reading_order(boxes)


def _reading_order(boxes: list[Box]) -> list[Box]:
    """Rows of boxes whose vertical extents overlap, top to bottom; left to right within a row."""
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        row = rows[-1] if rows else None
        if row and min(box[3], row[0][3]) - max(box[1], row[0][1]) > 0.5 * min(box[3] - box[1], row[0][3] - row[0][1]):
            row.append(box)
        else:
            rows.append([box])
    return [b for row in rows for b in sorted(row, key=lambda b: b[0])]


def equal_panels(width: int, count: int) -> list[tuple[int, int]]:
    w = width // count
    return [(i * w, (i + 1) * w) for i in range(count)]


def equal_boxes(shape: tuple[int, int], count: int, grid: bool = False) -> list[Box]:
    """`count` equal views side by side, or, with grid, in two rows of count / 2 (left to right, top to bottom)."""
    height, width = shape
    if not grid:
        return [(x0, 0, x1, height) for x0, x1 in equal_panels(width, count)]
    half = height // 2
    columns = equal_panels(width, count // 2)
    return [(x0, y0, x1, y1) for y0, y1 in ((0, half), (half, height)) for x0, x1 in columns]
