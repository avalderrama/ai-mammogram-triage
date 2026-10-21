"""'Is it a mammogram?' filter and photo quality checks (same logic as the training repository's gate.py).

Backend-independent: the network is injected (PyTorch or ONNX Runtime, see inference.Network).
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

from .constants import GATE_SIZE
from .inference import LoadedNetwork


def to_square(img: Image.Image, size: int = GATE_SIZE) -> np.ndarray:
    g = img.convert("L")
    g.thumbnail((size, size), Image.BILINEAR)
    out = Image.new("L", (size, size), 0)
    out.paste(g, ((size - g.width) // 2, (size - g.height) // 2))
    return np.asarray(out)


class GateModel:
    def __init__(self, loaded: LoadedNetwork) -> None:
        self._network = loaded.network
        self.threshold: float = float(loaded.meta["umbral"])
        self.quality: dict = loaded.meta["calidad"]

    def p_mammogram(self, img: Image.Image) -> float:
        """Average over 3 variants (original, slightly blurred, 3% border removed)."""
        w, h = img.size
        m = max(int(0.03 * min(w, h)), 1)
        variants = [img, img.filter(ImageFilter.GaussianBlur(1)), img.crop((m, m, w - m, h - m))]
        return float(self._network.probabilities(np.stack([to_square(v) for v in variants]))[:, 1].mean())

    def quality_problems(self, img: Image.Image) -> list[str]:
        q = self.quality
        problems: list[str] = []
        g = np.asarray(img.convert("L"), dtype=np.float32)
        if min(g.shape) < q["min_lado_px"]:
            problems.append("low_resolution")
        small = np.asarray(Image.fromarray(g.astype(np.uint8)).resize((512, round(512 * g.shape[0] / g.shape[1]))),
                           dtype=np.float32)
        tissue = small > 25
        if tissue.mean() > 0.02:
            inner = ndimage.binary_erosion(tissue, iterations=3)
            if inner.any() and ndimage.laplace(small)[inner].var() < q["nitidez_min"]:
                problems.append("blurry")
            if (small[tissue] > 250).mean() > q["saturado_max"]:
                problems.append("glare")
        if tissue.mean() < q["mama_min_fraccion"]:
            problems.append("small_breast")
        return problems
