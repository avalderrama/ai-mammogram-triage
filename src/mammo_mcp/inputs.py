"""Turns MCP tool arguments (base64 or local path) into the application's EncodedImage."""
from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

from mammo_application.abstractions import EncodedImage

_DATA_URL = re.compile(r"^data:image/[a-z0-9.+-]+;base64,", re.I)
_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp")


class ImageInputError(ValueError):
    """Invalid tool arguments; the message is safe to show to the client. `code` matches ErrorCode values."""

    def __init__(self, message: str, code: str = "invalid_input") -> None:
        super().__init__(message)
        self.code = code


def sniff_media_type(data: bytes) -> str | None:
    """Media type from magic bytes. The declared type is never trusted, so only these decoders are reachable."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def _too_large(max_bytes: int) -> ImageInputError:
    return ImageInputError(f"The image is larger than {max_bytes / 1e6:.0f} MB. Crop it or reduce its resolution.",
                           "image_too_large")


def _typed(data: bytes) -> EncodedImage:
    media_type = sniff_media_type(data)
    if media_type is None:
        raise ImageInputError("The data is not a supported image (PNG, JPEG, WebP, TIFF or BMP).",
                              "unsupported_media_type")
    return EncodedImage(data, media_type)


def image_from_arguments(image_base64: str | None, image_path: str | None, allow_local_paths: bool,
                         max_bytes: int) -> EncodedImage:
    if bool(image_base64) == bool(image_path):
        raise ImageInputError("Provide exactly one of image_base64 or image_path.")
    if image_path:
        if not allow_local_paths:
            raise ImageInputError("image_path is only available when the server runs locally (stdio). "
                                  "Send the image as image_base64.")
        path = Path(image_path).expanduser()
        if path.suffix.lower() not in _EXTENSIONS:
            raise ImageInputError(f"Unsupported file extension '{path.suffix}'. Use: {', '.join(_EXTENSIONS)}.",
                                  "unsupported_media_type")
        if not path.is_file():
            raise ImageInputError("The file does not exist or is not a regular file.")
        if path.stat().st_size > max_bytes:
            raise _too_large(max_bytes)
        return _typed(path.read_bytes())

    text = _DATA_URL.sub("", image_base64.strip(), count=1)  # type: ignore[union-attr]
    # Exact decoded size (padding excluded) so a file of exactly max_bytes is accepted.
    decoded_size = len(text) * 3 // 4 - text[-2:].count("=")
    if decoded_size > max_bytes:
        raise _too_large(max_bytes)
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageInputError("image_base64 is not valid base64.") from exc
    return _typed(data)
