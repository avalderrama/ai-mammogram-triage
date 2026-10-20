"""OCR-based masking of text drawn over medical images (names, IDs, dates...).

Every detected text region is covered, not only what "looks like" personal data. The text that is
read is NEVER returned or logged: only categories and counts.
"""
from __future__ import annotations

import re

import numpy as np
from PIL import Image, ImageDraw

PATTERNS = {
    "date_time": re.compile(r"\d{1,4}[/\-.]\d{1,2}[/\-.]\d{1,4}|\d{1,2}:\d{2}"),
    "long_number": re.compile(r"\d{6,}"),
    "age": re.compile(r"\d{1,3}\s*(años|anos|y\b|yrs|a\b)", re.I),
    "probable_name": re.compile(r"^[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+([ ,]+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)+$"),
    "keyword": re.compile(r"paciente|patient|cuenta|account|\bid\b|doc|historia|nacimiento|dob|sexo|edad|age", re.I),
}


def categorize(text: str) -> list[str]:
    return [name for name, rx in PATTERNS.items() if rx.search(text.strip())] or ["other_text"]


class TextMasker:
    def __init__(self, use_gpu: bool, min_conf: float = 0.3, max_rel_height: float = 0.08, pad: int = 4) -> None:
        import easyocr  # heavy import: only when the masker is created
        self._reader = easyocr.Reader(["es", "en"], gpu=use_gpu, verbose=False)
        self._min_conf, self._max_rel_height, self._pad = min_conf, max_rel_height, pad

    def mask(self, img: Image.Image) -> tuple[Image.Image, int, dict[str, int]]:
        rgb = img.convert("RGB")
        w, h = rgb.size
        scale = 2.0 if max(w, h) < 1200 else 1.0
        big = np.asarray(rgb.resize((int(w * scale), int(h * scale)))) if scale != 1 else np.asarray(rgb)
        out = rgb.copy()
        draw = ImageDraw.Draw(out)
        regions, cats = 0, {}
        for box, text, conf in self._reader.readtext(big, paragraph=False):
            xs = [p[0] / scale for p in box]
            ys = [p[1] / scale for p in box]
            if conf < self._min_conf or (max(ys) - min(ys)) > self._max_rel_height * h or not text.strip():
                continue
            pad = self._pad
            draw.rectangle([min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad], fill=(0, 0, 0))
            regions += 1
            for c in categorize(text):
                cats[c] = cats.get(c, 0) + 1
        return out, regions, cats
