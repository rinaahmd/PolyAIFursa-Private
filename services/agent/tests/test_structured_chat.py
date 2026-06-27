import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

import app as agent_app


@pytest.fixture
def api_client():
    return TestClient(agent_app.app)


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


def test_chat_returns_structured_response_without_tool_calls(api_client, monkeypatch):
    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools([AIMessage(content="hello", tool_calls=[])]),
    )

    response = api_client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "hello"
    assert data["prediction_id"] is None
    assert data["annotated_image"] is None
    assert isinstance(data["agent_loop_time_s"], float)
    assert data["iterations"] == 1
    assert data["tools_called"] == []
    assert data["context_limit_exceeded"] is False


def test_chat_sets_context_limit_exceeded_on_max_iterations(api_client, monkeypatch):
    looping_responses = [
        AIMessage(
            content="",
            tool_calls=[{"id": f"tool-{i}", "name": "unknown_tool", "args": {}}],
        )
        for i in range(1, 11)
    ]

    monkeypatch.setattr(
        agent_app,
        "llm_with_tools",
        FakeLLMWithTools(looping_responses),
    )

    response = api_client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "force loop"}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "Agent stopped because it reached the maximum number of tool iterations."
    assert data["iterations"] == 10
    assert data["tools_called"] == ["unknown_tool"] * 10
    assert data["context_limit_exceeded"] is True


def test_chat_extracts_prediction_id_and_annotated_image(api_client, monkeypatch):
    first = AIMessage(
        content="calling tool",
        tool_calls=[{"id": "tool-1", "name": "detect_objects", "args": {}}],
    )
    second = AIMessage(content="done", tool_calls=[])
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

    response = api_client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "check image", "image_base64": "dGVzdA=="}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "done"
    assert data["prediction_id"] == "pred-123"
    assert data["annotated_image"] == "ZmFrZS1pbWFnZQ=="
    assert data["tools_called"] == ["detect_objects"]
    assert data["context_limit_exceeded"] is False


def test_chat_does_not_crash_on_invalid_tool_json(api_client, monkeypatch):
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

    response = api_client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "check image", "image_base64": "dGVzdA=="}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "done"
    assert data["prediction_id"] is None
    assert data["annotated_image"] is None
    assert data["tools_called"] == ["detect_objects"]
    assert data["context_limit_exceeded"] is False
