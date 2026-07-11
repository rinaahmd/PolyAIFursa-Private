"""Tests for Bedrock content-block classification and the deterministic
flip/blur/noise fallback that runs when the LLM's own response is blocked.
See ContentBlockedError, _classify_client_error, _invoke_with_content_filter_retry,
_run_deterministic_fallback, and the ContentBlockedError handling in chat()."""

import json

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app as agent_app

client = TestClient(agent_app.app)

THE_PROMPT = (
    "add noise 0.6 to the person on the right and flip the person on the left "
    "and blur the second person to the left"
)


def _client_error(code: str, message: str, request_id: str = "req-123") -> ClientError:
    response = {
        "Error": {"Code": code, "Message": message},
        "ResponseMetadata": {"RequestId": request_id},
    }
    return ClientError(response, "Converse")


# --- _classify_client_error --------------------------------------------------


def test_classify_client_error_detects_image_block():
    exc = _client_error("ValidationException", "The provided image content is not allowed.")
    classified = agent_app._classify_client_error(exc)
    assert classified.block_kind == "input_image"
    assert "ValidationException" in classified.detail


def test_classify_client_error_detects_guardrail():
    exc = _client_error("ValidationException", "Request blocked by guardrail policy.")
    classified = agent_app._classify_client_error(exc)
    assert classified.block_kind == "guardrail"


def test_classify_client_error_detects_text_prompt_block():
    exc = _client_error("ValidationException", "Input violates our content policy for harmful content.")
    classified = agent_app._classify_client_error(exc)
    assert classified.block_kind == "text_prompt"


def test_classify_client_error_falls_back_to_other():
    exc = _client_error("ThrottlingException", "Rate exceeded.")
    classified = agent_app._classify_client_error(exc)
    assert classified.block_kind == "other"


def test_classify_client_error_never_raises_on_malformed_response():
    # response missing Error/ResponseMetadata entirely - must not crash.
    exc = ClientError({}, "Converse")
    classified = agent_app._classify_client_error(exc)
    assert classified.block_kind == "other"


# --- _invoke_with_content_filter_retry ---------------------------------------


def test_invoke_retry_raises_content_blocked_error_on_thrown_client_error():
    class FakeLLM:
        def invoke(self, _messages):
            raise _client_error("ValidationException", "harmful content policy violation")

    with pytest.raises(agent_app.ContentBlockedError) as exc_info:
        agent_app._invoke_with_content_filter_retry(FakeLLM(), [])
    assert exc_info.value.block_kind == "text_prompt"


def test_invoke_retry_raises_after_exhausting_retries_on_in_band_block():
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            self.calls += 1
            return AIMessage(
                content="The generated text has been blocked by our content filters.",
                tool_calls=[],
                response_metadata={"stopReason": "content_filtered"},
            )

    fake_llm = FakeLLM()
    with pytest.raises(agent_app.ContentBlockedError) as exc_info:
        agent_app._invoke_with_content_filter_retry(fake_llm, [], max_retries=2)
    assert exc_info.value.block_kind == "model_output"
    assert fake_llm.calls == 3  # initial attempt + 2 retries


def test_invoke_retry_succeeds_after_transient_in_band_block():
    responses = [
        AIMessage(content="", tool_calls=[], response_metadata={"stopReason": "content_filtered"}),
        AIMessage(content="ok now", tool_calls=[], response_metadata={"stopReason": "end_turn"}),
    ]

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            response = responses[self.calls]
            self.calls += 1
            return response

    result = agent_app._invoke_with_content_filter_retry(FakeLLM(), [], max_retries=4)
    assert result.content == "ok now"


# --- run_agent propagates ContentBlockedError with tools_called -------------


def test_run_agent_attaches_tools_called_so_far_to_content_blocked_error(monkeypatch):
    first = AIMessage(
        content="calling tool",
        tool_calls=[{"id": "tool-1", "name": "detect_objects", "args": {}}],
    )

    class FakeLLMWithTools:
        def __init__(self, responses):
            self._responses = list(responses)
            self._idx = 0

        def invoke(self, _messages):
            if self._idx == 0:
                self._idx += 1
                return first
            raise _client_error("ValidationException", "harmful content policy")

    from langchain_core.messages import ToolMessage

    class FakeDetectObjectsTool:
        name = "detect_objects"

        def invoke(self, tool_call):
            return ToolMessage(tool_call_id=tool_call["id"], content=json.dumps({"prediction_uid": "p1"}))

    monkeypatch.setattr(agent_app, "llm_with_tools", FakeLLMWithTools([]))
    monkeypatch.setitem(agent_app.TOOLS, "detect_objects", FakeDetectObjectsTool())
    monkeypatch.setattr(agent_app, "_fetch_annotated_image", lambda prediction_id: None)

    from langchain_core.messages import HumanMessage

    with pytest.raises(agent_app.ContentBlockedError) as exc_info:
        agent_app.run_agent([HumanMessage(content="do stuff")])

    assert exc_info.value.tools_called == ["detect_objects"]


# --- _run_deterministic_fallback ---------------------------------------------


class _FakeToolCallable:
    """Stand-in for a langchain @tool-decorated function - just needs a
    `.name` and `.invoke(kwargs)`, matching what _run_deterministic_fallback
    actually calls. Real StructuredTool instances are Pydantic models and
    reject ad-hoc monkeypatching of their `.invoke` attribute, so tests swap
    the whole callable (via monkeypatch.setattr(agent_app, "detect_objects", ...)
    or the _OBJECT_TOOL_BY_OPERATION / _WHOLE_IMAGE_TOOL_BY_OPERATION dispatch
    dicts) instead of patching a method on the real tool object."""

    def __init__(self, name, calls, result_operation=None, fail=False, on_invoke=None):
        self.name = name
        self._calls = calls
        self._result_operation = result_operation or name
        self._fail = fail
        self._on_invoke = on_invoke

    def invoke(self, kwargs):
        self._calls.append((self.name, dict(kwargs)))
        if self._on_invoke:
            return self._on_invoke(kwargs)
        if self._fail:
            return json.dumps({"error": f"{self.name} failed"})
        op_id = f"op-{self.name}"
        agent_app._processed_images[op_id] = f"result-after-{self.name}"
        return json.dumps({"status": "ok", "operation": self._result_operation, "operation_id": op_id})


def test_deterministic_fallback_calls_detect_objects_then_all_three_object_tools(monkeypatch):
    detections = [
        {"index": 0, "label": "person", "box": [0, 0, 100, 100]},
        {"index": 1, "label": "person", "box": [150, 0, 250, 100]},
        {"index": 2, "label": "person", "box": [300, 0, 400, 100]},
    ]
    agent_app._add_left_right_rank(detections)

    calls = []

    def fake_detect_objects_invoke(_kwargs):
        agent_app._detections_by_chat["fallback-chat"] = detections
        return json.dumps({"prediction_uid": "pred-xyz", "detections": detections})

    monkeypatch.setattr(
        agent_app, "detect_objects",
        _FakeToolCallable("detect_objects", calls, on_invoke=fake_detect_objects_invoke),
    )
    monkeypatch.setitem(agent_app._OBJECT_TOOL_BY_OPERATION, "flip", _FakeToolCallable("flip_object", calls, "flip"))
    monkeypatch.setitem(agent_app._OBJECT_TOOL_BY_OPERATION, "blur", _FakeToolCallable("blur_object", calls, "blur"))
    monkeypatch.setitem(
        agent_app._OBJECT_TOOL_BY_OPERATION, "add_noise", _FakeToolCallable("add_noise_object", calls, "add_noise")
    )
    monkeypatch.setattr(agent_app, "_fetch_annotated_image", lambda prediction_id: "YW5ub3RhdGVk")
    monkeypatch.setattr(agent_app, "_processed_images", {})
    monkeypatch.setattr(agent_app, "_detections_by_chat", {})

    result = agent_app._run_deterministic_fallback("fallback-chat", THE_PROMPT)

    # Clause order in THE_PROMPT is "add noise ... and flip ... and blur ...",
    # so the object-scoped tools fire in that same left-to-right order -
    # excluding the detect_objects call, which happens once, up front.
    object_tool_calls = [(name, kwargs) for name, kwargs in calls if name != "detect_objects"]
    called_tool_names = [name for name, _ in object_tool_calls]
    assert called_tool_names == ["add_noise_object", "flip_object", "blur_object"]

    add_noise_kwargs = dict(object_tool_calls[0][1])
    assert add_noise_kwargs["amount"] == 0.6
    assert add_noise_kwargs["rank_from_right"] == 1  # "the person on the right"

    flip_kwargs = dict(object_tool_calls[1][1])
    assert flip_kwargs["rank_from_left"] == 1  # "the person on the left"

    blur_kwargs = dict(object_tool_calls[2][1])
    assert blur_kwargs["rank_from_left"] == 2  # "the second person to the left"

    assert result["tools_called"] == ["detect_objects", "add_noise_object", "flip_object", "blur_object"]
    assert result["prediction_id"] == "pred-xyz"
    # The LAST clause executed (blur, per the prompt's own left-to-right
    # order) determines the final processed_image - each edit builds on/
    # overwrites the previous one's result, same as the LLM-driven path.
    assert result["processed_image"] == "result-after-blur_object"
    assert "flip" in result["response"] and "blur" in result["response"] and "add_noise" in result["response"]


def test_deterministic_fallback_uses_whole_image_tool_when_no_object_named(monkeypatch):
    calls = []

    monkeypatch.setitem(agent_app._WHOLE_IMAGE_TOOL_BY_OPERATION, "flip", _FakeToolCallable("flip_image", calls))
    monkeypatch.setattr(agent_app, "_processed_images", {})

    result = agent_app._run_deterministic_fallback("fallback-chat-2", "flip the image")

    assert calls == [("flip_image", {})]
    assert result["tools_called"] == ["flip_image"]


def test_deterministic_fallback_returns_error_response_when_nothing_parseable(monkeypatch):
    result = agent_app._run_deterministic_fallback("fallback-chat-3", "please make it prettier somehow")

    assert result["tools_called"] == []
    assert result["processed_image"] is None
    assert "could not" in result["response"].lower() or "no supported" in result["response"].lower()


def test_deterministic_fallback_reports_partial_success_when_one_tool_errors(monkeypatch):
    calls = []

    def fake_detect_objects_invoke(_kwargs):
        agent_app._detections_by_chat["fallback-chat-4"] = []
        return json.dumps({"prediction_uid": "pred-1", "detections": []})

    monkeypatch.setattr(
        agent_app, "detect_objects",
        _FakeToolCallable("detect_objects", calls, on_invoke=fake_detect_objects_invoke),
    )
    monkeypatch.setattr(agent_app, "_fetch_annotated_image", lambda prediction_id: None)
    monkeypatch.setattr(agent_app, "_detections_by_chat", {})

    result = agent_app._run_deterministic_fallback("fallback-chat-4", "flip the person on the left")

    # No person detected (empty detections), so _resolve_object_box errors out.
    assert result["processed_image"] is None
    assert "blocked" in result["response"].lower()


# --- /chat endpoint wiring ----------------------------------------------------


def test_chat_endpoint_returns_specific_message_for_input_image_block(monkeypatch):
    def fake_run_agent(history, max_iterations=10):
        raise agent_app.ContentBlockedError("input_image", "ValidationException: bad image", tools_called=[])

    monkeypatch.setattr(agent_app, "run_agent", fake_run_agent)

    response = client.post("/chat", json={"messages": [{"role": "user", "content": "flip it"}]})

    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "The provider blocked the uploaded image."
    assert data["tools_called"] == []
    # The raw AWS error detail must never reach the frontend response.
    assert "ValidationException" not in data["response"]


def test_chat_endpoint_falls_through_to_generic_message_when_fallback_finds_nothing(monkeypatch):
    def fake_run_agent(history, max_iterations=10):
        raise agent_app.ContentBlockedError("text_prompt", "ValidationException: bad prompt", tools_called=[])

    monkeypatch.setattr(agent_app, "run_agent", fake_run_agent)

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "please make it look nicer somehow"}]},
    )

    assert response.status_code == 200
    data = response.json()
    # text_prompt IS fallback-eligible, but this message has no flip/blur/noise
    # keyword to parse, so _run_deterministic_fallback itself reports it found
    # nothing - that response is what reaches the frontend, not the raw AWS detail.
    assert data["tools_called"] == []
    assert "could not" in data["response"].lower() or "no supported" in data["response"].lower()


def test_chat_endpoint_returns_generic_message_for_guardrail_block_with_unparseable_text(monkeypatch):
    def fake_run_agent(history, max_iterations=10):
        raise agent_app.ContentBlockedError("guardrail", "guardrail intervened", tools_called=[])

    monkeypatch.setattr(agent_app, "run_agent", fake_run_agent)

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "do something unrelated"}]},
    )

    assert response.status_code == 200
    data = response.json()
    # "guardrail" is not in the fallback-eligible set (text_prompt, model_output),
    # so this goes straight to the generic block-kind message.
    assert data["response"] == "A configured content safety guardrail blocked this request."


def test_chat_endpoint_invokes_fallback_and_returns_its_result_for_model_output_block(monkeypatch):
    def fake_run_agent(history, max_iterations=10):
        raise agent_app.ContentBlockedError("model_output", "stopReason=content_filtered", tools_called=["detect_objects"])

    fallback_result = {
        "response": "Completed deterministically: flip.",
        "tools_called": ["detect_objects", "flip_object"],
        "processed_image": "ZmFrZS1yZXN1bHQ=",
        "prediction_id": "pred-99",
        "annotated_image": None,
    }

    monkeypatch.setattr(agent_app, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_app, "_run_deterministic_fallback", lambda chat_id, text: fallback_result)

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "flip the person on the left"}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["response"] == "Completed deterministically: flip."
    assert data["tools_called"] == ["detect_objects", "flip_object"]
    assert data["processed_image"] == "ZmFrZS1yZXN1bHQ="


def test_chat_endpoint_does_not_use_fallback_for_input_image_block(monkeypatch):
    fallback_called = {"value": False}

    def fake_run_agent(history, max_iterations=10):
        raise agent_app.ContentBlockedError("input_image", "bad image", tools_called=[])

    def fake_fallback(chat_id, text):
        fallback_called["value"] = True
        return {"response": "should not be used", "tools_called": [], "processed_image": None,
                "prediction_id": None, "annotated_image": None}

    monkeypatch.setattr(agent_app, "run_agent", fake_run_agent)
    monkeypatch.setattr(agent_app, "_run_deterministic_fallback", fake_fallback)

    response = client.post("/chat", json={"messages": [{"role": "user", "content": "flip it"}]})

    assert response.status_code == 200
    assert fallback_called["value"] is False
    assert response.json()["response"] == "The provider blocked the uploaded image."
