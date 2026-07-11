from PIL import Image

import app as img_proc_app
from .helpers import decode_image, encode_image


def test_resize_changes_dimensions():
    original = Image.new("RGB", (10, 10), (255, 255, 0))

    result_b64 = img_proc_app._resize(encode_image(original), width=40, height=20)
    result_image = decode_image(result_b64)

    assert result_image.format == "PNG"
    assert result_image.size == (40, 20)
