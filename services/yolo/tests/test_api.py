import os
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
import numpy as np
from unittest.mock import MagicMock

os.environ.setdefault("CONFIDENCE_THRESHOLD", "0.5")

from app import app, init_db

TEST_IMAGE = os.path.join(os.path.dirname(__file__), "data", "beatles.jpeg")


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_predictions.db")
    monkeypatch.setattr("app.db.DB_PATH", db_file)
    init_db()


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_predict_rejects_non_image_file(client):
    response = client.post(
        "/predict",
        files={"file": ("document.pdf", b"fake pdf content", "application/pdf")}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Only image files are supported"}

def create_prediction_with_mock(client, monkeypatch):
    fake_box = MagicMock()
    fake_box.cls = [MagicMock(item=lambda: 0)]
    fake_box.conf = [0.95]
    fake_box.xyxy = [MagicMock(tolist=lambda: [10, 20, 30, 40])]

    fake_result = MagicMock()
    fake_result.boxes = [fake_box]
    fake_result.plot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

    fake_model = MagicMock()
    fake_model.return_value = [fake_result]
    fake_model.names = {0: "person"}

    fake_image = MagicMock()
    def save_to_disk(path, *args, **kwargs):
        open(path, "wb").close()
    fake_image.save.side_effect = save_to_disk

    fake_image_module = MagicMock()
    fake_image_module.fromarray.return_value = fake_image

    monkeypatch.setattr("app.model", fake_model)
    monkeypatch.setattr("app.Image", fake_image_module)

    with open(TEST_IMAGE, "rb") as f:
        response = client.post(
            "/predict",
            files={"file": ("beatles.jpeg", f, "image/jpeg")}
        )

    assert response.status_code == 200
    return response.json()["prediction_uid"]







def test_get_prediction_by_uid(client, monkeypatch):
    uid = create_prediction_with_mock(client, monkeypatch)

    response = client.get(f"/prediction/{uid}")

    assert response.status_code == 200
    data = response.json()

    assert data["uid"] == uid
    assert "timestamp" in data
    assert "original_image" in data
    assert "predicted_image" in data
    assert "detection_objects" in data

    assert isinstance(data["detection_objects"], list)
    assert len(data["detection_objects"]) == 1
    assert data["detection_objects"][0]["label"] == "person"
    assert data["detection_objects"][0]["score"] == 0.95


def test_get_prediction_timestamp_is_persisted(client, monkeypatch):
    uid = create_prediction_with_mock(client, monkeypatch)

    response = client.get(f"/prediction/{uid}")
    assert response.status_code == 200

    data = response.json()
    assert data["timestamp"] is not None
    assert isinstance(data["timestamp"], str)
    datetime.fromisoformat(data["timestamp"])


def test_get_prediction_by_uid_not_found(client):
    response = client.get("/prediction/not-existing-uid")

    assert response.status_code == 404
    assert response.json()["detail"] == "Prediction not found"


def test_rina_endpoint(client):
    response = client.get("/RINA")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_endpoint_when_running(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_endpoint_when_shutting_down(client, monkeypatch):
    monkeypatch.setattr("app.is_shutting_down", True)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "Service is shutting down"


def test_predict_response_has_zero_detections_when_model_returns_none(client, monkeypatch):
    fake_result = MagicMock()
    fake_result.boxes = []
    fake_result.plot.return_value = __import__("numpy").zeros((100, 100, 3), dtype=__import__("numpy").uint8)

    fake_model = MagicMock()
    fake_model.return_value = [fake_result]
    fake_model.names = {}

    fake_image = MagicMock()
    def save_to_disk(path, *args, **kwargs):
        open(path, "wb").close()
    fake_image.save.side_effect = save_to_disk

    fake_image_module = MagicMock()
    fake_image_module.fromarray.return_value = fake_image

    monkeypatch.setattr("app.model", fake_model)
    monkeypatch.setattr("app.Image", fake_image_module)

    with open(TEST_IMAGE, "rb") as f:
        response = client.post(
            "/predict",
            files={"file": ("beatles.jpeg", f, "image/jpeg")}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["detection_count"] == 0
    assert data["labels"] == []

def test_get_prediction_image(client, monkeypatch):
    uid = create_prediction_with_mock(client, monkeypatch)

    response = client.get(f"/prediction/{uid}/image")

    assert response.status_code == 200


def test_get_prediction_image_not_found(client):
    response = client.get("/prediction/not-existing-uid/image")

    assert response.status_code == 404
    assert response.json()["detail"] == "Image not found"