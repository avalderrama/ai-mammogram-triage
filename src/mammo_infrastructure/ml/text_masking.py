"""OCR-based masking of text drawn over medical images (names, IDs, dates...).

Every detected text region is covered, not only what "looks like" personal data. The text that is
read is NEVER returned or logged: only categories and counts. The OCR engine is injected (EasyOCR or RapidOCR,
see TextDetector); the masking rules are the same for both.
"""
from __future__ import annotations

import re
from typing import Protocol

import numpy as np
from PIL import Image, ImageDraw

PATTERNS = {
    "date_time": re.compile(r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}|\d{1,2}:\d{2}"),
    "long_number": re.compile(r"\d{6,}"),
    "age": re.compile(r"\d{1,3}\s*(años|anos|y\b|yrs|a\b)", re.I),
    "probable_name": re.compile(r"^[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+([ ,]+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)+$"),
    "keyword": re.compile(r"paciente|patient|cuenta|account|\bid\b|doc|historia|nacimiento|dob|sexo|edad|age", re.I),
}

Detection = tuple[list[tuple[float, float]], str, float]  # polygon (pixel coordinates), text, confidence


class TextDetector(Protocol):
    # How much taller the engine's boxes are than the text (RapidOCR expands them); the height rule ignores it.
    box_margin: float
    # Largest image side handed to the engine at once (None = no limit). Larger images are read in overlapping
    # tiles at full resolution: the engine's peak memory grows with its input, and shrinking would lose small print.
    max_side: int | None

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        ...


TILE_OVERLAP = 200  # px; a line of text cut by a tile edge is still read whole, or in two masked parts
DUPLICATE_OVERLAP = 0.8  # a box mostly inside another one (overlapping tiles) is the same text


def categorize(text: str) -> list[str]:
    return [name for name, rx in PATTERNS.items() if rx.search(text.strip())] or ["other_text"]


def tiles(width: int, height: int, size: int | None) -> list[tuple[int, int, int, int]]:
    """Overlapping windows (x0, y0, x1, y1) of at most size x size covering the image."""
    def starts(n: int) -> list[int]:
        if not size or n <= size:
            return [0]
        return [*range(0, n - size, size - TILE_OVERLAP), n - size]
    end = lambda start, n: min(start + size, n) if size else n  # noqa: E731
    return [(x, y, end(x, width), end(y, height)) for y in starts(height) for x in starts(width)]


def _inside(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Fraction of box a's area that lies inside box b."""
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h / max((a[2] - a[0]) * (a[3] - a[1]), 1e-6)


class TextMasker:
    def __init__(self, detector: TextDetector, min_conf: float = 0.3, max_rel_height: float = 0.08,
                 pad: int = 4) -> None:
        self._detector = detector
        self._min_conf, self._max_rel_height, self._pad = min_conf, max_rel_height, pad

    def _detect(self, big: np.ndarray) -> list[Detection]:
        found = []
        for x0, y0, x1, y1 in tiles(big.shape[1], big.shape[0], self._detector.max_side):
            for box, text, conf in self._detector.detect(np.ascontiguousarray(big[y0:y1, x0:x1])):
                found.append(([(p[0] + x0, p[1] + y0) for p in box], text, conf))
        return found

    def mask(self, img: Image.Image) -> tuple[Image.Image, int, dict[str, int]]:
        rgb = img.convert("RGB")
        w, h = rgb.size
        scale = 2.0 if max(w, h) < 1200 else 1.0  # small images are enlarged so small print is still read
        big = np.asarray(rgb.resize((int(w * scale), int(h * scale)))) if scale != 1 else np.asarray(rgb)
        kept: list[tuple[tuple[float, float, float, float], str]] = []
        for box, text, conf in self._detect(big):
            xs = [p[0] / scale for p in box]
            ys = [p[1] / scale for p in box]
            too_tall = (max(ys) - min(ys)) / self._detector.box_margin > self._max_rel_height * h
            if conf < self._min_conf or too_tall or not text.strip():
                continue
            kept.append(((min(xs), min(ys), max(xs), max(ys)), text))
        # Largest first; a box mostly inside a kept one was read twice (overlapping tiles): masked, not counted.
        kept.sort(key=lambda k: (k[0][2] - k[0][0]) * (k[0][3] - k[0][1]), reverse=True)
        out = rgb.copy()
        draw = ImageDraw.Draw(out)
        counted: list[tuple[float, float, float, float]] = []
        cats: dict[str, int] = {}
        pad = self._pad
        for rect, text in kept:
            draw.rectangle([rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad], fill=(0, 0, 0))
            if any(_inside(rect, c) >= DUPLICATE_OVERLAP for c in counted):
                continue
            counted.append(rect)
            for c in categorize(text):
                cats[c] = cats.get(c, 0) + 1
        return out, len(counted), cats
