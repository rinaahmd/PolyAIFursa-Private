import os
import unittest
import tempfile
import uuid
import numpy as np
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import app as app_module
import db
from app import app, init_db

TEST_IMAGE = os.path.join(os.path.dirname(__file__), "data", "beatles.jpeg")


class TestPredictionsByScore(unittest.TestCase):
    def setUp(self):
        _, db.DB_PATH = tempfile.mkstemp(suffix=".db")
        init_db()
        self.client = TestClient(app)

    def _mock_yolo_with_objects(self, mock_model, mock_image):
        fake_person_box = MagicMock()
        fake_person_box.cls = [MagicMock(item=lambda: 0)]
        fake_person_box.conf = [0.95]
        fake_person_box.xyxy = [MagicMock(tolist=lambda: [10, 20, 30, 40])]

        fake_car_box = MagicMock()
        fake_car_box.cls = [MagicMock(item=lambda: 1)]
        fake_car_box.conf = [0.60]
        fake_car_box.xyxy = [MagicMock(tolist=lambda: [50, 60, 70, 80])]

        fake_result = MagicMock()
        fake_result.boxes = [fake_person_box, fake_car_box]
        fake_result.plot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

        mock_model.return_value = [fake_result]
        mock_model.names = {
            0: "person",
            1: "car"
        }

        fake_image = MagicMock()
        mock_image.fromarray.return_value = fake_image

    @patch("app.Image")
    @patch("app.model")
    @patch("app.upload_file_to_s3")
    @patch("app.download_file_from_s3")
    def test_get_predictions_by_score_returns_objects(self, mock_download, _mock_upload, mock_model, mock_image):
        self._mock_yolo_with_objects(mock_model, mock_image)

        def fake_download(_s3_key, local_path):
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(TEST_IMAGE, "rb") as src, open(local_path, "wb") as dst:
                dst.write(src.read())

        mock_download.side_effect = fake_download

        prediction_id = str(uuid.uuid4())
        response = self.client.post(
            "/predict",
            json={
                "image_s3_key": f"chat/{prediction_id}/original/beatles.jpeg",
                "prediction_id": prediction_id,
            },
        )

        self.assertEqual(response.status_code, 200)

        response = self.client.get("/predictions/score/0.5")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

        self.assertIn("id", data[0])
        self.assertIn("prediction_uid", data[0])
        self.assertIn("label", data[0])
        self.assertIn("score", data[0])
        self.assertIn("box", data[0])
        self.assertGreaterEqual(data[0]["score"], 0.5)

    def test_get_predictions_by_score_no_matches(self):
        response = self.client.get("/predictions/score/1.0")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_predictions_by_score_less_than_zero(self):
        response = self.client.get("/predictions/score/-0.1")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "min_score must be between 0.0 and 1.0"
        )

    def test_get_predictions_by_score_greater_than_one(self):
        response = self.client.get("/predictions/score/1.1")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "min_score must be between 0.0 and 1.0"
        )