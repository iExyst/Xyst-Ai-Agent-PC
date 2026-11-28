from base64 import b64encode
from io import BytesIO

import pytest
from PIL import Image
from xaa.runtime.image_input import decode_run_image_data_url


def encoded_image(image_format: str, size: tuple[int, int] = (2, 2)) -> tuple[bytes, str]:
    buffer = BytesIO()
    Image.new("RGB", size, (20, 80, 160)).save(buffer, format=image_format)
    raw = buffer.getvalue()
    media_type = "image/png" if image_format == "PNG" else "image/jpeg"
    return raw, f"data:{media_type};base64," + b64encode(raw).decode("ascii")


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_static_image_validation_decodes_and_preserves_bounded_rasters(image_format: str) -> None:
    raw, data_url = encoded_image(image_format)
    image, media_type, original, source_type = decode_run_image_data_url(data_url)
    assert image == raw
    assert media_type == ("image/png" if image_format == "PNG" else "image/jpeg")
    assert original is None
    assert source_type is None


@pytest.mark.parametrize(
    ("image_format", "size"),
    [("PNG", (1601, 1)), ("JPEG", (1600, 1563))],
)
def test_static_image_validation_rejects_oversized_dimensions(
    image_format: str, size: tuple[int, int]
) -> None:
    _raw, data_url = encoded_image(image_format, size)
    with pytest.raises(ValueError, match="bounded image dimensions"):
        decode_run_image_data_url(data_url)


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_static_image_validation_rejects_signature_only_payloads(image_format: str) -> None:
    signature = b"\x89PNG\r\n\x1a\n" if image_format == "PNG" else b"\xff\xd8\xff"
    media_type = "image/png" if image_format == "PNG" else "image/jpeg"
    data_url = f"data:{media_type};base64," + b64encode(signature + b"not-an-image").decode("ascii")
    with pytest.raises(ValueError, match="safely decoded"):
        decode_run_image_data_url(data_url)
