import base64
import io

from PIL import Image

import app as img_proc_app


def _encode_image(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def test_blur_returns_valid_base64_png_of_same_size():
    original = Image.new("RGB", (10, 10), (255, 0, 0))

    result_b64 = img_proc_app._blur(_encode_image(original), radius=2.0)
    result_image = Image.open(io.BytesIO(base64.b64decode(result_b64)))

    assert result_image.format == "PNG"
    assert result_image.size == (10, 10)


def test_blur_softens_a_hard_edge():
    image = Image.new("RGB", (10, 10), (255, 255, 255))
    for x in range(5):
        for y in range(10):
            image.putpixel((x, y), (0, 0, 0))

    result_b64 = img_proc_app._blur(_encode_image(image), radius=2.0)
    result_image = Image.open(io.BytesIO(base64.b64decode(result_b64))).convert("RGB")

    boundary_pixel = result_image.getpixel((5, 5))
    assert boundary_pixel != (0, 0, 0)
    assert boundary_pixel != (255, 255, 255)
