"""Structural validation of incoming images (format, size). Business rules live in the domain."""
from __future__ import annotations

from mammo_application.abstractions import SUPPORTED_MEDIA_TYPES, EncodedImage, ImageTooManyPixelsError

from .result import AppError, ErrorCode


def validate_image(image: EncodedImage, max_bytes: int) -> AppError | None:
    if image.media_type not in SUPPORTED_MEDIA_TYPES:
        return AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                        f"Unsupported image type '{image.media_type}'. Use one of: {', '.join(SUPPORTED_MEDIA_TYPES)}.")
    if image.size_bytes == 0:
        return AppError(ErrorCode.INVALID_INPUT, "The image is empty.")
    if image.size_bytes > max_bytes:
        return AppError(ErrorCode.IMAGE_TOO_LARGE,
                        f"The image is {image.size_bytes / 1e6:.1f} MB; the limit is {max_bytes / 1e6:.0f} MB. "
                        "Crop it to the mammogram or reduce its resolution.")
    return None


def decoding_error(exc: Exception) -> AppError:
    """Error for an image the adapters could not decode; an over-sized image gets actionable advice."""
    if isinstance(exc, ImageTooManyPixelsError):
        return AppError(ErrorCode.IMAGE_TOO_LARGE, "The image has too many pixels. Crop it to the mammogram or "
                                                   "reduce its resolution (at most 60 megapixels).")
    return AppError(ErrorCode.UNREADABLE_IMAGE, "The file could not be read as an image.")
