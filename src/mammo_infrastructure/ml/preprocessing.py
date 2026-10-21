"""Mammogram preprocessing. Must stay identical to the training repository (common.preprocess)."""
from __future__ import annotations

import numpy as np
from PIL import Image
from scipy import ndimage

BASE_H, BASE_W = 512, 416
REF_HEIGHT = 578


def img_size(height: int) -> tuple[int, int]:
    return height, round(height * BASE_W / BASE_H / 32) * 32


def preprocess(gray: np.ndarray, out_h: int = BASE_H) -> np.ndarray:
    """uint8 grayscale mammogram -> uint8 (out_h, width): breast cropped, chest wall on the left."""
    out_h, out_w = img_size(out_h)
    ref_height = round(REF_HEIGHT * out_h / BASE_H)
    g = gray
    if g.shape[0] > ref_height * 1.2:
        new_w = round(g.shape[1] * ref_height / g.shape[0])
        g = np.asarray(Image.fromarray(g).resize((new_w, ref_height), Image.BOX))
    border = np.concatenate([g[:10].ravel(), g[-10:].ravel(), g[:, :10].ravel(), g[:, -10:].ravel()])
    if border.mean() > 127:
        g = 255 - g
    mask = ndimage.binary_opening(g > 20, iterations=2)
    labels, n = ndimage.label(mask)
    if n > 0:
        sizes = ndimage.sum(mask, labels, range(1, n + 1))
        ys, xs = np.where(labels == (np.argmax(sizes) + 1))
        y0, y1 = max(ys.min() - 4, 0), min(ys.max() + 5, g.shape[0])
        x0, x1 = max(xs.min() - 4, 0), min(xs.max() + 5, g.shape[1])
        g = g[y0:y1, x0:x1]
    half = g.shape[1] // 2
    if g[:, half:].sum() > g[:, :half].sum():
        g = g[:, ::-1]
    scale = min(out_h / g.shape[0], out_w / g.shape[1])
    h, w = max(round(g.shape[0] * scale), 1), max(round(g.shape[1] * scale), 1)
    g = np.asarray(Image.fromarray(np.ascontiguousarray(g)).resize((w, h), Image.BICUBIC))
    out = np.zeros((out_h, out_w), dtype=np.uint8)
    top = (out_h - h) // 2
    out[top:top + h, :w] = g
    return out
