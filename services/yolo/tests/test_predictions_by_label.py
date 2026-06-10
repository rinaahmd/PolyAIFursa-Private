import os
import unittest
import tempfile
from fastapi.testclient import TestClient
import app as app_module
from app import app, init_db

TEST_IMAGE = os.path.join(os.path.dirname(__file__), "data", "beatles.jpeg")


class TestPredictionsByLabel(unittest.TestCase):
    def setUp(self):
        _, app_module.DB_PATH = tempfile.mkstemp(suffix=".db")
        init_db()
        self.client = TestClient(app)

    def test_get_predictions_by_label_existing_label(self):
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
        self.assertEqual(data[0]["detection_objects"][0]["label"], "person")

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
        
    