# YOLO Object Detection Service

This is a FastAPI-based web service that performs object detection on uploaded images using the YOLOv8 model. The application analyzes images, detects objects, and stores prediction results in a SQLite database for later retrieval.

## Setup Instructions

1. Make sure the shared project virtualenv is activated (see the root README).

1. Install requirements (from `services/yolo/`):

```bash
pip install -r torch-requirements.txt
pip install -r requirements.txt
```

1. Run the application:

```bash
python app.py
```

The service will be available at http://<your_server_ip>:8080

You can test the api endpoints using `curl` or Postman. See the API Endpoints section below for details on available endpoints and how to use them.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | `0.5` | Minimum confidence score (0.0–1.0) for a detection to be reported. Raise it to get only high-confidence results; lower it to catch more objects. |
| `AWS_REGION` | - | AWS region used to access S3 |
| `AWS_S3_BUCKET` | - | Bucket that stores original and predicted images |

Example:
```bash
export CONFIDENCE_THRESHOLD=0.7
python app.py
```

## Running Tests

The test suite uses `pytest` and FastAPI's built-in test client — no running server needed.

```bash
pytest tests/
```


## API Endpoints

* `POST /predict` - Trigger object detection from an S3 image key
* `GET /prediction/{uid}` - Get details of a specific prediction by ID
* `GET /predictions/label/{label}` - Get all predictions containing a specific object label (e.g., "person", "car")
* `GET /predictions/score/{min_score}` - Get predictions with confidence score above threshold (e.g., 0.5)
* `GET /prediction/{uid}/image` - Get the processed image with detection boxes
* `GET /image/{type}/{filename}` - Get original or predicted image by filename

## Testing the API

You can use tools like curl, Postman, or a web browser to test the endpoints. For example:

1. Upload an image:
```bash
curl -X POST http://localhost:8080/predict \
	-H "Content-Type: application/json" \
	-d '{"image_s3_key":"chat/demo/original/your_image.jpg","prediction_id":"demo-prediction-id"}'
```

2. View detection results (replace {uid} with the ID returned from the upload):
```bash
curl http://localhost:8080/prediction/{uid} 