"""Image decoding adapter (no models needed)."""
import io

import pytest
from PIL import Image

from mammo_application.abstractions import EncodedImage, ImageDecodingError
from mammo_infrastructure.ml.imaging import decode


def test_exif_rotation_is_applied():
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90° clockwise to display
    Image.new("L", (40, 20)).save(buf, format="JPEG", exif=exif)
    img = decode(EncodedImage(buf.getvalue(), "image/jpeg"))
    assert img.size == (20, 40)


def test_garbage_raises_decoding_error():
    with pytest.raises(ImageDecodingError):
        decode(EncodedImage(b"\x89PNG\r\n\x1a\nnot really", "image/png"))


def test_pixel_flood_is_rejected_before_decoding():
    buf = io.BytesIO()
    Image.new("1", (9000, 9000)).save(buf, format="PNG")  # 81 MP, a few KB compressed
    with pytest.raises(ImageDecodingError):
        decode(EncodedImage(buf.getvalue(), "image/png"))


def test_formats_outside_the_allowlist_are_not_decoded():
    buf = io.BytesIO()
    Image.new("L", (10, 10)).save(buf, format="GIF")
    with pytest.raises(ImageDecodingError):
        decode(EncodedImage(buf.getvalue(), "image/png"))
