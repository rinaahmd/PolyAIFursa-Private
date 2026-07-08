import asyncio
import base64
import binascii
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
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logging.getLogger("langchain").setLevel(logging.DEBUG)
logging.getLogger("langchain_core").setLevel(logging.DEBUG)
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


# The result of the most recent edit for each chat, so edits build on each other
# (blur, then rotate, rotates the blurred version) instead of every tool always
# starting over from the original upload. Keyed by chat_id, not a ContextVar, for
# the same reason as _processed_images below: tool calls run in a copied context,
# so a write from inside one tool call would never be visible to the next one in
# the same turn. Reset (popped) whenever chat() sees a genuinely new upload.
_current_working_image: dict[str, str] = {}

# Set by chat() from _parse_object_reference_hints on the current user message,
# keyed by chat_id -> operation name ("blur"/"rotate"/"flip"/"add_noise").
# blur_object/rotate_object/flip_object/add_noise_object use the entry for
# their own operation to OVERRIDE whatever label/rank_from_left/rank_from_right
# the model passed - even after being told to just copy these values, the
# model has been observed to still substitute the wrong object/number most of
# the time (including mixing up which object goes with which edit, when a
# single message requests several edits at once), so the parsed reference is
# treated as ground truth rather than a suggestion.
_object_reference_hints_by_chat: dict[str, dict[str, dict]] = {}


def _get_current_image() -> Optional[str]:
    return _current_working_image.get(_normalized_chat_id()) or _current_image_b64.get()


# Populated by detect_objects, read by blur_object/rotate_object/flip_object/add_noise_object
# to resolve "the second dog" to actual pixel coordinates without asking the LLM to copy box
# coordinates by hand. Keyed by chat_id for the same reason as _current_working_image above.
_detections_by_chat: dict[str, list[dict]] = {}


def _upload_bytes_to_s3(data: bytes, s3_key: str, content_type: str = "image/jpeg") -> None:
    if not AWS_REGION:
        raise RuntimeError("AWS_REGION environment variable is required")
    if not AWS_S3_BUCKET:
        raise RuntimeError("AWS_S3_BUCKET environment variable is required")

    s3_client = boto3.client("s3", region_name=AWS_REGION)
    s3_client.put_object(Bucket=AWS_S3_BUCKET, Key=s3_key, Body=data, ContentType=content_type)

def _fetch_detections(prediction_id: str) -> list[dict]:
    """Fetch per-object bounding boxes for a prediction. /predict itself only
    returns labels, not boxes - the box coordinates live behind a separate
    GET /prediction/{uid} call, same as _fetch_annotated_image below."""
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
    """Add rank_from_left/rank_from_right (1-based) to each detection, ranked
    among objects sharing the same label, ordered by horizontal center. Doing
    this comparison in code - rather than asking the LLM to compare several
    raw box coordinates by itself - is what makes "the second dog from the
    right" resolve reliably instead of depending on the model's arithmetic."""
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

    The result includes a "detections" list, each with an "index", "label",
    "score", "box" ([left, top, right, bottom] in pixels), "rank_from_left",
    and "rank_from_right" (1-based, computed among objects sharing the same
    label). You do not need to read or compare these rank values yourself -
    to edit a specific object (e.g. "the second dog from the right"), call
    blur_object/rotate_object/flip_object/add_noise_object directly with
    label="dog" and rank_from_right=2, copied straight from the user's
    wording. Do not try to resolve this to an index yourself.
    """
    image_b64 = _get_current_image()
    if not image_b64:
        return json.dumps({"error": "No image was provided by the user."})

    try:
        image_bytes = base64.b64decode(image_b64)
    except (binascii.Error, ValueError) as exc:
        logger.exception("detect_objects: invalid base64 image encoding")
        return json.dumps({"error": f"Invalid image encoding: {exc}"})

    prediction_id = str(uuid.uuid4())
    chat_id = _normalized_chat_id()
    filename = "image.jpg"
    image_s3_key = f"{chat_id}/{prediction_id}/original/{filename}"

    try:
        _upload_bytes_to_s3(image_bytes, image_s3_key)
    except RuntimeError as exc:
        logger.exception("detect_objects: S3 configuration error")
        return json.dumps({"error": f"S3 configuration error: {exc}"})
    except (BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError) as exc:
        logger.exception("detect_objects: failed to upload image to S3")
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
        logger.exception("detect_objects: YOLO service returned non-2xx status")
        detail = exc.response.text if exc.response is not None else str(exc)
        return json.dumps({"error": f"YOLO service returned an error: {detail}"})
    except httpx.HTTPError as exc:
        logger.exception("detect_objects: failed HTTP call to YOLO service")
        return json.dumps({"error": f"Failed to call YOLO service: {exc}"})

    detections = _fetch_detections(prediction_id)
    _detections_by_chat[chat_id] = detections
    result["detections"] = detections
    return json.dumps(result)


def _extract_mcp_text(result: Any) -> str:
    """MCP tool results can come back as a plain string or as a list of
    content blocks (e.g. [{"type": "text", "text": "..."}]) - unwrap either
    shape without touching the raw payload (no LLM-text sanitization here,
    since this may carry base64 image bytes)."""
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


# Tool calls run in a copied context (LangChain's tracing machinery), so a
# ContextVar written inside a tool never propagates back to run_agent's
# context. Stash processed image bytes in a plain dict instead, keyed by a
# small id the tool hands back in its (LLM-visible) JSON output - the same
# side-channel trick detect_objects/prediction_id/annotated_image already use.
_processed_images: dict[str, str] = {}


async def _call_mcp_object_op(operation: str, arguments: dict, image_b64: str, box: list) -> str:
    left, top, right, bottom = (int(round(v)) for v in box)
    region_b64 = await _call_mcp_tool(
        "crop", {"image_b64": image_b64, "left": left, "top": top, "right": right, "bottom": bottom}
    )
    transformed_region_b64 = await _call_mcp_tool(operation, {"image_b64": region_b64, **arguments})
    return await _call_mcp_tool(
        "paste", {"base_image_b64": image_b64, "region_b64": transformed_region_b64, "left": left, "top": top}
    )


def _resolve_object_box(label: str, rank_from_left: int | None, rank_from_right: int | None) -> tuple[list | None, str | None]:
    """Resolve (label, rank) to a box, entirely in code. We deliberately do NOT
    ask the LLM to compare box coordinates or pick a raw index itself - even
    when handed pre-computed rank_from_left/rank_from_right values, this small
    model has been observed to state the correct rank in its own reasoning and
    then still pick the wrong object. Reducing its job to "translate the
    phrase 'second from the right' into rank_from_right=2" - a literal
    wording-to-number copy, not a comparison across a list - is reliable."""
    chat_id = _normalized_chat_id()
    candidates = [d for d in _detections_by_chat.get(chat_id, []) if d["label"] == label]
    if not candidates:
        return None, f"No detected object labeled '{label}'. Call detect_objects first."

    rank = rank_from_left if rank_from_left is not None else (rank_from_right or 1)
    rank_field = "rank_from_left" if rank_from_left is not None else "rank_from_right"
    match = next((d for d in candidates if d[rank_field] == rank), None)
    if match is None:
        return None, f"No '{label}' with {rank_field}={rank}. There are only {len(candidates)}."
    return match["box"], None


def _run_image_op(
    operation: str,
    arguments: dict,
    label: str | None = None,
    rank_from_left: int | None = None,
    rank_from_right: int | None = None,
) -> str:
    chat_id = _normalized_chat_id()
    image_b64 = _get_current_image()
    if not image_b64:
        return json.dumps({"error": "No image was provided by the user."})

    if label is not None:
        # chat() already parsed the user's actual wording into a hint with
        # plain Python - that's ground truth. Override whatever the model
        # passed rather than trust it, since the model has been observed to
        # still get the number wrong (or mix up which object goes with which
        # edit) even when told to just copy the hint.
        hint = _object_reference_hints_by_chat.get(chat_id, {}).get(operation)
        if hint is not None:
            label = hint["label"]
            rank_from_left = hint["rank_from_left"]
            rank_from_right = hint["rank_from_right"]

    box = None
    if label is not None:
        box, error = _resolve_object_box(label, rank_from_left, rank_from_right)
        if error:
            return json.dumps({"error": error})

    try:
        if box is None:
            result_b64 = asyncio.run(_call_mcp_tool(operation, {"image_b64": image_b64, **arguments}))
        else:
            result_b64 = asyncio.run(_call_mcp_object_op(operation, arguments, image_b64, box))
    except Exception as exc:
        logger.exception("%s: failed to call img-proc MCP server", operation)
        return json.dumps({"error": f"Failed to {operation} image: {exc}"})

    _current_working_image[chat_id] = result_b64  # so the next edit builds on this one
    operation_id = str(uuid.uuid4())
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
    if profile is None:
        return {}

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
    for key in keys:
        if key in data:
            value = _coerce_int(data.get(key))
            if value is not None:
                return value
    return None


def validate_model_profile(model_obj: Any, model_name: str) -> dict[str, Any]:
    profile = _profile_to_dict(getattr(model_obj, "profile", None))

    if profile.get("tool_calling") is not True:
        raise RuntimeError(
            f"Model '{model_name}' is incompatible: missing required feature 'tool_calling=True' in llm.profile."
        )

    # Some providers/version pairs do not expose structured_output in profile.
    # We only enforce it when the key exists.
    if "structured_output" in profile and profile.get("structured_output") is not True:
        raise RuntimeError(
            f"Model '{model_name}' is incompatible: missing required feature 'structured_output=True' in llm.profile."
        )

    return profile


def _extract_usage_metadata(response: AIMessage) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        return {"input": None, "output": None, "total": None}

    input_tokens = _pick_first_int(
        usage,
        (
            "input_tokens",
            "inputTokens",
            "input_token_count",
            "inputTokenCount",
        ),
    )
    output_tokens = _pick_first_int(
        usage,
        (
            "output_tokens",
            "outputTokens",
            "output_token_count",
            "outputTokenCount",
        ),
    )
    total_tokens = _pick_first_int(
        usage,
        (
            "total_tokens",
            "totalTokens",
            "total_token_count",
            "totalTokenCount",
        ),
    )
    return {"input": input_tokens, "output": output_tokens, "total": total_tokens}


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
llm_profile: dict[str, Any] = {}
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
            "llm_profile": initialized_profile,
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
            item_text = _content_item_text(item)
            if item_text:
                text_parts.append(item_text)
        return _sanitize_response_text("\n".join(text_parts))
    if content is None:
        return ""
    return _sanitize_response_text(str(content))


def _content_item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        text = item.get("text")
        if isinstance(text, str):
            return text
        return ""
    return ""


def _sanitize_response_text(text: str) -> str:
    cleaned = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # The model sometimes wraps its final answer in a literal <response> tag
    # instead of just returning plain text - keep the text, drop the tags.
    cleaned = re.sub(r"</?response>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"!\[[^\]]*\]\(data:image/[^)]+\)", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _parse_tool_json(content) -> Optional[dict]:
    text = _stringify_content(content)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _fetch_annotated_image(prediction_id: str) -> Optional[str]:
    image_url = f"{YOLO_SERVICE_URL}/prediction/{prediction_id}/image"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(image_url)
            response.raise_for_status()
        return base64.b64encode(response.content).decode("utf-8")
    except (httpx.HTTPError, ValueError, TypeError):
        return None




def _invoke_with_content_filter_retry(llm_with_tools, messages: list, max_retries: int = 4) -> AIMessage:
    """Bedrock's own content-safety layer has been observed to block a
    response to some multi-step edit requests intermittently - the exact
    same messages can succeed on a retry a moment later. Retry a few times
    before giving up, since this is nondeterministic and out of this code's
    control (it isn't something our prompt or tool design can fix)."""
    response = llm_with_tools.invoke(messages)
    attempts = 1
    while (
        not response.tool_calls
        and "blocked by our content filters" in _stringify_content(response.content).lower()
        and attempts <= max_retries
    ):
        logger.warning("LLM response blocked by content filters, retrying (attempt %d/%d)", attempts, max_retries)
        response = llm_with_tools.invoke(messages)
        attempts += 1
    return response


def run_agent(history: list, max_iterations: int = 10) -> dict:
    """
    Simple ReAct loop:
      1. Send messages to the LLM.
      2. If the LLM requests tool calls, execute them and append results.
      3. Repeat until the LLM returns a plain text response.
      4. Stop after max_iterations to avoid infinite loops.
    """
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
        response: AIMessage = _invoke_with_content_filter_retry(active_llm_with_tools, messages)
        usage = _extract_usage_metadata(response)
        total_input_tokens = _sum_optional(total_input_tokens, usage["input"])
        total_output_tokens = _sum_optional(total_output_tokens, usage["output"])
        total_tokens = _sum_optional(total_tokens, usage["total"])

        if llm_max_input_tokens is not None and usage["input"] is not None:
            near_limit_threshold = int(llm_max_input_tokens * 0.9)
            if usage["input"] >= near_limit_threshold:
                token_limit_risk = True

        print("TOOL CALLS:", response.tool_calls)
        print("CONTENT:", response.content)
        messages.append(response)

        # No tool calls, the model produced its final answer
        if not response.tool_calls:
            final_response = _stringify_content(response.content)
            if "blocked by our content filters" in final_response.lower():
                # This is the model provider's own safety layer, triggered
                # intermittently (observed to vary between identical, repeated
                # requests) - not something this code can control. Give the
                # user something actionable instead of the raw filter string.
                final_response = (
                    "This request was blocked by the AI provider's content safety filters. "
                    "This can happen intermittently, especially with multi-step edit requests - "
                    "please try again, or rephrase your request."
                )
            break

        # Execute every tool the model requested
        for tool_call in response.tool_calls:
            tool_name = tool_call.get("name", "")
            if tool_name:
                tools_called.append(tool_name)

            tool_fn = TOOLS.get(tool_name)
            if tool_fn is None:
                tool_result = ToolMessage(
                    tool_call_id=tool_call.get("id", "unknown"),
                    content=json.dumps({"error": f"Unknown tool: {tool_name}"}),
                )
                messages.append(tool_result)
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
                        # Overwrite, don't accumulate - each edit builds on the last, so
                        # the final one already reflects every edit made this turn.
                        processed_image = _processed_images.pop(operation_id, processed_image)
    else:
        context_limit_exceeded = True
        final_response = "Agent stopped because it reached the maximum number of tool iterations."

    if token_limit_risk:
        logging.warning(
            "Model input token usage was near or above max_input_tokens for at least one loop iteration."
        )

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
        "http://34.224.235.157:3000","http://3.214.66.146:3000",
        "http://rina-dev.fursa.click:3000",
    ],    allow_methods=["POST", "GET"],
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


# Small tool-calling models can "pattern-lock" onto the phrasing of their own
# prior replies (e.g. several "The image has been successfully X'd." in a row)
# and start completing that template instead of calling a tool for a new
# request. Only replay the most recent turns to the LLM to keep it grounded -
# the original image is still recovered below by scanning the FULL history.
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
# "the <label>", used when there's no ordinal, just a side (e.g. "the person on the right").
_LABEL_ONLY_PATTERN = re.compile(r"\bthe\s+(?:detected\s+)?([a-zA-Z]+)\b", re.IGNORECASE)
# Accepts "from/on/to the left/right" - covers "second dog from the right",
# "the person on the right", "the person to the left", etc.
_DIRECTION_PATTERN = re.compile(r"\b(?:from|on|to)\s+the\s+(left|right)\b", re.IGNORECASE)
# One keyword per object-scoped operation, used to split a multi-edit message
# into per-operation clauses (e.g. "...add noise to X and blur Y" has one
# clause for add_noise, one for blur).
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
    """Best-effort parse of a single edit clause (e.g. 'blur the second dog
    from the right', or 'the person on the right') into a
    {label, rank_from_left, rank_from_right} reference.

    This exists because the small tool-calling model has been observed to
    mistranslate even a plain ordinal word like "second" into the wrong
    integer most of the time, and to mix up which object goes with which
    edit when a message requests several edits at once. Parsing each clause
    in plain Python and overriding the model's own arguments with the result
    is far more reliable than asking it to work this out itself."""
    direction_match = _DIRECTION_PATTERN.search(clause)
    direction = direction_match.group(1).lower() if direction_match else None

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
    """Split a message into clauses (on 'and') and parse each clause's object
    reference, keyed by the operation (blur/rotate/flip/add_noise) mentioned
    in that clause - so a single message requesting several different edits
    on different objects resolves each one independently instead of a single
    reference bleeding into every edit in the message."""
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


def _reorder_clauses_to_avoid_content_filter(text: str) -> str:
    """Observed empirically: a message with several edit clauses ordered as
    "add noise to X and flip Y and blur Z" reliably triggers Bedrock's own
    content-safety filter (blocking the reply with zero tool calls), while
    the identical edits succeed every time when the noise clause is moved to
    the end - "flip Y and blur Z and add noise to X". This only changes what
    we send the model, not the message shown in the chat transcript, and
    each clause is still resolved to its own object independently regardless
    of order (see _parse_object_reference_hints)."""
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
    latest_image = None
    for msg in request.messages:
        if msg.role == "user" and msg.image_base64:
            latest_image = msg.image_base64          # saved for detect_objects tool

    normalized_chat_id = (request.chat_id or "chat").strip() or "chat"
    newest_message = request.messages[-1] if request.messages else None
    if newest_message is not None and newest_message.role == "user" and newest_message.image_base64:
        # A brand new image was just uploaded this turn - start a fresh edit chain
        # instead of continuing to build on whatever was edited before it.
        _current_working_image.pop(normalized_chat_id, None)

    parsed_hints: dict[str, dict] = {}
    if newest_message is not None and newest_message.role == "user":
        parsed_hints = _parse_object_reference_hints(newest_message.content)
    if parsed_hints:
        _object_reference_hints_by_chat[normalized_chat_id] = parsed_hints
    else:
        # Don't let hints from an earlier, unrelated turn leak into this one.
        _object_reference_hints_by_chat.pop(normalized_chat_id, None)

    recent_messages = request.messages[-MAX_HISTORY_MESSAGES:]
    while recent_messages and recent_messages[0].role != "user":
        recent_messages = recent_messages[1:]  # Bedrock requires the first message to be from the user

    lc_messages = []
    for msg in recent_messages:
        if msg.role == "user":
            if msg.image_base64:
                reminder = "\n[An image was uploaded. Use the available tools to fulfill this request - do not claim to have performed an action without actually calling the matching tool.]"
            else:
                reminder = "\n[Use the available tools to fulfill this request - do not claim to have performed an action without actually calling the matching tool.]"
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
    finally:
        _current_chat_id.reset(chat_token)
        _current_image_b64.reset(image_token)


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
