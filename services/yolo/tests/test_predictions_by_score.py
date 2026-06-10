import os
import unittest
import tempfile
from fastapi.testclient import TestClient
import app as app_module
from app import app, init_db

TEST_IMAGE = os.path.join(os.path.dirname(__file__), "data", "beatles.jpeg")


class TestPredictionsByScore(unittest.TestCase):
    def setUp(self):
        _, app_module.DB_PATH = tempfile.mkstemp(suffix=".db")
        init_db()
        self.client = TestClient(app)

    def test_get_predictions_by_score_returns_objects(self):
        with open(TEST_IMAGE, "rb") as f:
            response = self.client.post(
                "/predict",
                files={"file": ("beatles.jpeg", f, "image/jpeg")}
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