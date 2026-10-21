"""Framework-neutral image type used across the application boundary."""
from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/tiff", "image/bmp")


@dataclass(frozen=True)
class EncodedImage:
    """Encoded image bytes (PNG, JPEG, WebP...). Never persisted by the application."""

    data: bytes
    media_type: str

    @property
    def size_bytes(self) -> int:
        return len(self.data)
