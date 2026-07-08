from fastapi.testclient import TestClient

import app as agent_app


client = TestClient(agent_app.app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_returns_structured_response_from_mocked_run_agent(monkeypatch):
    mocked_response = {
        "response": "hello from mock",
        "prediction_id": "pred-123",
        "annotated_image": "ZmFrZS1pbWFnZQ==",
        "processed_image": None,
        "agent_loop_time_s": 0.123,
        "iterations": 2,
        "tools_called": ["detect_objects"],
        "context_limit_exceeded": False,
        "tokens_used": {"input": 11, "output": 7, "total": 18},
    }

    def fake_run_agent(history, max_iterations=10):
        assert len(history) == 1
        assert history[0].content == "hello"
        assert max_iterations == 10
        return mocked_response

    monkeypatch.setattr(agent_app, "run_agent", fake_run_agent)

    response = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data == mocked_response
    assert "response" in data
    assert "prediction_id" in data
    assert "annotated_image" in data
    assert "processed_image" in data
    assert "agent_loop_time_s" in data
    assert "iterations" in data
    assert "tools_called" in data
    assert "context_limit_exceeded" in data
    assert "tokens_used" in data


def test_chat_rejects_invalid_request_shape():
    response = client.post(
        "/chat",
        json={"message": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 422
