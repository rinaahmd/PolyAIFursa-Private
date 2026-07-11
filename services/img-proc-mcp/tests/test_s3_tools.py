import io

import boto3
import pytest
from moto import mock_aws
from PIL import Image

import app as img_proc_app

BUCKET = "test-img-proc-bucket"


@pytest.fixture(autouse=True)
def _s3_env(monkeypatch):
    monkeypatch.setattr(img_proc_app, "AWS_REGION", "us-east-1")
    monkeypatch.setattr(img_proc_app, "AWS_S3_BUCKET", BUCKET)


@pytest.fixture
def s3_bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _put_png(client, key: str, image: Image.Image) -> None:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    client.put_object(Bucket=BUCKET, Key=key, Body=buffer.getvalue(), ContentType="image/png")


def _get_png(client, key: str) -> Image.Image:
    body = client.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return Image.open(io.BytesIO(body))


def test_blur_tool_downloads_from_s3_uploads_result_and_returns_output_key(s3_bucket):
    original = Image.new("RGB", (10, 10), (255, 0, 0))
    _put_png(s3_bucket, "input.png", original)

    output_key = img_proc_app.blur("input.png", radius=2.0)

    assert output_key != "input.png"
    assert output_key.startswith("img-proc-mcp/")
    result_image = _get_png(s3_bucket, output_key)
    assert result_image.size == (10, 10)


def test_rotate_tool_round_trips_through_s3(s3_bucket):
    original = Image.new("RGB", (10, 20), (0, 255, 0))
    _put_png(s3_bucket, "input.png", original)

    output_key = img_proc_app.rotate("input.png", angle=90, expand=True)

    result_image = _get_png(s3_bucket, output_key)
    assert result_image.size == (20, 10)


def test_flip_tool_round_trips_through_s3(s3_bucket):
    image = Image.new("RGB", (4, 4), (0, 0, 0))
    image.putpixel((0, 0), (255, 255, 255))
    _put_png(s3_bucket, "input.png", image)

    output_key = img_proc_app.flip("input.png", direction="horizontal")

    result_image = _get_png(s3_bucket, output_key).convert("RGB")
    assert result_image.getpixel((3, 0)) == (255, 255, 255)


def test_resize_tool_round_trips_through_s3(s3_bucket):
    original = Image.new("RGB", (10, 10), (1, 2, 3))
    _put_png(s3_bucket, "input.png", original)

    output_key = img_proc_app.resize("input.png", width=40, height=20)

    result_image = _get_png(s3_bucket, output_key)
    assert result_image.size == (40, 20)


def test_crop_tool_round_trips_through_s3(s3_bucket):
    original = Image.new("RGB", (20, 20), (1, 2, 3))
    _put_png(s3_bucket, "input.png", original)

    output_key = img_proc_app.crop("input.png", left=5, top=5, right=15, bottom=15)

    result_image = _get_png(s3_bucket, output_key)
    assert result_image.size == (10, 10)


def test_add_noise_tool_round_trips_through_s3(s3_bucket):
    original = Image.new("RGB", (10, 10), (255, 255, 255))
    _put_png(s3_bucket, "input.png", original)

    output_key = img_proc_app.add_noise("input.png", amount=0.5)

    result_image = _get_png(s3_bucket, output_key)
    assert result_image.size == (10, 10)


def test_paste_tool_downloads_both_inputs_and_uploads_composited_result(s3_bucket):
    base = Image.new("RGB", (20, 20), (0, 0, 0))
    region = Image.new("RGB", (5, 5), (255, 255, 255))
    _put_png(s3_bucket, "base.png", base)
    _put_png(s3_bucket, "region.png", region)

    output_key = img_proc_app.paste("base.png", "region.png", left=10, top=10)

    result_image = _get_png(s3_bucket, output_key).convert("RGB")
    assert result_image.size == (20, 20)
    assert result_image.getpixel((12, 12)) == (255, 255, 255)
    assert result_image.getpixel((0, 0)) == (0, 0, 0)


def test_blur_tool_raises_when_s3_not_configured(monkeypatch):
    monkeypatch.setattr(img_proc_app, "AWS_REGION", None)
    monkeypatch.setattr(img_proc_app, "AWS_S3_BUCKET", None)

    with pytest.raises(RuntimeError, match="AWS_REGION"):
        img_proc_app.blur("input.png", radius=2.0)
