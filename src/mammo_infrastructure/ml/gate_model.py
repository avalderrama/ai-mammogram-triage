"""'Is it a mammogram?' filter and photo quality checks (same logic as the training repository's gate.py)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFilter
from scipy import ndimage

from .networks import load_network, normalize

GATE_SIZE = 224


def to_square(img: Image.Image, size: int = GATE_SIZE) -> np.ndarray:
    g = img.convert("L")
    g.thumbnail((size, size), Image.BILINEAR)
    out = Image.new("L", (size, size), 0)
    out.paste(g, ((size - g.width) // 2, (size - g.height) // 2))
    return np.asarray(out)


class GateModel:
    def __init__(self, path: Path, device: torch.device) -> None:
        self._device = device
        self._net, meta = load_network(path, 2, device)
        self.threshold: float = meta["umbral"]
        self.quality: dict = meta["calidad"]

    @torch.no_grad()
    def p_mammogram(self, img: Image.Image) -> float:
        """Average over 3 variants (original, slightly blurred, 3% border removed)."""
        w, h = img.size
        m = max(int(0.03 * min(w, h)), 1)
        variants = [img, img.filter(ImageFilter.GaussianBlur(1)), img.crop((m, m, w - m, h - m))]
        x = torch.from_numpy(np.stack([to_square(v) for v in variants])).unsqueeze(1).to(self._device)
        return float(torch.softmax(self._net(normalize(x, 0.449, 0.226)), 1)[:, 1].mean())

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
