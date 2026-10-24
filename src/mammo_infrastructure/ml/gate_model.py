"""'Is it a mammogram?' filter and photo quality checks (same logic as the training repository's gate.py).

Backend-independent: the network is injected (PyTorch or ONNX Runtime, see inference.Network).
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

from ..bundle import BundleError
from .constants import GATE_SIZE
from .inference import LoadedNetwork

# Gate versions this server can run: 2 adds the black-level normalization below.
KNOWN_GATE_VERSIONS = ("1", "2")


def to_square(img: Image.Image, size: int = GATE_SIZE) -> np.ndarray:
    g = img.convert("L")
    g.thumbnail((size, size), Image.BILINEAR)
    out = Image.new("L", (size, size), 0)
    out.paste(g, ((size - g.width) // 2, (size - g.height) // 2))
    return np.asarray(out)


def normalize_black(img: Image.Image, cfg: dict) -> Image.Image:
    """Gate v2: a photographed monitor shows black as grey. If the given low percentile of the grey image is at least
    cfg["min"], subtract it and rescale (black to 0, white kept); digital mammograms (percentile ~0) are left as they
    are. Same as gate.normalize_black in the training repository."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    b = float(np.percentile(g, cfg["percentile"]))
    if b < cfg["min"]:
        return img.convert("L")
    return Image.fromarray(np.clip((g - b) * 255 / (255 - b), 0, 255).astype(np.uint8))


class GateModel:
    def __init__(self, loaded: LoadedNetwork) -> None:
        self._network = loaded.network
        self.threshold: float = float(loaded.meta["umbral"])
        self.quality: dict = loaded.meta["calidad"]
        self.version = str(loaded.meta.get("version", "1"))
        self._black_level: dict | None = loaded.meta.get("black_level")
        # A gate trained on normalized images runs wrongly, without any error, on raw ones: refuse what is not known.
        if self.version not in KNOWN_GATE_VERSIONS:
            raise BundleError(f"Gate version {self.version} is not supported by this server (known: "
                              f"{', '.join(KNOWN_GATE_VERSIONS)}): update the server")
        if self.version == "2" and not (isinstance(self._black_level, dict)
                                        and {"percentile", "min"} <= set(self._black_level)):
            raise BundleError("Gate version 2 needs black_level (percentile, min) in its metadata")

    def p_mammogram(self, img: Image.Image) -> float:
        """Average over 3 variants (original, slightly blurred, 3% border removed); gate v2 first normalizes the
        black level. Quality checks keep the original image."""
        if self._black_level:
            img = normalize_black(img, self._black_level)
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
