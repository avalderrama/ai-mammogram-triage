"""Global BI-RADS classifiers (512 + 1024 px ensemble) and the 2048 px patch scanner.

Backend-independent: the networks are injected (PyTorch or ONNX Runtime, see inference.Network).
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .constants import PATCH, PATCH_HEIGHT, STRIDE
from .inference import Network
from .preprocessing import preprocess


class GlobalEnsemble:
    """Average of the 512 px and 1024 px models: [P(1-2), P(3), P(4-5)]."""

    def __init__(self, networks: list[tuple[Network, int]]) -> None:
        self._networks = networks  # (network, input height)

    def probabilities(self, gray: np.ndarray) -> np.ndarray:
        return np.mean([net.probabilities(preprocess(gray, height)[None])[0] for net, height in self._networks],
                       axis=0)


class PatchScanner:
    """Slides 256 px windows (stride 128) over the breast at 2048 px. Scores = mean of the 2 most
    suspicious windows: P(4-5) and 'lesion' = 1 - P(normal). Also returns the P(4-5) grid.
    batch_size only trades speed for memory: the result does not depend on it."""

    def __init__(self, network: Network, batch_size: int) -> None:
        self._network, self._batch_size = network, batch_size

    def scan(self, gray: np.ndarray, top_k: int = 2) -> tuple[float, float, np.ndarray, np.ndarray]:
        a = preprocess(gray, PATCH_HEIGHT)
        ys = range(0, a.shape[0] - PATCH + 1, STRIDE)
        xs = range(0, a.shape[1] - PATCH + 1, STRIDE)
        coords = [(y, x) for y in ys for x in xs if (a[y:y + PATCH, x:x + PATCH] > 25).mean() >= 0.3]
        heat = np.zeros((len(ys), len(xs)), np.float32)
        if not coords:
            return 0.0, 0.0, heat, a
        probs = []
        for i in range(0, len(coords), self._batch_size):
            tiles = np.stack([a[y:y + PATCH, x:x + PATCH] for y, x in coords[i:i + self._batch_size]])
            probs.append(self._network.probabilities(tiles))
        p = np.concatenate(probs)
        for (y, x), q in zip(coords, p):
            heat[y // STRIDE, x // STRIDE] = q[2]
        k = min(top_k, len(p))
        return float(np.sort(p[:, 2])[-k:].mean()), float(np.sort(1 - p[:, 0])[-k:].mean()), heat, a


def render_heatmap(breast: np.ndarray, heat: np.ndarray, out_height: int = 768) -> Image.Image:
    """Overlay of the suspicious areas (red, more opaque = more suspicious) on the processed breast."""
    full = np.zeros(breast.shape, np.float32)
    up = np.kron(heat, np.ones((STRIDE, STRIDE), np.float32))
    hh, ww = min(up.shape[0], breast.shape[0] - PATCH // 4), min(up.shape[1], breast.shape[1] - PATCH // 4)
    full[PATCH // 4:PATCH // 4 + hh, PATCH // 4:PATCH // 4 + ww] = up[:hh, :ww]
    alpha = np.clip((full - 0.2) / 0.6, 0, 0.75)[..., None]
    base = np.repeat(breast[..., None].astype(np.float32), 3, axis=2)
    red = np.array([217, 58, 43], np.float32)
    rgb = (base * (1 - alpha) + red * alpha).astype(np.uint8)
    img = Image.fromarray(rgb)
    return img.resize((round(img.width * out_height / img.height), out_height), Image.BILINEAR)
