import json
import base64
import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import app as agent_app


class FakeLLMWithTools:
    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    def invoke(self, _messages):
        if self._idx >= len(self._responses):
            return AIMessage(content="", tool_calls=[])
        response = self._responses[self._idx]
        self._idx += 1
        return response


def test_run_agent_returns_structured_response_without_tool_calls(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools(
            [
                AIMessage(
                    content="hello",
                    tool_calls=[],
                    usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                )
            ]
        ),
    )

    data = agent_app.run_agent([HumanMessage(content="hello")])

    assert data["response"] == "hello"
    assert data["prediction_id"] is None
    assert data["annotated_image"] is None
    assert isinstance(data["agent_loop_time_s"], float)
    assert data["iterations"] == 1
    assert data["tools_called"] == []
    assert data["context_limit_exceeded"] is False
    assert data["tokens_used"] == {"input": 10, "output": 5, "total": 15}


def test_run_agent_extracts_prediction_id_and_annotated_image(monkeypatch):
    first = AIMessage(
        content="calling tool",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
        tool_calls=[{"id": "tool-1", "name": "detect_objects", "args": {}}],
    )
    second = AIMessage(
        content="done",
        tool_calls=[],
        usage_metadata={"input_tokens": 9, "output_tokens": 4, "total_tokens": 13},
    )
    monkeypatch.setattr(agent_app, "llm_with_tools", FakeLLMWithTools([first, second]))

    class FakeDetectObjectsTool:
        name = "detect_objects"

        def invoke(self, tool_call):
            return ToolMessage(
                tool_call_id=tool_call["id"],
                content=json.dumps(
                    {
                        "prediction_uid": "pred-123",
                        "detection_count": 1,
                        "labels": ["person"],
                        "time_took": 0.42,
                    }
                ),
            )

    monkeypatch.setitem(agent_app.TOOLS, "detect_objects", FakeDetectObjectsTool())
    monkeypatch.setattr(agent_app, "_fetch_annotated_image", lambda prediction_id: "ZmFrZS1pbWFnZQ==")

    data = agent_app.run_agent([HumanMessage(content="check image")])

    assert data["response"] == "done"
    assert data["prediction_id"] == "pred-123"
    assert data["annotated_image"] == "ZmFrZS1pbWFnZQ=="
    assert isinstance(data["agent_loop_time_s"], float)
    assert data["iterations"] == 2
    assert data["tools_called"] == ["detect_objects"]
    assert data["context_limit_exceeded"] is False
    assert data["tokens_used"] == {"input": 16, "output": 7, "total": 23}


def test_run_agent_sets_context_limit_exceeded_on_max_iterations(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"id": "tool-1", "name": "detect_objects", "args": {}}],
                )
            ]
        ),
    )

    class FakeDetectObjectsTool:
        name = "detect_objects"

        def invoke(self, tool_call):
            return ToolMessage(
                tool_call_id=tool_call["id"],
                content=json.dumps({"prediction_uid": "pred-123"}),
            )

    monkeypatch.setitem(agent_app.TOOLS, "detect_objects", FakeDetectObjectsTool())
    monkeypatch.setattr(agent_app, "_fetch_annotated_image", lambda prediction_id: "ZmFrZS1pbWFnZQ==")

    data = agent_app.run_agent([HumanMessage(content="force loop")], max_iterations=1)

    assert data["response"] == "Agent stopped because it reached the maximum number of tool iterations."
    assert data["prediction_id"] == "pred-123"
    assert data["annotated_image"] == "ZmFrZS1pbWFnZQ=="
    assert data["iterations"] == 1
    assert data["tools_called"] == ["detect_objects"]
    assert data["context_limit_exceeded"] is True
    assert data["tokens_used"] == {"input": None, "output": None, "total": None}


def test_run_agent_does_not_crash_on_invalid_tool_json(monkeypatch):
    first = AIMessage(
        content="calling tool",
        tool_calls=[{"id": "tool-1", "name": "detect_objects", "args": {}}],
    )
    second = AIMessage(content="done", tool_calls=[])
    monkeypatch.setattr(agent_app, "llm_with_tools", FakeLLMWithTools([first, second]))

    class FakeDetectObjectsToolInvalidJson:
        name = "detect_objects"

        def invoke(self, tool_call):
            return ToolMessage(tool_call_id=tool_call["id"], content="not-json")

    monkeypatch.setitem(agent_app.TOOLS, "detect_objects", FakeDetectObjectsToolInvalidJson())

    data = agent_app.run_agent([HumanMessage(content="check image")])

    assert data["response"] == "done"
    assert data["prediction_id"] is None
    assert data["annotated_image"] is None
    assert data["tools_called"] == ["detect_objects"]
    assert data["context_limit_exceeded"] is False
    assert data["tokens_used"] == {"input": None, "output": None, "total": None}


def test_run_agent_filters_reasoning_blocks_and_embedded_images(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools(
            [
                AIMessage(
                    content=[
                        {
                            "type": "reasoning_content",
                            "reasoning_content": {
                                "text": "internal chain of thought",
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Here is what is in the image.\n\n"
                                "![annotated](data:image/jpeg;base64,ZmFrZQ==)"
                            ),
                        },
                    ],
                    tool_calls=[],
                )
            ]
        ),
    )

    data = agent_app.run_agent([HumanMessage(content="describe image")])

    assert data["response"] == "Here is what is in the image."
    assert data["prediction_id"] is None
    assert data["annotated_image"] is None


def test_run_agent_filters_plain_text_thinking_tags(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools(
            [
                AIMessage(
                    content=(
                        "<thinking>internal chain of thought</thinking>\n\n"
                        "The image contains the following objects:\n\n"
                        "- 5 persons\n"
                        "- 4 cars"
                    ),
                    tool_calls=[],
                )
            ]
        ),
    )

    data = agent_app.run_agent([HumanMessage(content="describe image")])

    assert data["response"] == "The image contains the following objects:\n\n- 5 persons\n- 4 cars"


def test_extract_usage_metadata_handles_partial_keys():
    class FakeResponse:
        usage_metadata = {"input_tokens": 6}

    usage = getattr(agent_app, "_extract_usage_metadata")(FakeResponse())
    assert usage == {"input": 6, "output": None, "total": None}


def test_model_profile_validation_fails_when_tool_calling_disabled():
    class FakeModel:
        profile = {"tool_calling": False, "structured_output": True}

    with pytest.raises(RuntimeError, match="tool_calling"):
        agent_app.validate_model_profile(FakeModel(), "fake-model")


def test_model_profile_validation_allows_missing_structured_output_key():
    class FakeModel:
        profile = {"tool_calling": True}

    profile = agent_app.validate_model_profile(FakeModel(), "fake-model")
    assert profile["tool_calling"] is True


def test_detect_objects_uploads_to_s3_and_calls_yolo_with_json(monkeypatch):
    captured = {}

    def fake_upload_bytes_to_s3(data: bytes, s3_key: str, content_type: str = "image/jpeg"):
        captured["uploaded_data"] = data
        captured["uploaded_key"] = s3_key
        captured["content_type"] = content_type

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "prediction_uid": "pred-123",
                "detection_count": 1,
                "labels": ["person"],
                "time_took": 0.1,
            }

    class FakeDetectionsResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"detection_objects": []}

    class FakeHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return FakeResponse()

        def get(self, url, **kwargs):
            # detect_objects also fetches per-object boxes via a follow-up
            # GET /prediction/{uid} call - see _fetch_detections in app.py.
            return FakeDetectionsResponse()

    monkeypatch.setattr(agent_app, "_upload_bytes_to_s3", fake_upload_bytes_to_s3)
    monkeypatch.setattr(agent_app.httpx, "Client", FakeHttpxClient)

    image_bytes = b"fake-image-bytes"
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    image_token = agent_app._current_image_b64.set(image_b64)
    chat_token = agent_app._current_chat_id.set("chat-42")
    try:
        raw = agent_app.detect_objects.invoke({})
    finally:
        agent_app._current_chat_id.reset(chat_token)
        agent_app._current_image_b64.reset(image_token)

    payload = json.loads(raw)
    assert payload["prediction_uid"] == "pred-123"

    assert captured["uploaded_data"] == image_bytes
    assert captured["content_type"] == "image/jpeg"
    assert captured["url"] == f"{agent_app.YOLO_SERVICE_URL}/predict"

    post_kwargs = captured["post_kwargs"]
    assert "files" not in post_kwargs
    assert "json" in post_kwargs

    body = post_kwargs["json"]
    assert set(body.keys()) == {"image_s3_key", "prediction_id"}
    prediction_id = body["prediction_id"]
    assert isinstance(prediction_id, str)
    assert re.fullmatch(r"[0-9a-fA-F-]{36}", prediction_id)

    expected_key = f"chat-42/{prediction_id}/original/image.jpg"
    assert body["image_s3_key"] == expected_key
    assert captured["uploaded_key"] == expected_key


def test_blur_image_calls_mcp_and_hides_image_bytes_from_tool_output(monkeypatch):
    async def fake_call_mcp_tool(tool_name, arguments):
        assert tool_name == "blur"
        assert arguments == {"image_b64": "aW1hZ2U=", "radius": 3.0}
        return "Ymx1cnJlZC1pbWFnZQ=="

    monkeypatch.setattr(agent_app, "_call_mcp_tool", fake_call_mcp_tool)
    monkeypatch.setattr(agent_app, "_processed_images", {})

    image_token = agent_app._current_image_b64.set("aW1hZ2U=")
    try:
        raw = agent_app.blur_image.invoke({"radius": 3.0})
    finally:
        agent_app._current_image_b64.reset(image_token)

    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["operation"] == "blur"
    assert payload["radius"] == 3.0
    assert "Ymx1cnJlZC1pbWFnZQ==" not in raw

    operation_id = payload["operation_id"]
    assert agent_app._processed_images[operation_id] == "Ymx1cnJlZC1pbWFnZQ=="


def test_blur_image_returns_error_when_no_image_provided():
    image_token = agent_app._current_image_b64.set(None)
    try:
        raw = agent_app.blur_image.invoke({})
    finally:
        agent_app._current_image_b64.reset(image_token)

    assert json.loads(raw) == {"error": "No image was provided by the user."}


def test_blur_image_returns_error_when_mcp_call_fails(monkeypatch):
    async def failing_call_mcp_tool(tool_name, arguments):
        raise RuntimeError("img-proc-mcp unreachable")

    monkeypatch.setattr(agent_app, "_call_mcp_tool", failing_call_mcp_tool)

    image_token = agent_app._current_image_b64.set("aW1hZ2U=")
    try:
        raw = agent_app.blur_image.invoke({})
    finally:
        agent_app._current_image_b64.reset(image_token)

    payload = json.loads(raw)
    assert "error" in payload
    assert "img-proc-mcp unreachable" in payload["error"]


def test_run_agent_surfaces_processed_image_from_blur_tool(monkeypatch):
    first = AIMessage(
        content="calling blur tool",
        tool_calls=[{"id": "tool-1", "name": "blur_image", "args": {"radius": 2.0}}],
    )
    second = AIMessage(content="done", tool_calls=[])
    monkeypatch.setattr(agent_app, "llm_with_tools", FakeLLMWithTools([first, second]))

    monkeypatch.setattr(agent_app, "_processed_images", {"op-1": "Ymx1cnJlZC1pbWFnZQ=="})

    class FakeBlurTool:
        name = "blur_image"

        def invoke(self, tool_call):
            return ToolMessage(
                tool_call_id=tool_call["id"],
                content=json.dumps({"status": "ok", "operation": "blur", "operation_id": "op-1", "radius": 2.0}),
            )

    monkeypatch.setitem(agent_app.TOOLS, "blur_image", FakeBlurTool())

    data = agent_app.run_agent([HumanMessage(content="blur it")])

    assert data["response"] == "done"
    assert data["processed_image"] == "Ymx1cnJlZC1pbWFnZQ=="
    assert "op-1" not in agent_app._processed_images


def test_run_agent_processed_image_is_none_when_blur_not_called(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools([AIMessage(content="hello", tool_calls=[])]),
    )

    data = agent_app.run_agent([HumanMessage(content="hello")])

    assert data["processed_image"] is None


@pytest.mark.parametrize(
    "clause, expected",
    [
        ("flip the last person in the right", {"label": "person", "rank_from_left": None, "rank_from_right": 1}),
        ("flip the last person on the right", {"label": "person", "rank_from_left": None, "rank_from_right": 1}),
        ("flip the last person from the right", {"label": "person", "rank_from_left": None, "rank_from_right": 1}),
        ("flip the last person", {"label": "person", "rank_from_left": None, "rank_from_right": 1}),
        ("flip the last person from the left", {"label": "person", "rank_from_left": 1, "rank_from_right": None}),
        ("blur the second dog from the right", {"label": "dog", "rank_from_left": None, "rank_from_right": 2}),
        ("flip the person on the left", {"label": "person", "rank_from_left": 1, "rank_from_right": None}),
    ],
)
def test_parse_object_reference_in_clause(clause, expected):
    assert agent_app._parse_object_reference_in_clause(clause) == expected


def test_persist_current_image_to_s3_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(agent_app, "AWS_REGION", None)
    monkeypatch.setattr(agent_app, "AWS_S3_BUCKET", None)

    # Must not raise even though S3 isn't configured - persistence is best-effort.
    agent_app._persist_current_image_to_s3("chat-1", base64.b64encode(b"data").decode("utf-8"))


def test_persist_and_download_current_image_round_trip(monkeypatch):
    store: dict[str, bytes] = {}

    def fake_upload(data, s3_key, content_type="image/jpeg"):
        store[s3_key] = data

    def fake_download(s3_key):
        return store.get(s3_key)

    monkeypatch.setattr(agent_app, "AWS_REGION", "us-east-1")
    monkeypatch.setattr(agent_app, "AWS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(agent_app, "_upload_bytes_to_s3", fake_upload)
    monkeypatch.setattr(agent_app, "_download_bytes_from_s3", fake_download)

    raw_png_bytes = b"working-image-bytes"
    image_b64 = base64.b64encode(raw_png_bytes).decode("utf-8")
    agent_app._persist_current_image_to_s3("chat-1", image_b64)

    assert store[agent_app._current_image_s3_key("chat-1")] == raw_png_bytes
    assert agent_app._download_current_image_from_s3("chat-1") == image_b64
    assert agent_app._download_current_image_from_s3("chat-2") is None


def test_persist_current_image_to_s3_uses_png_content_type(monkeypatch):
    captured = {}

    def fake_upload(data, s3_key, content_type="image/jpeg"):
        captured["content_type"] = content_type
        captured["s3_key"] = s3_key

    monkeypatch.setattr(agent_app, "AWS_REGION", "us-east-1")
    monkeypatch.setattr(agent_app, "AWS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(agent_app, "_upload_bytes_to_s3", fake_upload)

    image_b64 = base64.b64encode(b"png-bytes").decode("utf-8")
    agent_app._persist_current_image_to_s3("chat-1", image_b64)

    assert captured["content_type"] == "image/png"
    assert captured["s3_key"] == "chat-1/current.png"


def test_persist_operation_image_to_s3_uses_operations_key_and_png_content_type(monkeypatch):
    captured = {}

    def fake_upload(data, s3_key, content_type="image/jpeg"):
        captured["content_type"] = content_type
        captured["s3_key"] = s3_key

    monkeypatch.setattr(agent_app, "AWS_REGION", "us-east-1")
    monkeypatch.setattr(agent_app, "AWS_S3_BUCKET", "some-bucket")
    monkeypatch.setattr(agent_app, "_upload_bytes_to_s3", fake_upload)

    image_b64 = base64.b64encode(b"png-bytes").decode("utf-8")
    agent_app._persist_operation_image_to_s3("chat-1", "op-42", "blur", image_b64)

    assert captured["content_type"] == "image/png"
    assert captured["s3_key"] == "chat-1/operations/op-42-blur.png"


def test_get_current_image_falls_back_to_s3_when_memory_is_empty(monkeypatch):
    chat_token = agent_app._current_chat_id.set("chat-restore")
    image_token = agent_app._current_image_b64.set(None)
    try:
        monkeypatch.setattr(agent_app, "_download_current_image_from_s3", lambda chat_id: "cmVzdG9yZWQ=")
        assert agent_app._get_current_image() == "cmVzdG9yZWQ="
        # Restored copy should now be cached in memory too.
        assert agent_app._current_working_image["chat-restore"] == "cmVzdG9yZWQ="
    finally:
        agent_app._current_chat_id.reset(chat_token)
        agent_app._current_image_b64.reset(image_token)


def test_get_current_image_falls_back_to_request_history_when_no_current_image_in_s3(monkeypatch):
    """Covers a brand new upload this turn - no S3 copy exists yet (the first
    edit or detect_objects call is what creates <chat_id>/current.png), so the
    image carried on this request's own history is the last resort."""
    chat_token = agent_app._current_chat_id.set("chat-restore-2")
    image_token = agent_app._current_image_b64.set("cmVxdWVzdC1oaXN0b3J5")
    try:
        monkeypatch.setattr(agent_app, "_download_current_image_from_s3", lambda chat_id: None)
        assert agent_app._get_current_image() == "cmVxdWVzdC1oaXN0b3J5"
        # This fallback is NOT cached in memory - it's per-request, not durable yet.
        assert "chat-restore-2" not in agent_app._current_working_image
    finally:
        agent_app._current_chat_id.reset(chat_token)
        agent_app._current_image_b64.reset(image_token)


def test_get_current_image_returns_none_when_nothing_available(monkeypatch):
    chat_token = agent_app._current_chat_id.set("chat-restore-3")
    image_token = agent_app._current_image_b64.set(None)
    try:
        monkeypatch.setattr(agent_app, "_download_current_image_from_s3", lambda chat_id: None)
        assert agent_app._get_current_image() is None
    finally:
        agent_app._current_chat_id.reset(chat_token)
        agent_app._current_image_b64.reset(image_token)
