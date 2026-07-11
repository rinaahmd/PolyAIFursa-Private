import io

from PIL import Image

from app import _normalize_orientation_in_place


def _write_jpeg_with_orientation(path: str, size: tuple[int, int], orientation: int) -> None:
    """Write a JPEG whose stored pixel grid is `size`, tagged with the given
    EXIF orientation. Mirrors what a phone camera produces: the file's raw
    pixels are one way, but a viewer honoring EXIF displays it rotated."""
    image = Image.new("RGB", size, (255, 0, 0))
    exif = Image.Exif()
    exif[274] = orientation  # 274 = Orientation tag
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    with open(path, "wb") as f:
        f.write(buffer.getvalue())


def test_normalize_orientation_bakes_in_a_90_degree_rotation(tmp_path):
    path = str(tmp_path / "rotated.jpg")
    # Orientation 6 = stored rotated 90 CW from how it should display,
    # i.e. correcting it swaps width and height.
    _write_jpeg_with_orientation(path, size=(100, 60), orientation=6)

    width, height = _normalize_orientation_in_place(path)

    assert (width, height) == (60, 100)
    with Image.open(path) as normalized:
        assert normalized.size == (60, 100)
        # The EXIF orientation tag is now gone/neutral - nothing downstream
        # needs to re-apply exif_transpose on this file.
        assert normalized.getexif().get(274, 1) == 1


def test_normalize_orientation_is_a_noop_for_already_upright_images(tmp_path):
    path = str(tmp_path / "upright.jpg")
    _write_jpeg_with_orientation(path, size=(80, 50), orientation=1)

    width, height = _normalize_orientation_in_place(path)

    assert (width, height) == (80, 50)


def test_normalize_orientation_handles_images_with_no_exif_at_all(tmp_path):
    path = str(tmp_path / "plain.jpg")
    image = Image.new("RGB", (40, 30), (0, 255, 0))
    image.save(path, format="JPEG")

    width, height = _normalize_orientation_in_place(path)

    assert (width, height) == (40, 30)
