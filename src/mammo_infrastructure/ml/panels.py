"""Split images that place several mammogram views side by side."""
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


def equal_panels(width: int, count: int) -> list[tuple[int, int]]:
    w = width // count
    return [(i * w, (i + 1) * w) for i in range(count)]
