import base64
import io
import os
import random
import uuid

import boto3
from fastmcp import FastMCP
from PIL import Image, ImageFilter

mcp = FastMCP("img-proc")

AWS_REGION = os.environ.get("AWS_REGION")
AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET")


def _s3_client():
    if not AWS_REGION:
        raise RuntimeError("AWS_REGION environment variable is required")
    if not AWS_S3_BUCKET:
        raise RuntimeError("AWS_S3_BUCKET environment variable is required")
    # No access key / secret is passed here - boto3 resolves credentials from
    # its default chain (EC2 instance/IAM role, or the ~/.aws mount in
    # docker-compose), never from a hard-coded value in this file.
    return boto3.client("s3", region_name=AWS_REGION)


def _download_image_from_s3(s3_key: str) -> Image.Image:
    response = _s3_client().get_object(Bucket=AWS_S3_BUCKET, Key=s3_key)
    return Image.open(io.BytesIO(response["Body"].read()))


def _upload_image_to_s3(image: Image.Image, s3_key: str) -> None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    _s3_client().put_object(Bucket=AWS_S3_BUCKET, Key=s3_key, Body=buffer.getvalue(), ContentType="image/png")


def _result_s3_key(prefix: str) -> str:
    return f"img-proc-mcp/{prefix}/{uuid.uuid4()}.png"


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


def _apply_from_s3(input_s3_key: str, operation) -> str:
    """Download input_s3_key, run `operation` (one of the _verb functions
    above, called with the decoded image's base64 form as its first
    argument), upload the PNG result under a fresh key, return that key.
    Keeps the pixel-math functions themselves untouched - only I/O moves."""
    image = _download_image_from_s3(input_s3_key)
    result_b64 = operation(_encode(image))
    result_image = _decode(result_b64)
    output_s3_key = _result_s3_key(operation.__name__.strip("_"))
    _upload_image_to_s3(result_image, output_s3_key)
    return output_s3_key


@mcp.tool()
def blur(input_s3_key: str, radius: float = 2.0) -> str:
    """Apply Gaussian blur to the image at input_s3_key. Uploads the result to a
    new S3 key and returns output_s3_key - the MCP server never receives or
    returns raw image bytes, only S3 key references."""
    return _apply_from_s3(input_s3_key, lambda b64: _blur(b64, radius))


@mcp.tool()
def rotate(input_s3_key: str, angle: float, expand: bool = True) -> str:
    """Rotate the image at input_s3_key counter-clockwise by the given angle in
    degrees. Uploads the result to a new S3 key and returns output_s3_key.

    If expand is True (default), the output image grows to fit the whole
    rotated result. If False, the output keeps the original image's
    dimensions and corners of the rotated content are clipped - useful when
    the result must be pasted back into a fixed-size region.
    """
    return _apply_from_s3(input_s3_key, lambda b64: _rotate(b64, angle, expand))


@mcp.tool()
def flip(input_s3_key: str, direction: str = "horizontal") -> str:
    """Flip the image at input_s3_key. direction is 'horizontal' (mirror
    left-right) or 'vertical' (upside down). Uploads the result to a new S3
    key and returns output_s3_key."""
    return _apply_from_s3(input_s3_key, lambda b64: _flip(b64, direction))


@mcp.tool()
def resize(input_s3_key: str, width: int, height: int) -> str:
    """Resize the image at input_s3_key to the given width and height in
    pixels. Uploads the result to a new S3 key and returns output_s3_key."""
    return _apply_from_s3(input_s3_key, lambda b64: _resize(b64, width, height))


@mcp.tool()
def crop(input_s3_key: str, left: int, top: int, right: int, bottom: int) -> str:
    """Crop the image at input_s3_key to the bounding box (left, top, right,
    bottom) in pixels. Uploads the result to a new S3 key and returns
    output_s3_key."""
    return _apply_from_s3(input_s3_key, lambda b64: _crop(b64, left, top, right, bottom))


@mcp.tool()
def add_noise(input_s3_key: str, amount: float = 0.05) -> str:
    """Add salt-and-pepper noise to the image at input_s3_key. amount is the
    fraction of pixels affected (0-1). Uploads the result to a new S3 key and
    returns output_s3_key."""
    return _apply_from_s3(input_s3_key, lambda b64: _add_noise(b64, amount))


@mcp.tool()
def paste(base_s3_key: str, region_s3_key: str, left: int, top: int) -> str:
    """Paste the image at region_s3_key into the image at base_s3_key at
    position (left, top), overwriting that area. Used to composite a
    transformed sub-region back into the full original image (e.g. after
    blurring just one detected object). Uploads the composited full image to
    a new S3 key, same size as the base image, and returns output_s3_key.
    """
    base_image = _download_image_from_s3(base_s3_key)
    region_image = _download_image_from_s3(region_s3_key)
    result_b64 = _paste(_encode(base_image), _encode(region_image), left, top)
    output_s3_key = _result_s3_key("paste")
    _upload_image_to_s3(_decode(result_b64), output_s3_key)
    return output_s3_key


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
