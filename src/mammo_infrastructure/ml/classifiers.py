"""Global BI-RADS classifiers (512 + 1024 px ensemble) and the 2048 px patch scanner."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from .networks import load_network, normalize
from .preprocessing import preprocess

PATCH, STRIDE, PATCH_HEIGHT = 256, 128, 2048


class GlobalEnsemble:
    """Average of the 512 px and 1024 px models: [P(1-2), P(3), P(4-5)]."""

    def __init__(self, paths: list[Path], device: torch.device) -> None:
        self._device = device
        self._models = []
        for p in paths:
            net, meta = load_network(p, 3, device)
            self._models.append((net, meta["height"], meta["mean"], meta["std"]))

    @torch.no_grad()
    def probabilities(self, gray: np.ndarray) -> np.ndarray:
        out = []
        for net, height, mean, std in self._models:
            x = torch.from_numpy(preprocess(gray, height)).unsqueeze(0).unsqueeze(0).to(self._device)
            with torch.autocast(self._device.type, enabled=self._device.type == "cuda"):
                logits = net(normalize(x, mean, std))
            out.append(F.softmax(logits.float(), 1).cpu().numpy()[0])
        return np.mean(out, axis=0)


class PatchScanner:
    """Slides 256 px windows (stride 128) over the breast at 2048 px. Scores = mean of the 2 most
    suspicious windows: P(4-5) and 'lesion' = 1 - P(normal). Also returns the P(4-5) grid."""

    def __init__(self, path: Path, device: torch.device) -> None:
        self._device = device
        self._net, meta = load_network(path, 3, device)
        self._mean, self._std = meta["mean"], meta["std"]

    @torch.no_grad()
    def scan(self, gray: np.ndarray, top_k: int = 2) -> tuple[float, float, np.ndarray, np.ndarray]:
        a = preprocess(gray, PATCH_HEIGHT)
        ys = range(0, a.shape[0] - PATCH + 1, STRIDE)
        xs = range(0, a.shape[1] - PATCH + 1, STRIDE)
        coords, tiles = [], []
        for y in ys:
            for x in xs:
                t = a[y:y + PATCH, x:x + PATCH]
                if (t > 25).mean() >= 0.3:
                    coords.append((y, x))
                    tiles.append(t)
        heat = np.zeros((len(ys), len(xs)), np.float32)
        if not tiles:
            return 0.0, 0.0, heat, a
        probs = []
        for i in range(0, len(tiles), 96):
            b = torch.from_numpy(np.stack(tiles[i:i + 96])).unsqueeze(1).to(self._device)
            with torch.autocast(self._device.type, enabled=self._device.type == "cuda"):
                probs.append(F.softmax(self._net(normalize(b, self._mean, self._std)).float(), 1).cpu().numpy())
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
