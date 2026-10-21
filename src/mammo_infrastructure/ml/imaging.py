"""Decoding/encoding between the application's EncodedImage and PIL / numpy."""
from __future__ import annotations

import io
import warnings

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from mammo_application.abstractions import EncodedImage, ImageDecodingError, ImageTooManyPixelsError

# Pixel budget checked before decoding: full-field mammograms are ~5-14 MP and phone photos ~12-50 MP.
# Pillow only raises above 2x MAX_IMAGE_PIXELS, so the budget is enforced explicitly (pixel-flood defence).
MAX_DECODE_PIXELS = 60_000_000
Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS
ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP", "TIFF", "BMP")


def decode(image: EncodedImage) -> Image.Image:
    try:
        # catch_warnings is process-global: safe only because inference runs one thread at a time
        # (CapacityLimiter(1) in mammo_mcp.server). The explicit pixel check below is the primary defence.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(image.data), formats=ALLOWED_FORMATS)
            if img.width * img.height > MAX_DECODE_PIXELS:
                raise ImageTooManyPixelsError("Image has too many pixels")
            img.load()
        # Phone photos carry their rotation in EXIF; apply it so panels and sides keep their real layout.
        return ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ImageDecodingError("Unreadable image") from exc


def to_gray_array(img: Image.Image) -> np.ndarray:
    if img.mode in ("I;16", "I;16B", "I"):
        a = np.asarray(img, dtype=np.float32)
        lo, hi = np.percentile(a, [0.5, 99.5])
        return (np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1) * 255).astype(np.uint8)
    return np.asarray(img.convert("L"))


def encode_png(img: Image.Image | np.ndarray) -> EncodedImage:
    if isinstance(img, np.ndarray):
        img = Image.fromarray(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return EncodedImage(buf.getvalue(), "image/png")
