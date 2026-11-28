"""Bounded local normalization for animated image inputs."""

from __future__ import annotations

import base64
import binascii
import warnings
from io import BytesIO

from PIL import Image

MAX_CONVERTED_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_DIMENSION = 1600
MAX_IMAGE_PIXELS = 2_500_000
CONVERTIBLE_IMAGE_FORMATS = {"image/gif": "GIF", "image/webp": "WEBP"}


def decode_run_image_data_url(
    data_url: str,
) -> tuple[bytes, str, bytes | None, str | None]:
    """Validate a bounded user image and normalize GIF/WebP for model input."""
    supported_prefixes = (
        "data:image/png;base64,",
        "data:image/jpeg;base64,",
        "data:image/gif;base64,",
        "data:image/webp;base64,",
    )
    if not data_url.startswith(supported_prefixes):
        raise ValueError("Run image must be a PNG, JPEG, GIF or WebP data URL.")
    header, encoded = data_url.split(",", 1)
    media_type = header.split(";", 1)[0].removeprefix("data:")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Run image data is invalid.") from None
    if not raw or len(raw) > 3 * 1024 * 1024:
        raise ValueError("Run image must be between 1 byte and 3 MiB.")
    if media_type in CONVERTIBLE_IMAGE_FORMATS:
        normalized = convert_animated_image_to_png(raw, CONVERTIBLE_IMAGE_FORMATS[media_type])
        return normalized, "image/png", raw, media_type
    validate_static_image(raw, media_type)
    return raw, media_type, None, None


def validate_static_image(raw: bytes, media_type: str) -> None:
    """Decode a bounded PNG/JPEG and reject mislabeled, malformed or oversized rasters."""
    expected_format = {"image/png": "PNG", "image/jpeg": "JPEG"}.get(media_type)
    if expected_format is None:
        raise ValueError("Run image must be a PNG, JPEG, GIF or WebP image.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=[expected_format]) as image:
                width, height = image.size
                if (
                    image.format != expected_format
                    or width < 1
                    or height < 1
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise ValueError("Run image exceeds the bounded image dimensions.")
                image.load()
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Run image could not be safely decoded.") from None


def convert_animated_image_to_png(raw: bytes, expected_format: str) -> bytes:
    """Decode only frame one of a dimension- and byte-bounded GIF or WebP image."""
    try:
        image = Image.open(BytesIO(raw), formats=[expected_format])
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValueError("Image could not be safely decoded.") from None
    with image:
        width, height = image.size
        if (
            image.format != expected_format
            or width < 1
            or height < 1
            or width > MAX_IMAGE_DIMENSION
            or height > MAX_IMAGE_DIMENSION
            or width * height > MAX_IMAGE_PIXELS
        ):
            raise ValueError("Image exceeds the bounded image dimensions.")
        try:
            image.seek(0)
            image.load()
            output = BytesIO()
            image.convert("RGBA").save(output, format="PNG", optimize=False)
            normalized = output.getvalue()
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            raise ValueError("Image could not be safely converted.") from None
    if len(normalized) > MAX_CONVERTED_IMAGE_BYTES:
        raise ValueError("Converted image exceeds the 2 MiB limit.")
    return normalized
