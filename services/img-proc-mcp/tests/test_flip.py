import pytest
from PIL import Image

import app as img_proc_app
from .helpers import decode_image, encode_image


def _half_red_half_blue(direction: str) -> Image.Image:
    image = Image.new("RGB", (10, 10), (0, 0, 255))
    if direction == "horizontal":
        for x in range(5):
            for y in range(10):
                image.putpixel((x, y), (255, 0, 0))
    else:
        for x in range(10):
            for y in range(5):
                image.putpixel((x, y), (255, 0, 0))
    return image


def test_flip_horizontal_mirrors_left_and_right():
    image = _half_red_half_blue("horizontal")

    result_b64 = img_proc_app._flip(encode_image(image), direction="horizontal")
    result_image = decode_image(result_b64).convert("RGB")

    assert result_image.getpixel((0, 5)) == (0, 0, 255)
    assert result_image.getpixel((9, 5)) == (255, 0, 0)


def test_flip_vertical_mirrors_top_and_bottom():
    image = _half_red_half_blue("vertical")

    result_b64 = img_proc_app._flip(encode_image(image), direction="vertical")
    result_image = decode_image(result_b64).convert("RGB")

    assert result_image.getpixel((5, 0)) == (0, 0, 255)
    assert result_image.getpixel((5, 9)) == (255, 0, 0)


def test_flip_rejects_invalid_direction():
    image = Image.new("RGB", (4, 4), (0, 0, 0))

    with pytest.raises(ValueError):
        img_proc_app._flip(encode_image(image), direction="diagonal")
