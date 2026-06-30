import os
import unittest
import tempfile
import uuid
from fastapi.testclient import TestClient
import app as app_module
import db
from app import app, init_db
from unittest.mock import patch

TEST_IMAGE = os.path.join(os.path.dirname(__file__), "data", "beatles.jpeg")


class TestPredictionTime(unittest.TestCase):
    def setUp(self):
        _, db.DB_PATH = tempfile.mkstemp(suffix=".db")
        init_db()
        self.client = TestClient(app)

    @patch("app.upload_file_to_s3")
    @patch("app.download_file_from_s3")
    def test_predict_includes_processing_time(self, mock_download, _mock_upload):
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
        data = response.json()
        self.assertIn("time_took", data)
        self.assertIsInstance(data["time_took"], (int, float))
        self.assertGreaterEqual(data["time_took"], 0)
