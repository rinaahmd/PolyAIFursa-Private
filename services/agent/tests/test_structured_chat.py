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
    async def fake_call_mcp_blur(image_b64, radius):
        assert image_b64 == "aW1hZ2U="
        assert radius == 3.0
        return "Ymx1cnJlZC1pbWFnZQ=="

    monkeypatch.setattr(agent_app, "_call_mcp_blur", fake_call_mcp_blur)
    monkeypatch.setattr(agent_app, "_blurred_images", {})

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
    assert agent_app._blurred_images[operation_id] == "Ymx1cnJlZC1pbWFnZQ=="


def test_blur_image_returns_error_when_no_image_provided():
    image_token = agent_app._current_image_b64.set(None)
    try:
        raw = agent_app.blur_image.invoke({})
    finally:
        agent_app._current_image_b64.reset(image_token)

    assert json.loads(raw) == {"error": "No image was provided by the user."}


def test_blur_image_returns_error_when_mcp_call_fails(monkeypatch):
    async def failing_call_mcp_blur(image_b64, radius):
        raise RuntimeError("img-proc-mcp unreachable")

    monkeypatch.setattr(agent_app, "_call_mcp_blur", failing_call_mcp_blur)

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

    monkeypatch.setattr(agent_app, "_blurred_images", {"op-1": "Ymx1cnJlZC1pbWFnZQ=="})

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
    assert "op-1" not in agent_app._blurred_images


def test_run_agent_processed_image_is_none_when_blur_not_called(monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools([AIMessage(content="hello", tool_calls=[])]),
    )

    data = agent_app.run_agent([HumanMessage(content="hello")])

    assert data["processed_image"] is None
