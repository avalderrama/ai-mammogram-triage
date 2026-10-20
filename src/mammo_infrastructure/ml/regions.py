"""Suspicious regions from the patch scanner: the windows with the highest P(4-5), described by position."""
from __future__ import annotations

import numpy as np

from .classifiers import PATCH, STRIDE

TISSUE_LEVEL = 25


def _thirds(value: float, low: str, mid: str, high: str) -> str:
    return low if value < 1 / 3 else mid if value < 2 / 3 else high


def find_regions(heat: np.ndarray, breast: np.ndarray, max_regions: int = 3, min_score: float = 0.25,
                 max_overlap: float = 0.25) -> list[dict]:
    """Top windows by P(4-5) after non-maximum suppression.

    Boxes are normalized to the processed breast image, which is exactly the image the heatmap is drawn on
    (chest wall on the left). Depth goes from the chest wall (posterior) to the nipple (anterior), measured
    against the tissue extent; height is the vertical third of the tissue.
    """
    height, width = breast.shape
    tissue = breast > TISSUE_LEVEL
    cols, rows = np.where(tissue.any(axis=0))[0], np.where(tissue.any(axis=1))[0]
    if cols.size == 0 or heat.size == 0:
        return []
    tissue_right, top, bottom = cols.max() + 1, rows.min(), rows.max() + 1

    chosen: list[tuple[int, int, float]] = []
    for flat in np.argsort(heat, axis=None)[::-1]:
        iy, ix = np.unravel_index(flat, heat.shape)
        score = float(heat[iy, ix])
        if score < min_score or len(chosen) == max_regions:
            break
        y, x = int(iy) * STRIDE, int(ix) * STRIDE
        overlaps = (max(0, PATCH - abs(x - cx)) * max(0, PATCH - abs(y - cy)) / PATCH ** 2 for cy, cx, _ in chosen)
        if all(o <= max_overlap for o in overlaps):
            chosen.append((y, x, score))

    regions = []
    for y, x, score in chosen:
        center_x, center_y = x + PATCH / 2, y + PATCH / 2
        regions.append({
            "x": round(x / width, 4), "y": round(y / height, 4),
            "width": round(min(PATCH, width - x) / width, 4), "height": round(min(PATCH, height - y) / height, 4),
            "score": round(score, 4),
            "depth": _thirds(center_x / tissue_right, "posterior", "middle", "anterior"),
            "vertical": _thirds((center_y - top) / max(bottom - top, 1), "upper", "middle", "lower"),
        })
    return regions
