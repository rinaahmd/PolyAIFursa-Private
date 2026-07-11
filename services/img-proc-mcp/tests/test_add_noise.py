from PIL import Image

import app as img_proc_app
from .helpers import decode_image, encode_image


def test_add_noise_flips_some_pixels_but_keeps_size():
    original = Image.new("RGB", (20, 20), (128, 128, 128))

    result_b64 = img_proc_app._add_noise(encode_image(original), amount=0.5)
    result_image = decode_image(result_b64).convert("RGB")

    assert result_image.size == (20, 20)
    pixels = list(result_image.getdata())
    changed = [p for p in pixels if p != (128, 128, 128)]
    assert len(changed) > 0
    assert all(p in [(0, 0, 0), (255, 255, 255)] for p in changed)


def test_add_noise_zero_amount_leaves_image_unchanged():
    original = Image.new("RGB", (10, 10), (10, 20, 30))

    result_b64 = img_proc_app._add_noise(encode_image(original), amount=0.0)
    result_image = decode_image(result_b64).convert("RGB")

    assert list(result_image.getdata()) == list(original.getdata())
