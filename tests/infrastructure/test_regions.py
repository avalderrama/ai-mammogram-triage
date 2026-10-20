"""Suspicious-region extraction from the patch heat grid (no model needed)."""
import numpy as np

from mammo_infrastructure.ml.regions import find_regions


def breast_canvas(height=2048, width=1664, tissue_right=1200):
    a = np.zeros((height, width), np.uint8)
    a[200:1850, :tissue_right] = 120  # tissue from the chest wall (x=0) to the nipple (x=tissue_right)
    return a


def test_top_window_is_located_and_described():
    heat = np.zeros((15, 12), np.float32)
    heat[2, 8] = 0.9  # window at y=256, x=1024: near the nipple, upper part
    regions = find_regions(heat, breast_canvas())
    assert len(regions) == 1
    r = regions[0]
    assert (r["x"], r["y"]) == (round(1024 / 1664, 4), round(256 / 2048, 4))
    assert r["width"] == round(256 / 1664, 4) and r["score"] == 0.9
    assert (r["depth"], r["vertical"]) == ("anterior", "upper")


def test_overlapping_windows_are_suppressed_and_capped():
    heat = np.zeros((15, 12), np.float32)
    heat[10, 1], heat[10, 2], heat[5, 5], heat[12, 9], heat[1, 1] = 0.8, 0.79, 0.7, 0.6, 0.5
    regions = find_regions(heat, breast_canvas())
    assert [r["score"] for r in regions] == [0.8, 0.7, 0.6]  # (10,2) overlaps (10,1); capped at 3
    assert regions[0]["depth"] == "posterior" and regions[0]["vertical"] == "lower"


def test_nothing_below_the_minimum_score_or_without_tissue():
    heat = np.full((15, 12), 0.1, np.float32)
    assert find_regions(heat, breast_canvas()) == []
    assert find_regions(np.ones((15, 12), np.float32), np.zeros((2048, 1664), np.uint8)) == []
