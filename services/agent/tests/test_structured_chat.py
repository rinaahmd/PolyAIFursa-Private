import json

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
