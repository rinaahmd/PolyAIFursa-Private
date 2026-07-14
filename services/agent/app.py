import asyncio
import base64
import binascii
import io
import json
import logging
import os
import re
import time
import uuid
from contextlib import suppress
from contextvars import ContextVar
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError
from dotenv import load_dotenv
from PIL import Image, ImageOps
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import BaseModel

YOLO_SERVICE_URL = os.environ.get("YOLO_SERVICE_URL", "http://yolo:8080")
IMG_PROC_MCP_URL = os.environ.get("IMG_PROC_MCP_URL", "http://img-proc-mcp:9000/mcp")
MODEL = os.environ.get("MODEL")
AWS_REGION = os.environ.get("AWS_REGION")
AWS_S3_BUCKET = os.environ.get("AWS_S3_BUCKET")

SYSTEM_PROMPT = (
    "You are an AI vision assistant. You help users understand, analyze, and transform images. "
    "Use the available tools to extract information from images or to apply requested edits "
    "(blur, rotate, flip, resize, crop, add noise, etc.). "
    "You must call the matching tool for every such request - never claim you performed an "
    "operation without actually calling the tool for it, even if a similar request was handled "
    "earlier in the conversation. "
    "There are two families of edit tools: blur_image/rotate_image/flip_image/add_noise_image "
    "affect the WHOLE image - use these directly, with no need to call detect_objects first, "
    "whenever the request is about 'the image'/'this image'/'the whole image'. "
    "blur_object/rotate_object/flip_object/add_noise_object affect just ONE detected object - "
    "use these only when the user names a specific object (e.g. 'the second dog', 'the detected "
    "car'): call detect_objects first, then pass the object's label plus rank_from_left or "
    "rank_from_right copied directly from the user's wording (e.g. 'the second dog from the "
    "right' -> label='dog', rank_from_right=2). Never try to pick the object_index yourself by "
    "comparing box coordinates or ranks across a list - that reasoning is unreliable; let the "
    "rank_from_left/rank_from_right parameters do it. "
    "Edits build on each other: if the user already blurred the image and now asks to rotate it, "
    "rotate the blurred version - each new edit applies to the current state of the image, not "
    "back to the original upload."
)

_current_image_b64: ContextVar[Optional[str]] = ContextVar("current_image_b64", default=None)
_current_chat_id: ContextVar[Optional[str]] = ContextVar("current_chat_id", default=None)


def _normalized_chat_id() -> str:
    return (_current_chat_id.get() or "chat").strip() or "chat"


def _normalize_image_orientation(image_b64: str) -> str:
    """Bake EXIF orientation into the pixels once on upload, so every later consumer agrees on the same grid."""
    try:
        image_bytes = base64.b64decode(image_b64)
        with Image.open(io.BytesIO(image_bytes)) as image:
            normalized = ImageOps.exif_transpose(image) or image
            buffer = io.BytesIO()
            normalized.save(buffer, format="PNG")
            return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception:
        logger.exception("Failed to normalize image orientation; using the upload as-is")
        return image_b64


# Most recent edit result per chat, so edits build on each other. Keyed by chat_id, not a ContextVar,
# because tool calls run in a copied context (see _processed_images below).
_current_working_image: dict[str, str] = {}

# Per-chat, per-operation object reference parsed by chat() from the user's wording; overrides the
# model's own label/rank args, since the model has been observed to still get them wrong.
_object_reference_hints_by_chat: dict[str, dict[str, dict]] = {}


def _current_image_s3_key(chat_id: str) -> str:
    return f"{chat_id}/current.png"


def _scratch_base_s3_key(chat_id: str) -> str:
    """Stable per-chat staging key for an MCP call, reused on every edit (only current.png persists across turns)."""
    return f"{chat_id}/scratch/base.png"


def _scratch_region_s3_key(chat_id: str) -> str:
    return f"{chat_id}/scratch/region.png"


def _get_current_image() -> Optional[str]:
    """Resolve the image to operate on: in-memory cache, then S3, then this request's own history."""
    chat_id = _normalized_chat_id()
    cached = _current_working_image.get(chat_id)
    if cached is not None:
        return cached

    restored = _download_current_image_from_s3(chat_id)
    if restored is not None:
        _current_working_image[chat_id] = restored
        return restored

    return _current_image_b64.get()


# Populated by detect_objects; lets blur_object etc. resolve "the second dog" to a box in code.
_detections_by_chat: dict[str, list[dict]] = {}

# (width, height) YOLO measured boxes against, for _resolve_object_box to rescale if it's changed since.
_detection_image_size_by_chat: dict[str, tuple[int, int]] = {}


def _image_size(image_b64: str) -> Optional[tuple[int, int]]:
    try:
        with Image.open(io.BytesIO(base64.b64decode(image_b64))) as image:
            return image.size
    except Exception:
        return None


def _upload_bytes_to_s3(data: bytes, s3_key: str, content_type: str = "image/jpeg") -> None:
    if not AWS_REGION or not AWS_S3_BUCKET:
        raise RuntimeError("AWS_REGION and AWS_S3_BUCKET environment variables are required")

    s3_client = boto3.client("s3", region_name=AWS_REGION)
    s3_client.put_object(Bucket=AWS_S3_BUCKET, Key=s3_key, Body=data, ContentType=content_type)


def _download_bytes_from_s3(s3_key: str) -> Optional[bytes]:
    if not AWS_REGION or not AWS_S3_BUCKET:
        return None

    s3_client = boto3.client("s3", region_name=AWS_REGION)
    try:
        response = s3_client.get_object(Bucket=AWS_S3_BUCKET, Key=s3_key)
        return response["Body"].read()
    except (BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError):
        return None


def _persist_current_image_to_s3(chat_id: str, image_b64: str) -> None:
    """Best-effort write-through - persistence failures must not fail the caller's request."""
    s3_key = _current_image_s3_key(chat_id)
    try:
        _upload_bytes_to_s3(base64.b64decode(image_b64), s3_key, content_type="image/png")
    except RuntimeError:
        logger.warning("Skipping current image S3 persistence: S3 is not configured")
    except (BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError, binascii.Error, ValueError):
        logger.exception("Failed to persist current image to S3 (key=%s)", s3_key)


def _download_current_image_from_s3(chat_id: str) -> Optional[str]:
    data = _download_bytes_from_s3(_current_image_s3_key(chat_id))
    return base64.b64encode(data).decode("utf-8") if data is not None else None


def _delete_current_image_from_s3(chat_id: str) -> bool:
    """Unused by the reset path on purpose (missing DeleteObject IAM permission caused a stale-image bug);
    kept for test_new_upload_does_not_depend_on_s3_delete_succeeding."""
    if not AWS_REGION or not AWS_S3_BUCKET:
        return False
    s3_client = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3_client.delete_object(Bucket=AWS_S3_BUCKET, Key=_current_image_s3_key(chat_id))
        return True
    except (BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError):
        logger.exception("Failed to delete current image from S3 for chat_id=%s", chat_id)
        return False


def _fetch_detections(prediction_id: str) -> list[dict]:
    """/predict returns labels only; box coordinates live behind a separate GET /prediction/{uid} call."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{YOLO_SERVICE_URL}/prediction/{prediction_id}")
            response.raise_for_status()
        detection_objects = response.json().get("detection_objects", [])
    except (httpx.HTTPError, ValueError, TypeError):
        return []

    detections = []
    for index, obj in enumerate(detection_objects):
        try:
            box = json.loads(obj["box"])
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
        detections.append({"index": index, "label": obj.get("label"), "score": obj.get("score"), "box": box})

    _add_left_right_rank(detections)
    return detections


def _add_left_right_rank(detections: list[dict]) -> None:
    """Rank each detection among same-label objects by horizontal center, so "second dog from
    the right" resolves in code instead of relying on the model's arithmetic."""
    by_label: dict[str, list[dict]] = {}
    for detection in detections:
        by_label.setdefault(detection["label"], []).append(detection)

    for same_label_detections in by_label.values():
        same_label_detections.sort(key=lambda d: (d["box"][0] + d["box"][2]) / 2)
        count = len(same_label_detections)
        for rank, detection in enumerate(same_label_detections, start=1):
            detection["rank_from_left"] = rank
            detection["rank_from_right"] = count - rank + 1


@tool
def detect_objects() -> str:
    """Detect and identify objects in the image provided by the user using YOLO object detection.

    Result includes a "detections" list with "label", "box", "rank_from_left",
    "rank_from_right" per object. To edit a specific one (e.g. "the second dog
    from the right"), call blur_object/rotate_object/flip_object/add_noise_object
    with label="dog", rank_from_right=2 copied straight from the user's wording -
    do not resolve this to an index yourself.
    """
    image_b64 = _get_current_image()
    if not image_b64:
        return json.dumps({"error": "No image was provided by the user."})

    try:
        image_bytes = base64.b64decode(image_b64)
    except (binascii.Error, ValueError) as exc:
        return json.dumps({"error": f"Invalid image encoding: {exc}"})

    prediction_id = str(uuid.uuid4())
    chat_id = _normalized_chat_id()
    image_s3_key = f"{chat_id}/{prediction_id}/original/image.jpg"

    try:
        _upload_bytes_to_s3(image_bytes, image_s3_key)
    except RuntimeError as exc:
        return json.dumps({"error": f"S3 configuration error: {exc}"})
    except (BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError) as exc:
        return json.dumps({"error": f"Failed to upload image to S3: {exc}"})

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{YOLO_SERVICE_URL}/predict",
                json={"image_s3_key": image_s3_key, "prediction_id": prediction_id},
            )
            response.raise_for_status()
        result = response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        return json.dumps({"error": f"YOLO service returned an error: {detail}"})
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"Failed to call YOLO service: {exc}"})

    detections = _fetch_detections(prediction_id)
    _detections_by_chat[chat_id] = detections

    image_width = result.get("image_width")
    image_height = result.get("image_height")
    if isinstance(image_width, int) and isinstance(image_height, int):
        _detection_image_size_by_chat[chat_id] = (image_width, image_height)
    else:
        _detection_image_size_by_chat.pop(chat_id, None)

    result["detections"] = detections
    return json.dumps(result)


def _extract_mcp_text(result: Any) -> str:
    """Unwrap an MCP result (plain string, or list of {"type": "text", "text": ...} blocks) with no sanitization -
    this may carry base64 image bytes."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(result)


async def _call_mcp_tool(tool_name: str, arguments: dict) -> str:
    """Call an img-proc-mcp tool by S3 key(s) - no image bytes cross the agent<->MCP call itself."""
    client = MultiServerMCPClient(
        {
            "img-proc": {
                "url": IMG_PROC_MCP_URL,
                "transport": "http",
            }
        }
    )
    tools = await client.get_tools()
    mcp_tool = next(t for t in tools if t.name == tool_name)
    result = await mcp_tool.ainvoke(arguments)
    return _extract_mcp_text(result)


# Tool calls run in a copied context, so a ContextVar write never propagates back to run_agent;
# stash results here instead, keyed by an id the tool hands back in its JSON output.
_processed_images: dict[str, str] = {}


async def _call_mcp_object_op(operation: str, arguments: dict, chat_id: str, input_s3_key: str, box: list) -> str:
    """crop -> operation -> paste via S3 keys. Returns input_s3_key, since paste overwrites it in place."""
    left, top, right, bottom = (int(round(v)) for v in box)
    region_s3_key = _scratch_region_s3_key(chat_id)
    await _call_mcp_tool(
        "crop",
        {"input_s3_key": input_s3_key, "output_s3_key": region_s3_key, "left": left, "top": top, "right": right, "bottom": bottom},
    )
    await _call_mcp_tool(operation, {"input_s3_key": region_s3_key, "output_s3_key": region_s3_key, **arguments})
    return await _call_mcp_tool(
        "paste",
        {
            "base_s3_key": input_s3_key,
            "region_s3_key": region_s3_key,
            "output_s3_key": input_s3_key,
            "left": left,
            "top": top,
        },
    )


def _scale_box(box: list, from_size: tuple[int, int], to_size: tuple[int, int]) -> list:
    """Scale a box proportionally between two image sizes (e.g. image was resized since detect_objects)."""
    from_width, from_height = from_size
    to_width, to_height = to_size
    if from_width <= 0 or from_height <= 0:
        return box
    scale_x = to_width / from_width
    scale_y = to_height / from_height
    if scale_x == 1.0 and scale_y == 1.0:
        return box
    left, top, right, bottom = box
    return [left * scale_x, top * scale_y, right * scale_x, bottom * scale_y]


def _resolve_object_box(
    label: str, rank_from_left: int | None, rank_from_right: int | None, current_image_size: Optional[tuple[int, int]]
) -> tuple[list | None, str | None]:
    """Resolve (label, rank) to a box entirely in code - the model has been observed to still pick the
    wrong object even given pre-computed ranks. Rescales if the image size no longer matches YOLO's."""
    chat_id = _normalized_chat_id()
    candidates = [d for d in _detections_by_chat.get(chat_id, []) if d["label"] == label]
    if not candidates:
        return None, f"No detected object labeled '{label}'. Call detect_objects first."

    rank = rank_from_left if rank_from_left is not None else (rank_from_right or 1)
    rank_field = "rank_from_left" if rank_from_left is not None else "rank_from_right"
    match = next((d for d in candidates if d[rank_field] == rank), None)
    if match is None:
        return None, f"No '{label}' with {rank_field}={rank}. There are only {len(candidates)}."

    box = match["box"]
    detection_size = _detection_image_size_by_chat.get(chat_id)
    if detection_size is not None and current_image_size is not None and detection_size != current_image_size:
        box = _scale_box(box, detection_size, current_image_size)
    return box, None


def _run_image_op(
    operation: str,
    arguments: dict,
    label: str | None = None,
    rank_from_left: int | None = None,
    rank_from_right: int | None = None,
) -> str:
    """Choke point every blur/rotate/flip/resize/crop/add_noise tool routes through: resolves the current
    image, stages it in S3, calls img-proc-mcp, downloads the result, updates the chat's working image."""
    chat_id = _normalized_chat_id()
    image_b64 = _get_current_image()
    if not image_b64:
        return json.dumps({"error": "No image was provided by the user."})

    if label is not None:
        # chat()'s parsed hint is ground truth - override the model's args with it.
        hint = _object_reference_hints_by_chat.get(chat_id, {}).get(operation)
        if hint is not None:
            label = hint["label"]
            rank_from_left = hint["rank_from_left"]
            rank_from_right = hint["rank_from_right"]

    box = None
    if label is not None:
        current_image_size = _image_size(image_b64)
        box, error = _resolve_object_box(label, rank_from_left, rank_from_right, current_image_size)
        if error:
            return json.dumps({"error": error})

    try:
        input_s3_key = _scratch_base_s3_key(chat_id)
        _upload_bytes_to_s3(base64.b64decode(image_b64), input_s3_key, content_type="image/png")

        if box is None:
            output_s3_key = asyncio.run(
                _call_mcp_tool(operation, {"input_s3_key": input_s3_key, "output_s3_key": input_s3_key, **arguments})
            )
        else:
            output_s3_key = asyncio.run(
                _call_mcp_object_op(operation, arguments, chat_id, input_s3_key, box)
            )

        result_bytes = _download_bytes_from_s3(output_s3_key)
        if result_bytes is None:
            return json.dumps({"error": f"img-proc-mcp did not return a readable result for {operation}."})
        result_b64 = base64.b64encode(result_bytes).decode("utf-8")
    except Exception as exc:
        logger.exception("%s: failed to call img-proc MCP server", operation)
        return json.dumps({"error": f"Failed to {operation} image: {exc}"})

    _current_working_image[chat_id] = result_b64  # so the next edit builds on this one
    operation_id = str(uuid.uuid4())
    _persist_current_image_to_s3(chat_id, result_b64)
    _processed_images[operation_id] = result_b64
    result = {"status": "ok", "operation": operation, "operation_id": operation_id, **arguments}
    if label is not None:
        result["label"] = label
    return json.dumps(result)


@tool
def blur_image(radius: float = 2.0) -> str:
    """Apply a Gaussian blur to the whole image provided by the user (or its current edited state).

    Use this for requests about "the image"/"this image" as a whole. For a
    request naming one specific detected object (e.g. "blur the second dog"),
    use blur_object instead.

    Args:
        radius: Blur strength in pixels. There is no fixed maximum - use small
            values like 1-3 for a subtle/light blur, and larger values like
            8-15 or more for a heavy/strong blur, based on what the user asks for.
    """
    return _run_image_op("blur", {"radius": radius})


@tool
def blur_object(label: str, rank_from_left: int | None = None, rank_from_right: int | None = None, radius: float = 2.0) -> str:
    """Apply a Gaussian blur to just one detected object in the image.

    Call detect_objects first. Do not pick an index yourself - just copy the
    label and position wording directly from the user's request.

    Args:
        label: The object's label exactly as detect_objects reported it (e.g. "dog", "car").
        rank_from_left: Position counting from the left, 1-based (e.g. "the first dog" -> 1).
        rank_from_right: Position counting from the right, 1-based (e.g. "the second dog from the right" -> 2).
            Set exactly one of rank_from_left/rank_from_right, matching the user's wording. If the
            user just says "the dog"/"the detected car" with no position, leave both unset.
        radius: Blur strength in pixels, same scale as blur_image.
    """
    return _run_image_op(
        "blur", {"radius": radius}, label=label, rank_from_left=rank_from_left, rank_from_right=rank_from_right
    )


@tool
def rotate_image(angle: float) -> str:
    """Rotate the whole image provided by the user (or its current edited state) counter-clockwise.

    Use this for requests about "the image"/"this image" as a whole. For a
    request naming one specific detected object (e.g. "rotate the detected
    car"), use rotate_object instead.

    Args:
        angle: Rotation angle in degrees, e.g. 90 for a quarter turn.
    """
    return _run_image_op("rotate", {"angle": angle, "expand": True})


@tool
def rotate_object(label: str, angle: float, rank_from_left: int | None = None, rank_from_right: int | None = None) -> str:
    """Rotate just one detected object in the image counter-clockwise. The object keeps its
    original size (corners of the rotated content get clipped) so it can be placed back.

    Call detect_objects first. Do not pick an index yourself - just copy the
    label and position wording directly from the user's request.

    Args:
        label: The object's label exactly as detect_objects reported it (e.g. "dog", "car").
        angle: Rotation angle in degrees, e.g. 90 for a quarter turn.
        rank_from_left: Position counting from the left, 1-based (e.g. "the first dog" -> 1).
        rank_from_right: Position counting from the right, 1-based (e.g. "the second dog from the right" -> 2).
            Set exactly one of rank_from_left/rank_from_right, matching the user's wording. If the
            user just says "the dog"/"the detected car" with no position, leave both unset.
    """
    return _run_image_op(
        "rotate",
        {"angle": angle, "expand": False},
        label=label,
        rank_from_left=rank_from_left,
        rank_from_right=rank_from_right,
    )


@tool
def flip_image(direction: str = "horizontal") -> str:
    """Flip the whole image provided by the user (or its current edited state).

    Use this for requests about "the image"/"this image" as a whole. For a
    request naming one specific detected object (e.g. "flip the second dog"),
    use flip_object instead.

    Args:
        direction: 'horizontal' to mirror left-right, or 'vertical' to flip upside down.
    """
    return _run_image_op("flip", {"direction": direction})


@tool
def flip_object(label: str, direction: str = "horizontal", rank_from_left: int | None = None, rank_from_right: int | None = None) -> str:
    """Flip just one detected object in the image.

    Call detect_objects first. Do not pick an index yourself - just copy the
    label and position wording directly from the user's request.

    Args:
        label: The object's label exactly as detect_objects reported it (e.g. "dog", "car").
        direction: 'horizontal' to mirror left-right, or 'vertical' to flip upside down.
        rank_from_left: Position counting from the left, 1-based (e.g. "the first dog" -> 1).
        rank_from_right: Position counting from the right, 1-based (e.g. "the second dog from the right" -> 2).
            Set exactly one of rank_from_left/rank_from_right, matching the user's wording. If the
            user just says "the dog"/"the detected car" with no position, leave both unset.
    """
    return _run_image_op(
        "flip", {"direction": direction}, label=label, rank_from_left=rank_from_left, rank_from_right=rank_from_right
    )


@tool
def resize_image(width: int, height: int) -> str:
    """Resize the whole image provided by the user (or its current edited state) to an exact width and height in pixels.

    Args:
        width: Target width in pixels.
        height: Target height in pixels.
    """
    return _run_image_op("resize", {"width": width, "height": height})


@tool
def crop_image(left: int, top: int, right: int, bottom: int) -> str:
    """Crop the whole image provided by the user (or its current edited state) to a bounding box, in pixels from the top-left corner.

    Args:
        left: Left edge of the box (x, from the left).
        top: Top edge of the box (y, from the top).
        right: Right edge of the box (x, from the left).
        bottom: Bottom edge of the box (y, from the top).
    """
    return _run_image_op("crop", {"left": left, "top": top, "right": right, "bottom": bottom})


@tool
def add_noise_image(amount: float = 0.05) -> str:
    """Add salt-and-pepper noise to the whole image provided by the user (or its current edited state).

    Use this for requests about "the image"/"this image" as a whole. For a
    request naming one specific detected object (e.g. "add noise to the
    detected car"), use add_noise_object instead.

    Args:
        amount: Fraction of pixels to affect, between 0 and 1 (e.g. 0.05 = 5% of pixels).
    """
    return _run_image_op("add_noise", {"amount": amount})


@tool
def add_noise_object(label: str, amount: float = 0.05, rank_from_left: int | None = None, rank_from_right: int | None = None) -> str:
    """Add salt-and-pepper noise to just one detected object in the image.

    Call detect_objects first. Do not pick an index yourself - just copy the
    label and position wording directly from the user's request.

    Args:
        label: The object's label exactly as detect_objects reported it (e.g. "dog", "car").
        amount: Fraction of pixels to affect, between 0 and 1 (e.g. 0.05 = 5% of pixels).
        rank_from_left: Position counting from the left, 1-based (e.g. "the first dog" -> 1).
        rank_from_right: Position counting from the right, 1-based (e.g. "the second dog from the right" -> 2).
            Set exactly one of rank_from_left/rank_from_right, matching the user's wording. If the
            user just says "the dog"/"the detected car" with no position, leave both unset.
    """
    return _run_image_op(
        "add_noise", {"amount": amount}, label=label, rank_from_left=rank_from_left, rank_from_right=rank_from_right
    )


# Registry: map tool name -> tool function
TOOLS = {
    detect_objects.name: detect_objects,
    blur_image.name: blur_image,
    blur_object.name: blur_object,
    rotate_image.name: rotate_image,
    rotate_object.name: rotate_object,
    flip_image.name: flip_image,
    flip_object.name: flip_object,
    resize_image.name: resize_image,
    crop_image.name: crop_image,
    add_noise_image.name: add_noise_image,
    add_noise_object.name: add_noise_object,
}

IMAGE_OP_TOOL_NAMES = {
    blur_image.name,
    blur_object.name,
    rotate_image.name,
    rotate_object.name,
    flip_image.name,
    flip_object.name,
    resize_image.name,
    crop_image.name,
    add_noise_image.name,
    add_noise_object.name,
}


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    if isinstance(profile, dict):
        return profile

    for method_name in ("model_dump", "dict"):
        method = getattr(profile, method_name, None)
        if callable(method):
            dumped = method()
            if isinstance(dumped, dict):
                return dumped

    try:
        values = vars(profile)
    except TypeError:
        return {}

    if isinstance(values, dict):
        return {k: v for k, v in values.items() if not k.startswith("_")}
    return {}


def _coerce_int(value: Any) -> int | None:
    """bools are deliberately rejected since isinstance(True, int) is True in Python."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _pick_first_int(data: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    """keys covers different providers' naming conventions, e.g. "input_tokens" vs "inputTokens"."""
    for key in keys:
        value = _coerce_int(data.get(key))
        if value is not None:
            return value
    return None


def validate_model_profile(model_obj: Any, model_name: str) -> dict[str, Any]:
    """Raises RuntimeError unless the model declares tool_calling=True - this agent needs it to function."""
    profile = _profile_to_dict(getattr(model_obj, "profile", None))

    if profile.get("tool_calling") is not True:
        raise RuntimeError(
            f"Model '{model_name}' is incompatible: missing required feature 'tool_calling=True' in llm.profile."
        )

    # Only enforce structured_output when the key exists - some providers don't expose it.
    if "structured_output" in profile and profile.get("structured_output") is not True:
        raise RuntimeError(
            f"Model '{model_name}' is incompatible: missing required feature 'structured_output=True' in llm.profile."
        )

    return profile


def _extract_usage_metadata(response: AIMessage) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        return {"input": None, "output": None, "total": None}

    return {
        "input": _pick_first_int(usage, ("input_tokens", "inputTokens", "input_token_count", "inputTokenCount")),
        "output": _pick_first_int(usage, ("output_tokens", "outputTokens", "output_token_count", "outputTokenCount")),
        "total": _pick_first_int(usage, ("total_tokens", "totalTokens", "total_token_count", "totalTokenCount")),
    }


def _sum_optional(current: int | None, addition: int | None) -> int | None:
    if addition is None:
        return current
    if current is None:
        return addition
    return current + addition


# Client-side throttling helps reduce bursty calls that can trigger provider 429 limits.
rate_limiter = InMemoryRateLimiter(
    requests_per_second=0.2,
    check_every_n_seconds=0.1,
    max_bucket_size=2,
)

llm = None
llm_max_input_tokens: int | None = None
llm_with_tools = None


def _initialize_llm() -> None:
    initialized_llm = init_chat_model(
        MODEL,
        model_provider="bedrock",
        region_name=AWS_REGION,
        temperature=0,
        rate_limiter=rate_limiter,
    )
    initialized_profile = validate_model_profile(initialized_llm, MODEL or "unknown")
    globals().update(
        {
            "llm": initialized_llm,
            "llm_max_input_tokens": _coerce_int(initialized_profile.get("max_input_tokens")),
            "llm_with_tools": initialized_llm.bind_tools(list(TOOLS.values())),
        }
    )


def _get_llm_with_tools():
    if llm_with_tools is None:
        _initialize_llm()
    return llm_with_tools


def _stringify_content(content) -> str:
    if isinstance(content, str):
        return _sanitize_response_text(content)
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])
        return _sanitize_response_text("\n".join(text_parts))
    if content is None:
        return ""
    return _sanitize_response_text(str(content))


def _sanitize_response_text(text: str) -> str:
    cleaned = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # The model sometimes wraps its final answer in a literal <response> tag
    # instead of just returning plain text - keep the text, drop the tags.
    cleaned = re.sub(r"</?response>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"!\[[^\]]*\]\(data:image/[^)]+\)", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _parse_tool_json(content) -> Optional[dict]:
    try:
        parsed = json.loads(_stringify_content(content))
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _fetch_annotated_image(prediction_id: str) -> Optional[str]:
    image_url = f"{YOLO_SERVICE_URL}/prediction/{prediction_id}/image"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(image_url)
            response.raise_for_status()
        return base64.b64encode(response.content).decode("utf-8")
    except (httpx.HTTPError, ValueError, TypeError):
        return None


class ContentBlockedError(Exception):
    """Raised when Bedrock refuses a response - an in-band content filter (200 response, zero tool
    calls) or a thrown ClientError (Guardrail, ValidationException, etc)."""

    def __init__(self, block_kind: str, detail: str, tools_called: Optional[list[str]] = None):
        self.block_kind = block_kind  # "input_image" | "text_prompt" | "model_output" | "guardrail" | "other"
        self.detail = detail
        self.tools_called = tools_called or []  # tools already run this turn before the block
        super().__init__(detail)


# Never includes exc.detail (may echo AWS error text) so nothing provider-internal reaches the frontend.
_BLOCK_KIND_MESSAGES = {
    "input_image": "The provider blocked the uploaded image.",
    "text_prompt": "The provider blocked the text instruction.",
    "model_output": "The provider blocked its own generated response. Please try again or rephrase your request.",
    "guardrail": "A configured content safety guardrail blocked this request.",
    "other": "The provider blocked this request for a content-safety reason.",
}


# Best-effort message-substring classification - Bedrock has no clean single field for block reason.
_IMAGE_BLOCK_MARKERS = ("image", "vision", "unsafe image", "media type")
_GUARDRAIL_MARKERS = ("guardrail",)
_PROMPT_BLOCK_MARKERS = ("content policy", "content filter", "harmful", "prompt")


def _classify_client_error(exc: ClientError) -> ContentBlockedError:
    """Classify a Bedrock ClientError. Only looks at exc.response['Error'], never the request's image bytes."""
    error = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
    error_code = error.get("Code", "Unknown")
    error_message = error.get("Message", str(exc))
    request_id = exc.response.get("ResponseMetadata", {}).get("RequestId") if hasattr(exc, "response") else None

    lower_message = error_message.lower()
    if any(marker in lower_message for marker in _GUARDRAIL_MARKERS):
        block_kind = "guardrail"
    elif any(marker in lower_message for marker in _IMAGE_BLOCK_MARKERS):
        block_kind = "input_image"
    elif error_code == "ValidationException" and any(marker in lower_message for marker in _PROMPT_BLOCK_MARKERS):
        block_kind = "text_prompt"
    else:
        block_kind = "other"

    logger.error(
        "Bedrock ClientError: type=%s aws_error_code=%s request_id=%s block_kind=%s message=%s",
        type(exc).__name__, error_code, request_id, block_kind, error_message,
    )
    return ContentBlockedError(block_kind, f"{error_code}: {error_message}")


def _invoke_with_content_filter_retry(llm_with_tools, messages: list, max_retries: int = 4) -> AIMessage:
    """Bedrock's content-safety layer intermittently blocks some multi-step edit requests - the same
    messages can succeed on retry. In-band refusals (200, zero tool_calls) are retried up to max_retries;
    a thrown ClientError is classified and re-raised as ContentBlockedError instead."""
    attempts = 0
    while True:
        attempts += 1
        try:
            response = llm_with_tools.invoke(messages)
        except ClientError as exc:
            raise _classify_client_error(exc) from exc

        stop_reason = response.response_metadata.get("stopReason") if response.response_metadata else None
        is_content_filtered = stop_reason in ("content_filtered", "guardrail_intervened") or (
            "blocked by our content filters" in _stringify_content(response.content).lower()
        )
        if response.tool_calls or not is_content_filtered:
            return response
        if attempts > max_retries:
            block_kind = "guardrail" if stop_reason == "guardrail_intervened" else "model_output"
            logger.error(
                "Bedrock in-band content block after %d attempts: stop_reason=%s block_kind=%s",
                attempts, stop_reason, block_kind,
            )
            raise ContentBlockedError(block_kind, f"stopReason={stop_reason}")
        logger.warning("LLM response blocked by content filters, retrying (attempt %d/%d)", attempts, max_retries)


def run_agent(history: list, max_iterations: int = 10) -> dict:
    """Simple ReAct loop: invoke LLM, run any requested tools, repeat until a plain text response or
    max_iterations. Raises ContentBlockedError if Bedrock blocks the request."""
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + history
    start_time = time.perf_counter()
    iterations = 0
    tools_called: list[str] = []
    prediction_id: Optional[str] = None
    annotated_image: Optional[str] = None
    processed_image: Optional[str] = None
    context_limit_exceeded = False
    final_response = ""
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_tokens: int | None = None
    token_limit_risk = False
    active_llm_with_tools = _get_llm_with_tools()

    for _ in range(max_iterations):
        iterations += 1
        try:
            response: AIMessage = _invoke_with_content_filter_retry(active_llm_with_tools, messages)
        except ContentBlockedError as exc:
            # So chat() can decide fallback eligibility by whether tools ran before the block.
            exc.tools_called = list(tools_called)
            logger.error(
                "run_agent: content block after %d iteration(s), tools_called_before_block=%s, block_kind=%s",
                iterations, tools_called, exc.block_kind,
            )
            raise

        usage = _extract_usage_metadata(response)
        total_input_tokens = _sum_optional(total_input_tokens, usage["input"])
        total_output_tokens = _sum_optional(total_output_tokens, usage["output"])
        total_tokens = _sum_optional(total_tokens, usage["total"])

        if llm_max_input_tokens is not None and usage["input"] is not None and usage["input"] >= int(llm_max_input_tokens * 0.9):
            token_limit_risk = True

        messages.append(response)

        # No tool calls, the model produced its final answer
        if not response.tool_calls:
            final_response = _stringify_content(response.content)
            break

        # Execute every tool the model requested
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name", "")
            if tool_name:
                tools_called.append(tool_name)

            tool_fn = TOOLS.get(tool_name)
            if tool_fn is None:
                messages.append(ToolMessage(
                    tool_call_id=tool_call.get("id", "unknown"),
                    content=json.dumps({"error": f"Unknown tool: {tool_name}"}),
                ))
                continue

            tool_result = None
            with suppress(Exception):
                tool_result = tool_fn.invoke(tool_call)

            if tool_result is None:
                tool_result = ToolMessage(
                    tool_call_id=tool_call.get("id", "unknown"),
                    content=json.dumps({"error": "Tool execution failed."}),
                )

            messages.append(tool_result)

            if tool_name == detect_objects.name:
                parsed = _parse_tool_json(tool_result.content)
                if parsed:
                    parsed_prediction_id = parsed.get("prediction_uid")
                    if isinstance(parsed_prediction_id, str) and parsed_prediction_id:
                        prediction_id = parsed_prediction_id
                        annotated_image = _fetch_annotated_image(parsed_prediction_id)

            if tool_name in IMAGE_OP_TOOL_NAMES:
                parsed = _parse_tool_json(tool_result.content)
                if parsed:
                    operation_id = parsed.get("operation_id")
                    if isinstance(operation_id, str) and operation_id:
                        # Overwrite, don't accumulate - each edit builds on the last.
                        processed_image = _processed_images.pop(operation_id, processed_image)
    else:
        context_limit_exceeded = True
        final_response = "Agent stopped because it reached the maximum number of tool iterations."

    if token_limit_risk:
        logger.warning("Model input token usage was near or above max_input_tokens for at least one loop iteration.")

    return {
        "response": final_response,
        "prediction_id": prediction_id,
        "annotated_image": annotated_image,
        "processed_image": processed_image,
        "agent_loop_time_s": time.perf_counter() - start_time,
        "iterations": iterations,
        "tools_called": tools_called,
        "context_limit_exceeded": context_limit_exceeded,
        "tokens_used": {
            "input": total_input_tokens,
            "output": total_output_tokens,
            "total": total_tokens,
        },
    }


app = FastAPI(title="Vision Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://34.224.235.157:3000", "http://3.214.66.146:3000",
        "http://rina-dev.fursa.click:3000",
    ],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


class ChatMessage(BaseModel):
    role: str                           # "user" or "assistant"
    content: str
    image_base64: Optional[str] = None  # only on user messages that carry an image


class ChatRequest(BaseModel):
    messages: list[ChatMessage]         # full conversation thread, oldest first
    chat_id: str | None = None


class TokenUsage(BaseModel):
    input: int | None = None
    output: int | None = None
    total: int | None = None


class ChatResponse(BaseModel):
    response: str
    prediction_id: str | None = None
    annotated_image: str | None = None
    processed_image: str | None = None
    agent_loop_time_s: float
    iterations: int
    tools_called: list[str]
    context_limit_exceeded: bool
    tokens_used: TokenUsage


# Small models can "pattern-lock" onto their own prior reply phrasing instead of calling a tool
# for a new request, so only replay recent turns (the image is still recovered from full history).
MAX_HISTORY_MESSAGES = 4

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
# Ordinal word/digit immediately followed by the object's label, e.g. "second dog".
_ORDINAL_LABEL_PATTERN = re.compile(
    r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\s+"
    r"(?:detected\s+)?([a-zA-Z]+)",
    re.IGNORECASE,
)
# "the <label>", e.g. "the person on the right"; skips "last"/"detected" so it still captures "person".
_LABEL_ONLY_PATTERN = re.compile(r"\bthe\s+(?:last\s+)?(?:detected\s+)?([a-zA-Z]+)\b", re.IGNORECASE)
_DIRECTION_PATTERN = re.compile(r"\b(?:from|on|to|in)\s+the\s+(left|right)\b", re.IGNORECASE)
# "last" names the extreme of a side rather than a fixed number, so it can't live in _ORDINAL_WORDS.
_LAST_WORD_PATTERN = re.compile(r"\blast\b", re.IGNORECASE)
# One keyword per object-scoped operation, to split a multi-edit message into per-operation clauses.
_OPERATION_KEYWORDS = {
    "add_noise": ("noise", "salt", "pepper"),
    "blur": ("blur",),
    "rotate": ("rotate", "turn"),
    "flip": ("flip", "mirror"),
}
_NON_OBJECT_LABELS = {"image", "picture", "photo", "whole", "entire", "detected"}


def _singularize(word: str) -> str:
    word = word.lower().rstrip(".,!?")
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _parse_object_reference_in_clause(clause: str) -> Optional[dict]:
    """Parse one edit clause (e.g. 'blur the second dog from the right') into a
    {label, rank_from_left, rank_from_right} reference in plain Python - the model has been
    observed to mistranslate ordinals and mix up objects across edits, so this overrides its args."""
    direction_match = _DIRECTION_PATTERN.search(clause)
    direction = direction_match.group(1).lower() if direction_match else None

    if _LAST_WORD_PATTERN.search(clause):
        label_match = _LABEL_ONLY_PATTERN.search(clause)
        if label_match:
            label = _singularize(label_match.group(1))
            if label not in _NON_OBJECT_LABELS:
                # "last" names the extreme of the stated side, doesn't reverse it; defaults to rightmost.
                return {
                    "label": label,
                    "rank_from_left": 1 if direction == "left" else None,
                    "rank_from_right": 1 if direction != "left" else None,
                }

    ordinal_match = _ORDINAL_LABEL_PATTERN.search(clause)
    if ordinal_match:
        ordinal_text, label = ordinal_match.groups()
        rank = _ORDINAL_WORDS.get(ordinal_text.lower())
        if rank is None:
            digits = re.match(r"\d+", ordinal_text)
            rank = int(digits.group()) if digits else None
        if rank is not None:
            label = _singularize(label)
            if label not in _NON_OBJECT_LABELS:
                direction = direction or "left"
                return {
                    "label": label,
                    "rank_from_left": rank if direction == "left" else None,
                    "rank_from_right": rank if direction == "right" else None,
                }

    if direction is not None:
        label_match = _LABEL_ONLY_PATTERN.search(clause)
        if label_match:
            label = _singularize(label_match.group(1))
            if label not in _NON_OBJECT_LABELS:
                return {
                    "label": label,
                    "rank_from_left": 1 if direction == "left" else None,
                    "rank_from_right": 1 if direction == "right" else None,
                }

    return None


def _parse_object_reference_hints(text: str) -> dict[str, dict]:
    """Split on 'and' and parse each clause's object reference, keyed by its operation - so a multi-edit
    message resolves each edit independently instead of one reference bleeding into all of them."""
    hints: dict[str, dict] = {}
    for clause in re.split(r"\band\b", text, flags=re.IGNORECASE):
        clause_lower = clause.lower()
        operation = next(
            (op for op, keywords in _OPERATION_KEYWORDS.items() if any(k in clause_lower for k in keywords)),
            None,
        )
        if operation is None:
            continue
        reference = _parse_object_reference_in_clause(clause)
        if reference is not None:
            hints[operation] = reference
    return hints


_WHOLE_IMAGE_TOOL_BY_OPERATION = {
    "blur": blur_image,
    "flip": flip_image,
    "add_noise": add_noise_image,
}
_OBJECT_TOOL_BY_OPERATION = {
    "blur": blur_object,
    "flip": flip_object,
    "add_noise": add_noise_object,
}


def _extract_clause_number(clause: str) -> Optional[float]:
    """First decimal/integer literal in a clause, e.g. "noise 0.6" -> 0.6, or None (tool default applies)."""
    match = re.search(r"\b\d+(?:\.\d+)?\b", clause)
    return float(match.group()) if match else None


def _run_deterministic_fallback(chat_id: str, text: str) -> dict:
    """Parse flip/blur/add_noise clauses out of `text` and call the matching tool functions directly -
    no LLM call at all. Used only when Bedrock refuses a response. rotate/resize/crop are not covered;
    the regex parser only recognizes blur/flip/add_noise keywords."""
    chat_token = _current_chat_id.set(chat_id)
    try:
        tools_called: list[str] = []
        processed_image: Optional[str] = None
        performed: list[str] = []
        errors: list[str] = []
        needs_detection = False

        parsed_clauses: list[tuple[str, Optional[dict], Optional[float]]] = []
        for clause in re.split(r"\band\b", text, flags=re.IGNORECASE):
            clause_lower = clause.lower()
            operation = next(
                (op for op, keywords in _OPERATION_KEYWORDS.items() if any(k in clause_lower for k in keywords)),
                None,
            )
            if operation is None:
                continue
            reference = _parse_object_reference_in_clause(clause)
            number = _extract_clause_number(clause)
            parsed_clauses.append((operation, reference, number))
            if reference is not None:
                needs_detection = True

        if not parsed_clauses:
            return {
                "response": "The provider blocked this request and no supported flip/blur/noise "
                "instruction could be parsed from it automatically. Please try again or rephrase.",
                "tools_called": [],
                "processed_image": None,
                "prediction_id": None,
                "annotated_image": None,
            }

        prediction_id: Optional[str] = None
        annotated_image: Optional[str] = None
        if needs_detection:
            tools_called.append(detect_objects.name)
            parsed = _parse_tool_json(detect_objects.invoke({}))
            if parsed:
                candidate_id = parsed.get("prediction_uid")
                if isinstance(candidate_id, str) and candidate_id:
                    prediction_id = candidate_id
                    annotated_image = _fetch_annotated_image(candidate_id)

        for operation, reference, number in parsed_clauses:
            if reference is not None:
                tool_fn = _OBJECT_TOOL_BY_OPERATION[operation]
                kwargs = {
                    "label": reference["label"],
                    "rank_from_left": reference["rank_from_left"],
                    "rank_from_right": reference["rank_from_right"],
                }
            else:
                tool_fn = _WHOLE_IMAGE_TOOL_BY_OPERATION[operation]
                kwargs = {}

            if operation == "blur" and number is not None:
                kwargs["radius"] = number
            elif operation == "add_noise" and number is not None:
                kwargs["amount"] = number

            tools_called.append(tool_fn.name)
            parsed = _parse_tool_json(tool_fn.invoke(kwargs))
            if parsed and "error" not in parsed:
                operation_id = parsed.get("operation_id")
                if isinstance(operation_id, str) and operation_id:
                    processed_image = _processed_images.pop(operation_id, processed_image)
                performed.append(operation)
            elif parsed:
                errors.append(f"{operation}: {parsed['error']}")

        if performed and not errors:
            response_text = (
                "The AI provider blocked its own response for this request, so it was completed "
                f"deterministically instead: {', '.join(performed)}."
            )
        elif performed:
            response_text = (
                f"The AI provider blocked its own response. Completed: {', '.join(performed)}. "
                f"Failed: {'; '.join(errors)}."
            )
        else:
            response_text = (
                f"The AI provider blocked its own response, and the fallback could not complete "
                f"any operation: {'; '.join(errors)}."
            )

        return {
            "response": response_text,
            "tools_called": tools_called,
            "processed_image": processed_image,
            "prediction_id": prediction_id,
            "annotated_image": annotated_image,
        }
    finally:
        _current_chat_id.reset(chat_token)


def _reorder_clauses_to_avoid_content_filter(text: str) -> str:
    """Move add_noise clause(s) to the end - empirically, noise-before-other-edits reliably triggers
    Bedrock's content filter, moving it to the end doesn't. Only changes what we send the model."""
    clauses = re.split(r"\band\b", text, flags=re.IGNORECASE)
    if len(clauses) < 2:
        return text

    is_noise_clause = lambda clause: any(k in clause.lower() for k in _OPERATION_KEYWORDS["add_noise"])
    noise_clauses = [c for c in clauses if is_noise_clause(c)]
    other_clauses = [c for c in clauses if not is_noise_clause(c)]
    if not noise_clauses or not other_clauses:
        return text  # nothing to reorder

    return " and ".join(clause.strip() for clause in other_clauses + noise_clauses)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    """Detects a fresh upload, resets per-chat state for it, parses object reference hints, then
    drives run_agent() - falling back to a deterministic parser if Bedrock blocks the request."""
    normalized_chat_id = (request.chat_id or "chat").strip() or "chat"
    newest_message = request.messages[-1] if request.messages else None
    is_new_upload = newest_message is not None and newest_message.role == "user" and bool(newest_message.image_base64)
    if is_new_upload:
        newest_message.image_base64 = _normalize_image_orientation(newest_message.image_base64)

    latest_image = None
    for msg in request.messages:
        if msg.role == "user" and msg.image_base64:
            latest_image = msg.image_base64          # saved for detect_objects tool

    if is_new_upload:
        # Start a fresh edit chain: OVERWRITE current.png rather than DELETE it first - the S3 role only
        # grants PutObject, and a delete-then-rely-on-absence approach left the old image in place forever.
        _current_working_image[normalized_chat_id] = newest_message.image_base64
        _persist_current_image_to_s3(normalized_chat_id, newest_message.image_base64)
        # Drop detection state from any previous image, so old boxes can't leak into a new-image edit.
        _detections_by_chat.pop(normalized_chat_id, None)
        _detection_image_size_by_chat.pop(normalized_chat_id, None)

    parsed_hints: dict[str, dict] = {}
    if newest_message is not None and newest_message.role == "user":
        parsed_hints = _parse_object_reference_hints(newest_message.content)
    if parsed_hints:
        _object_reference_hints_by_chat[normalized_chat_id] = parsed_hints
    else:
        _object_reference_hints_by_chat.pop(normalized_chat_id, None)  # don't leak a stale hint into this turn

    recent_messages = request.messages[-MAX_HISTORY_MESSAGES:]
    while recent_messages and recent_messages[0].role != "user":
        recent_messages = recent_messages[1:]  # Bedrock requires the first message to be from the user

    lc_messages = []
    for msg in recent_messages:
        if msg.role == "user":
            upload_note = "An image was uploaded. " if msg.image_base64 else ""
            reminder = (
                f"\n[{upload_note}Use the available tools to fulfill this request - do not claim to "
                "have performed an action without actually calling the matching tool.]"
            )
            message_text = msg.content
            if msg is newest_message:
                message_text = _reorder_clauses_to_avoid_content_filter(message_text)
            content = message_text + reminder
            if msg is newest_message and parsed_hints:
                hint_text = ", ".join(
                    f"{operation}: label={hint['label']!r}, rank_from_left={hint['rank_from_left']!r}, "
                    f"rank_from_right={hint['rank_from_right']!r}"
                    for operation, hint in parsed_hints.items()
                )
                content += f"\n[Parsed object references per operation: {hint_text}.]"
            lc_messages.append(HumanMessage(content=content))
        else:
            lc_messages.append(AIMessage(content=msg.content))

    image_token = _current_image_b64.set(latest_image)
    chat_token = _current_chat_id.set(request.chat_id)
    try:
        return ChatResponse(**run_agent(lc_messages))
    except ContentBlockedError as exc:
        logger.error(
            "chat: Bedrock blocked chat_id=%s block_kind=%s tools_called_before_block=%s detail=%s",
            normalized_chat_id, exc.block_kind, exc.tools_called, exc.detail,
        )
        fallback_result: Optional[dict] = None
        # Only text/model-output blocks are eligible - an input-image block means the fallback has no
        # usable image either, and "other"/unclassified errors are kept generic rather than guessed at.
        if exc.block_kind in ("text_prompt", "model_output") and newest_message is not None:
            try:
                fallback_result = _run_deterministic_fallback(normalized_chat_id, newest_message.content)
            except Exception:
                logger.exception("chat: deterministic fallback itself failed for chat_id=%s", normalized_chat_id)

        if fallback_result is not None:
            return ChatResponse(
                response=fallback_result["response"],
                prediction_id=fallback_result["prediction_id"],
                annotated_image=fallback_result["annotated_image"],
                processed_image=fallback_result["processed_image"],
                agent_loop_time_s=0.0,
                iterations=0,
                tools_called=fallback_result["tools_called"],
                context_limit_exceeded=False,
                tokens_used=TokenUsage(),
            )

        return ChatResponse(
            response=_BLOCK_KIND_MESSAGES.get(exc.block_kind, _BLOCK_KIND_MESSAGES["other"]),
            prediction_id=None,
            annotated_image=None,
            processed_image=None,
            agent_loop_time_s=0.0,
            iterations=0,
            tools_called=exc.tools_called,
            context_limit_exceeded=False,
            tokens_used=TokenUsage(),
        )
    finally:
        _current_chat_id.reset(chat_token)
        _current_image_b64.reset(image_token)


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
