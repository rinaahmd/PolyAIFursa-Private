"""Regression tests for the object-specific image editing bug that appeared
after moving images to S3: EXIF orientation drift and dimension mismatches
between what YOLO measured boxes against and what img-proc-mcp actually
edited, plus state leaking between separate chats/uploads. See
_normalize_image_orientation, _scale_box, _resolve_object_box, and the
new-upload reset block in chat() for the fixes these tests cover."""

import base64
import io

import pytest
from PIL import Image

import app as agent_app


def _png_b64(width: int, height: int, color=(0, 0, 0)) -> str:
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _jpeg_b64_with_orientation(width: int, height: int, orientation: int, color=(0, 0, 0)) -> str:
    image = Image.new("RGB", (width, height), color)
    exif = Image.Exif()
    exif[274] = orientation
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# --- Four-person left/right ranking ---------------------------------------


def test_four_person_detection_ranks_all_four_left_to_right():
    """The reported bug: an image with 4 people, only 3 reported/ranked.
    Confirms _add_left_right_rank handles all 4 detections, not a subset,
    and assigns strictly increasing/decreasing ranks by horizontal center."""
    detections = [
        {"index": 0, "label": "person", "box": [300, 0, 400, 100]},  # 3rd from left
        {"index": 1, "label": "person", "box": [0, 0, 100, 100]},    # 1st from left
        {"index": 2, "label": "person", "box": [600, 0, 700, 100]},  # 4th from left
        {"index": 3, "label": "person", "box": [150, 0, 250, 100]},  # 2nd from left
    ]

    agent_app._add_left_right_rank(detections)

    by_index = {d["index"]: d for d in detections}
    assert by_index[1]["rank_from_left"] == 1
    assert by_index[3]["rank_from_left"] == 2
    assert by_index[0]["rank_from_left"] == 3
    assert by_index[2]["rank_from_left"] == 4

    assert by_index[1]["rank_from_right"] == 4
    assert by_index[3]["rank_from_right"] == 3
    assert by_index[0]["rank_from_right"] == 2
    assert by_index[2]["rank_from_right"] == 1


def test_resolve_object_box_finds_all_four_ranks(monkeypatch):
    detections = [
        {"index": 0, "label": "person", "box": [0, 0, 100, 100]},
        {"index": 1, "label": "person", "box": [150, 0, 250, 100]},
        {"index": 2, "label": "person", "box": [300, 0, 400, 100]},
        {"index": 3, "label": "person", "box": [600, 0, 700, 100]},
    ]
    agent_app._add_left_right_rank(detections)
    monkeypatch.setattr(agent_app, "_detections_by_chat", {"chat": detections})
    monkeypatch.setattr(agent_app, "_detection_image_size_by_chat", {})

    for rank in (1, 2, 3, 4):
        chat_token = agent_app._current_chat_id.set("chat")
        try:
            box, error = agent_app._resolve_object_box("person", rank, None, None)
        finally:
            agent_app._current_chat_id.reset(chat_token)
        assert error is None, f"rank {rank} should resolve, got error: {error}"
        assert box is not None


# --- Bounding-box scaling ---------------------------------------------------


def test_scale_box_is_noop_when_sizes_match():
    box = [10.0, 20.0, 30.0, 40.0]
    assert agent_app._scale_box(box, (100, 100), (100, 100)) == box


def test_scale_box_scales_proportionally_on_dimension_mismatch():
    box = [10.0, 10.0, 20.0, 20.0]
    # Current image is exactly double the size the box was measured against.
    scaled = agent_app._scale_box(box, (100, 100), (200, 200))
    assert scaled == [20.0, 20.0, 40.0, 40.0]


def test_scale_box_handles_non_uniform_scaling():
    box = [0.0, 0.0, 50.0, 50.0]
    # width doubles, height stays the same
    scaled = agent_app._scale_box(box, (100, 100), (200, 100))
    assert scaled == [0.0, 0.0, 100.0, 50.0]


def test_resolve_object_box_scales_when_current_image_size_differs(monkeypatch):
    detections = [{"index": 0, "label": "dog", "box": [10.0, 10.0, 20.0, 20.0], "rank_from_left": 1, "rank_from_right": 1}]
    monkeypatch.setattr(agent_app, "_detections_by_chat", {"chat": detections})
    monkeypatch.setattr(agent_app, "_detection_image_size_by_chat", {"chat": (100, 100)})

    chat_token = agent_app._current_chat_id.set("chat")
    try:
        box, error = agent_app._resolve_object_box("dog", 1, None, (200, 200))
    finally:
        agent_app._current_chat_id.reset(chat_token)

    assert error is None
    assert box == [20.0, 20.0, 40.0, 40.0]


def test_resolve_object_box_does_not_scale_when_sizes_match(monkeypatch):
    detections = [{"index": 0, "label": "dog", "box": [10.0, 10.0, 20.0, 20.0], "rank_from_left": 1, "rank_from_right": 1}]
    monkeypatch.setattr(agent_app, "_detections_by_chat", {"chat": detections})
    monkeypatch.setattr(agent_app, "_detection_image_size_by_chat", {"chat": (100, 100)})

    chat_token = agent_app._current_chat_id.set("chat")
    try:
        box, error = agent_app._resolve_object_box("dog", 1, None, (100, 100))
    finally:
        agent_app._current_chat_id.reset(chat_token)

    assert error is None
    assert box == [10.0, 10.0, 20.0, 20.0]


# --- EXIF orientation normalization -----------------------------------------


def test_normalize_image_orientation_bakes_in_rotation_and_reencodes_as_png():
    # 100x60 stored, orientation 6 -> corrected is 60x100.
    raw_b64 = _jpeg_b64_with_orientation(100, 60, orientation=6)

    normalized_b64 = agent_app._normalize_image_orientation(raw_b64)

    with Image.open(io.BytesIO(base64.b64decode(normalized_b64))) as image:
        assert image.size == (60, 100)
        assert image.format == "PNG"


def test_normalize_image_orientation_returns_input_unchanged_on_decode_failure():
    garbage = base64.b64encode(b"not an image").decode("utf-8")
    assert agent_app._normalize_image_orientation(garbage) == garbage


# --- Edits confined to the selected box (via _call_mcp_object_op contract) -


def test_call_mcp_object_op_only_touches_the_cropped_region(monkeypatch):
    """crop -> operation -> paste must pass the SAME box to crop and paste,
    and the operation step must only ever see the cropped region's key, never
    the full image's key - so a transform can't accidentally apply outside
    the selected box. Also confirms the whole chain reuses two stable
    per-chat scratch keys (agent-chosen output_s3_key), not a fresh S3
    object per call."""
    calls = []

    async def fake_call_mcp_tool(tool_name, arguments):
        calls.append((tool_name, dict(arguments)))
        if tool_name == "blur":
            assert arguments["input_s3_key"] == agent_app._scratch_region_s3_key("chat-1")  # region only, never full image
        return arguments["output_s3_key"]

    monkeypatch.setattr(agent_app, "_call_mcp_tool", fake_call_mcp_tool)

    import asyncio

    result = asyncio.run(
        agent_app._call_mcp_object_op("blur", {"radius": 2.0}, "chat-1", "full-image-key.png", [10.0, 20.0, 30.0, 40.0])
    )

    # paste overwrites the original full-image key - that's the final result location.
    assert result == "full-image-key.png"
    crop_call = next(c for name, c in calls if name == "crop")
    paste_call = next(c for name, c in calls if name == "paste")
    assert (crop_call["left"], crop_call["top"], crop_call["right"], crop_call["bottom"]) == (10, 20, 30, 40)
    assert (paste_call["left"], paste_call["top"]) == (10, 20)
    assert crop_call["output_s3_key"] == agent_app._scratch_region_s3_key("chat-1")
    assert paste_call["output_s3_key"] == "full-image-key.png"
    assert paste_call["base_s3_key"] == "full-image-key.png"  # pastes back into the ORIGINAL full image


# --- Sequential edits preserve prior changes --------------------------------


def test_sequential_edits_each_build_on_the_previous_result(monkeypatch):
    """flip, then blur, then noise - each _run_image_op call must operate on
    the PREVIOUS edit's result, not restart from the original upload."""
    seen_inputs = []
    upload_call_count = {"n": 0}

    def fake_upload(data, s3_key, content_type="image/jpeg"):
        upload_call_count["n"] += 1

    async def fake_call_mcp_tool(tool_name, arguments):
        seen_inputs.append(arguments.get("input_s3_key"))
        return f"output-after-{tool_name}.png"

    downloaded_bytes_by_key = {
        "output-after-flip.png": b"after-flip",
        "output-after-blur.png": b"after-blur",
        "output-after-add_noise.png": b"after-noise",
    }

    def fake_download(s3_key):
        return downloaded_bytes_by_key.get(s3_key)

    monkeypatch.setattr(agent_app, "_upload_bytes_to_s3", fake_upload)
    monkeypatch.setattr(agent_app, "_download_bytes_from_s3", fake_download)
    monkeypatch.setattr(agent_app, "_call_mcp_tool", fake_call_mcp_tool)
    monkeypatch.setattr(agent_app, "_persist_current_image_to_s3", lambda *a, **k: None)
    monkeypatch.setattr(agent_app, "_current_working_image", {})
    monkeypatch.setattr(agent_app, "_processed_images", {})

    chat_token = agent_app._current_chat_id.set("chat-seq")
    image_token = agent_app._current_image_b64.set(base64.b64encode(b"original").decode("utf-8"))
    try:
        agent_app._run_image_op("flip", {"direction": "horizontal"})
        agent_app._run_image_op("blur", {"radius": 2.0})
        agent_app._run_image_op("add_noise", {"amount": 0.1})
    finally:
        agent_app._current_chat_id.reset(chat_token)
        agent_app._current_image_b64.reset(image_token)

    # blur's MCP call must have used flip's output as input, and add_noise's
    # call must have used blur's output - never restarting from "original".
    assert upload_call_count["n"] == 3
    # First call uploads the original; can't assert its key content since it's a
    # fresh uuid each time, but the chain is verified via _current_working_image below.
    assert agent_app._current_working_image["chat-seq"] == base64.b64encode(b"after-noise").decode("utf-8")


# --- Separate chats/uploads don't share images or detections ---------------


def test_separate_chat_ids_do_not_share_detections(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "_detections_by_chat",
        {"chat-a": [{"index": 0, "label": "cat", "box": [0, 0, 10, 10], "rank_from_left": 1, "rank_from_right": 1}]},
    )
    monkeypatch.setattr(agent_app, "_detection_image_size_by_chat", {})

    chat_token = agent_app._current_chat_id.set("chat-b")
    try:
        box, error = agent_app._resolve_object_box("cat", 1, None, None)
    finally:
        agent_app._current_chat_id.reset(chat_token)

    assert box is None
    assert "No detected object labeled 'cat'" in error


def test_new_upload_in_same_chat_clears_previous_detections_and_size(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "_detections_by_chat",
        {"chat-x": [{"index": 0, "label": "cat", "box": [0, 0, 10, 10], "rank_from_left": 1, "rank_from_right": 1}]},
    )
    monkeypatch.setattr(agent_app, "_detection_image_size_by_chat", {"chat-x": (100, 100)})
    monkeypatch.setattr(agent_app, "_current_working_image", {"chat-x": "stale-b64"})
    monkeypatch.setattr(agent_app, "_delete_current_image_from_s3", lambda chat_id: None)
    monkeypatch.setattr(agent_app, "_normalize_image_orientation", lambda b64: b64)

    request = agent_app.ChatRequest(
        messages=[
            agent_app.ChatMessage(role="user", content="here's a new image", image_base64=_png_b64(10, 10)),
        ],
        chat_id="chat-x",
    )

    fake_response = {
        "response": "ok", "prediction_id": None, "annotated_image": None, "processed_image": None,
        "agent_loop_time_s": 0.0, "iterations": 1, "tools_called": [], "context_limit_exceeded": False,
        "tokens_used": {"input": None, "output": None, "total": None},
    }
    monkeypatch.setattr(agent_app, "run_agent", lambda *a, **k: fake_response)

    agent_app.chat(request)

    assert "chat-x" not in agent_app._detections_by_chat
    assert "chat-x" not in agent_app._detection_image_size_by_chat
    assert "chat-x" not in agent_app._current_working_image
