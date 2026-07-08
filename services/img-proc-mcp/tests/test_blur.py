from PIL import Image

import app as img_proc_app
from .helpers import decode_image, encode_image


def test_blur_returns_valid_base64_png_of_same_size():
    original = Image.new("RGB", (10, 10), (255, 0, 0))

    result_b64 = img_proc_app._blur(encode_image(original), radius=2.0)
    result_image = decode_image(result_b64)

    assert result_image.format == "PNG"
    assert result_image.size == (10, 10)


def test_blur_softens_a_hard_edge():
    image = Image.new("RGB", (10, 10), (255, 255, 255))
    for x in range(5):
        for y in range(10):
            image.putpixel((x, y), (0, 0, 0))

    result_b64 = img_proc_app._blur(encode_image(image), radius=2.0)
    result_image = decode_image(result_b64).convert("RGB")

    boundary_pixel = result_image.getpixel((5, 5))
    assert boundary_pixel != (0, 0, 0)
    assert boundary_pixel != (255, 255, 255)
