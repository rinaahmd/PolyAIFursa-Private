import os
import unittest
import tempfile
from fastapi.testclient import TestClient
import app as app_module
from app import app, init_db
from unittest.mock import MagicMock, patch
import numpy as np

TEST_IMAGE = os.path.join(os.path.dirname(__file__), "data", "beatles.jpeg")


class TestPredictionsByLabel(unittest.TestCase):
    def setUp(self):
        _, app_module.DB_PATH = tempfile.mkstemp(suffix=".db")
        init_db()
        self.client = TestClient(app)

    @patch("app.Image")
    @patch("app.model")
    def test_get_predictions_by_label_existing_label(self, mock_model, mock_image):

        fake_box = MagicMock()

        fake_box.cls = [MagicMock(item=lambda: 0)]
        fake_box.conf = [0.95]
        fake_box.xyxy = [MagicMock(tolist=lambda: [10, 20, 30, 40])]

        fake_result = MagicMock()
        fake_result.boxes = [fake_box]
        fake_result.plot.return_value = np.zeros((100, 100, 3), dtype=np.uint8)

        mock_model.return_value = [fake_result]
        mock_model.names = {0: "person"}

        fake_image = MagicMock()
        mock_image.fromarray.return_value = fake_image

        with open(TEST_IMAGE, "rb") as f:
            response = self.client.post(
                "/predict",
                files={"file": ("beatles.jpeg", f, "image/jpeg")}
            )

        self.assertEqual(response.status_code, 200)

        response = self.client.get("/predictions/label/person")

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

        self.assertIn("uid", data[0])
        self.assertIn("timestamp", data[0])
        self.assertIn("detection_objects", data[0])

        self.assertIsInstance(data[0]["detection_objects"], list)
        self.assertGreater(len(data[0]["detection_objects"]), 0)

        self.assertEqual(
            data[0]["detection_objects"][0]["label"],
            "person"
        )
    def test_get_predictions_by_label_not_existing_label(self):
        response = self.client.get("/predictions/label/not-existing-label")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_predictions_by_label_empty_label(self):
        response = self.client.get("/predictions/label/")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Label cannot be empty")
        
    def test_get_predictions_by_label_whitespace_label(self):
        response = self.client.get("/predictions/label/   ")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Label cannot be empty")
        
    