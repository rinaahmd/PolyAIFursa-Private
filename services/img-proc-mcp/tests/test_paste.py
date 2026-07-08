from PIL import Image

import app as img_proc_app
from .helpers import decode_image, encode_image


def test_paste_overwrites_region_at_given_position():
    base = Image.new("RGB", (20, 20), (0, 0, 0))
    region = Image.new("RGB", (5, 5), (255, 255, 255))

    result_b64 = img_proc_app._paste(encode_image(base), encode_image(region), left=10, top=10)
    result_image = decode_image(result_b64).convert("RGB")

    assert result_image.size == (20, 20)
    assert result_image.getpixel((12, 12)) == (255, 255, 255)
    assert result_image.getpixel((0, 0)) == (0, 0, 0)


def test_paste_leaves_area_outside_region_untouched():
    base = Image.new("RGB", (10, 10), (0, 0, 0))
    for x in range(10):
        for y in range(10):
            base.putpixel((x, y), (10, 20, 30))
    region = Image.new("RGB", (4, 4), (255, 0, 0))

    result_b64 = img_proc_app._paste(encode_image(base), encode_image(region), left=0, top=0)
    result_image = decode_image(result_b64).convert("RGB")

    assert result_image.getpixel((9, 9)) == (10, 20, 30)
    assert result_image.getpixel((0, 0)) == (255, 0, 0)
