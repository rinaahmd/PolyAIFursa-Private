from PIL import Image

import app as img_proc_app
from .helpers import decode_image, encode_image


def test_rotate_90_degrees_swaps_width_and_height():
    original = Image.new("RGB", (20, 10), (255, 0, 0))

    result_b64 = img_proc_app._rotate(encode_image(original), angle=90)
    result_image = decode_image(result_b64)

    assert result_image.format == "PNG"
    assert result_image.size == (10, 20)


def test_rotate_360_degrees_keeps_original_size():
    original = Image.new("RGB", (12, 8), (0, 255, 0))

    result_b64 = img_proc_app._rotate(encode_image(original), angle=360)
    result_image = decode_image(result_b64)

    assert result_image.size == (12, 8)


def test_rotate_90_degrees_with_expand_false_keeps_original_size():
    original = Image.new("RGB", (20, 10), (255, 0, 0))

    result_b64 = img_proc_app._rotate(encode_image(original), angle=90, expand=False)
    result_image = decode_image(result_b64)

    assert result_image.size == (20, 10)
