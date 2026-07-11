import base64
import io
import random

from fastmcp import FastMCP
from PIL import Image, ImageFilter

mcp = FastMCP("img-proc")


def _decode(image_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(image_b64)))


def _encode(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _blur(image_b64: str, radius: float = 2.0) -> str:
    image = _decode(image_b64).filter(ImageFilter.GaussianBlur(radius))
    return _encode(image)


def _rotate(image_b64: str, angle: float, expand: bool = True) -> str:
    image = _decode(image_b64).rotate(angle, expand=expand)
    return _encode(image)


def _flip(image_b64: str, direction: str = "horizontal") -> str:
    if direction not in ("horizontal", "vertical"):
        raise ValueError("direction must be 'horizontal' or 'vertical'")
    method = Image.FLIP_LEFT_RIGHT if direction == "horizontal" else Image.FLIP_TOP_BOTTOM
    image = _decode(image_b64).transpose(method)
    return _encode(image)


def _resize(image_b64: str, width: int, height: int) -> str:
    image = _decode(image_b64).resize((width, height))
    return _encode(image)


def _crop(image_b64: str, left: int, top: int, right: int, bottom: int) -> str:
    image = _decode(image_b64).crop((left, top, right, bottom))
    return _encode(image)


def _add_noise(image_b64: str, amount: float = 0.05) -> str:
    image = _decode(image_b64).convert("RGB")
    pixels = image.load()
    width, height = image.size
    num_pixels = int(width * height * amount)
    for _ in range(num_pixels):
        x = random.randrange(width)
        y = random.randrange(height)
        value = 0 if random.random() < 0.5 else 255
        pixels[x, y] = (value, value, value)
    return _encode(image)


def _paste(base_image_b64: str, region_b64: str, left: int, top: int) -> str:
    base_image = _decode(base_image_b64).convert("RGB")
    region = _decode(region_b64).convert("RGB")
    base_image.paste(region, (left, top))
    return _encode(base_image)


@mcp.tool()
def blur(image_b64: str, radius: float = 2.0) -> str:
    """Apply Gaussian blur to an image. Returns a base64-encoded PNG."""
    return _blur(image_b64, radius)


@mcp.tool()
def rotate(image_b64: str, angle: float, expand: bool = True) -> str:
    """Rotate an image counter-clockwise by the given angle in degrees. Returns a base64-encoded PNG.

    If expand is True (default), the output image grows to fit the whole
    rotated result. If False, the output keeps the original image's
    dimensions and corners of the rotated content are clipped - useful when
    the result must be pasted back into a fixed-size region.
    """
    return _rotate(image_b64, angle, expand)


@mcp.tool()
def flip(image_b64: str, direction: str = "horizontal") -> str:
    """Flip an image. direction is 'horizontal' (mirror left-right) or 'vertical' (upside down). Returns a base64-encoded PNG."""
    return _flip(image_b64, direction)


@mcp.tool()
def resize(image_b64: str, width: int, height: int) -> str:
    """Resize an image to the given width and height in pixels. Returns a base64-encoded PNG."""
    return _resize(image_b64, width, height)


@mcp.tool()
def crop(image_b64: str, left: int, top: int, right: int, bottom: int) -> str:
    """Crop an image to the bounding box (left, top, right, bottom) in pixels. Returns a base64-encoded PNG."""
    return _crop(image_b64, left, top, right, bottom)


@mcp.tool()
def add_noise(image_b64: str, amount: float = 0.05) -> str:
    """Add salt-and-pepper noise to an image. amount is the fraction of pixels affected (0-1). Returns a base64-encoded PNG."""
    return _add_noise(image_b64, amount)


@mcp.tool()
def paste(base_image_b64: str, region_b64: str, left: int, top: int) -> str:
    """Paste region_b64 into base_image_b64 at position (left, top), overwriting that area.

    Used to composite a transformed sub-region back into the full original
    image (e.g. after blurring just one detected object). Returns a
    base64-encoded PNG of the full composited image, same size as base_image_b64.
    """
    return _paste(base_image_b64, region_b64, left, top)


if __name__ == "__main__":
    # fastmcp rejects requests whose Host header isn't explicitly allowed
    # (DNS-rebinding protection). The agent reaches this server over the
    # docker-compose network as "img-proc-mcp:9000", which isn't covered
    # by fastmcp's localhost-only defaults, so it must be listed here.
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=9000,
        allowed_hosts=["img-proc-mcp:9000", "localhost:9000", "127.0.0.1:9000"],
    )
