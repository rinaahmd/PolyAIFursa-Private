import base64
import io

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


@mcp.tool()
def blur(image_b64: str, radius: float = 2.0) -> str:
    """Apply Gaussian blur to an image. Returns a base64-encoded PNG."""
    return _blur(image_b64, radius)


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=9000)
