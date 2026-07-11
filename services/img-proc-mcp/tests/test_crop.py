from PIL import Image

import app as img_proc_app
from .helpers import decode_image, encode_image


def test_crop_returns_requested_bounding_box_size():
    original = Image.new("RGB", (20, 20), (0, 0, 0))
    for x in range(5, 15):
        for y in range(5, 15):
            original.putpixel((x, y), (255, 255, 255))

    result_b64 = img_proc_app._crop(encode_image(original), left=5, top=5, right=15, bottom=15)
    result_image = decode_image(result_b64).convert("RGB")

    assert result_image.size == (10, 10)
    assert result_image.getpixel((0, 0)) == (255, 255, 255)
