from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import FileResponse
from prometheus_fastapi_instrumentator import Instrumentator
from ultralytics import YOLO
from PIL import Image
from pydantic import BaseModel
import logging
import os
import time
import signal
import sys

import torch
from sqlalchemy.orm import Session, selectinload

import db
from db import get_db
from models import DetectionObject, PredictionSession
from s3_utils import download_file_from_s3, upload_file_to_s3

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Disable GPU usage
torch.cuda.is_available = lambda: False

app = FastAPI()

# Expose /metrics endpoint with default process metrics + FastAPI HTTP metrics
Instrumentator().instrument(app).expose(app)

is_shutting_down = False


class PredictResponse(BaseModel):
    prediction_uid: str
    detection_count: int
    labels: list[str]
    time_took: float


class PredictRequest(BaseModel):
    image_s3_key: str
    prediction_id: str


def handle_sigterm(signum, frame):
    global is_shutting_down
    is_shutting_down = True
    logging.info("Received SIGTERM. Shutting down gracefully...")
    logging.info("Cleanup done. Exiting.")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

# Confidence threshold for object detection (0.0 - 1.0).
# Detections below this score are discarded.
# Override with: export CONFIDENCE_THRESHOLD=0.7
_raw_threshold = os.environ.get("CONFIDENCE_THRESHOLD")
if _raw_threshold is not None:
    CONFIDENCE_THRESHOLD = float(_raw_threshold)
    logging.info(f"CONFIDENCE_THRESHOLD set to {CONFIDENCE_THRESHOLD} (from environment)")
else:  # pragma: no cover
    CONFIDENCE_THRESHOLD = 0.5
    logging.info(f"CONFIDENCE_THRESHOLD not set, using default: {CONFIDENCE_THRESHOLD}")

UPLOAD_DIR = "uploads/original"
PREDICTED_DIR = "uploads/predicted"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PREDICTED_DIR, exist_ok=True)

# Download the AI model (tiny model ~6MB)
model = YOLO("yolov8n.pt")


def init_db(db_path: str | None = None):
    from db import init_db as db_init

    db_init(db_path)


def _format_timestamp(timestamp):
    if timestamp is None:
        return None
    return timestamp.strftime("%Y-%m-%d %H:%M:%S")


def save_prediction_session(db_session: Session, uid: str, original_image: str, predicted_image: str):
    prediction = PredictionSession(
        uid=uid,
        original_image=original_image,
        predicted_image=predicted_image,
    )
    db_session.add(prediction)
    return prediction


def save_detection_object(db_session: Session, prediction_uid: str, label: str, score: float, box: list):
    detection = DetectionObject(
        prediction_uid=prediction_uid,
        label=label,
        score=score,
        box=str(box),
    )
    db_session.add(detection)
    return detection


# 1. Health - return service status for readiness checks
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/RINA")
def rina():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    if is_shutting_down:
        raise HTTPException(status_code=503, detail="Service is shutting down")
    return {"status": "ready"}


# 2. Predict - upload an image, run object detection, and save results
@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest, db_session: Session = Depends(get_db)):
    allowed_extensions = (".jpg", ".jpeg", ".png")
    image_s3_key = request.image_s3_key.strip()
    if not image_s3_key:
        raise HTTPException(status_code=400, detail="Only image files are supported")

    source_filename = os.path.basename(image_s3_key)
    if not source_filename.lower().endswith(allowed_extensions):
        raise HTTPException(status_code=400, detail="Only image files are supported")

    start_time = time.time()
    ext = os.path.splitext(source_filename)[1]
    uid = request.prediction_id.strip()
    if not uid:
        raise HTTPException(status_code=400, detail="prediction_id is required")

    original_path = os.path.join(UPLOAD_DIR, uid + ext)
    predicted_path = os.path.join(PREDICTED_DIR, uid + ext)

    download_file_from_s3(image_s3_key, original_path)

    results = model(original_path, device="cpu", conf=CONFIDENCE_THRESHOLD)

    annotated_frame = results[0].plot()
    annotated_image = Image.fromarray(annotated_frame)
    annotated_image.save(predicted_path)

    if "/original/" not in image_s3_key:
        raise HTTPException(status_code=400, detail="image_s3_key must contain /original/")

    predicted_s3_key = image_s3_key.replace("/original/", "/predicted/", 1)

    upload_file_to_s3(predicted_path, predicted_s3_key)

    save_prediction_session(db_session, uid, image_s3_key, predicted_s3_key)

    detected_labels = []
    for box in results[0].boxes:
        label_idx = int(box.cls[0].item())
        label = model.names[label_idx]
        score = float(box.conf[0])
        bbox = box.xyxy[0].tolist()
        save_detection_object(db_session, uid, label, score, bbox)
        detected_labels.append(label)

    db_session.commit()
    processing_time = round(time.time() - start_time, 2)

    return PredictResponse(
        prediction_uid=uid,
        detection_count=len(results[0].boxes),
        labels=detected_labels,
        time_took=processing_time,
    )


# 3. Get prediction by UID - return session details and detected objects
@app.get("/prediction/{uid}")
def get_prediction_by_uid(uid: str, db_session: Session = Depends(get_db)):
    prediction = (
        db_session
        .query(PredictionSession)
        .options(selectinload(PredictionSession.detection_objects))
        .filter(PredictionSession.uid == uid)
        .first()
    )

    if not prediction:
        raise HTTPException(status_code=404, detail="Prediction not found")

    return {
        "uid": prediction.uid,
        "timestamp": _format_timestamp(prediction.timestamp),
        "original_image": prediction.original_image,
        "predicted_image": prediction.predicted_image,
        "detection_objects": [
            {
                "id": obj.id,
                "label": obj.label,
                "score": obj.score,
                "box": obj.box,
            }
            for obj in prediction.detection_objects
        ],
    }


# 4. Get prediction image - return the annotated image file for a prediction
@app.get("/prediction/{uid}/image")
def get_prediction_image(uid: str, db_session: Session = Depends(get_db)):
    prediction = db_session.get(PredictionSession, uid)
    if not prediction:
        raise HTTPException(status_code=404, detail="Image not found")

    predicted_ref = prediction.predicted_image

    if os.path.exists(predicted_ref):
        return FileResponse(predicted_ref)

    try:
        ext = os.path.splitext(os.path.basename(predicted_ref))[1] or ".jpg"
        local_path = os.path.join(PREDICTED_DIR, f"{uid}_download{ext}")
        download_file_from_s3(predicted_ref, local_path)
    except Exception:
        raise HTTPException(status_code=404, detail="Image not found")

    if not os.path.exists(local_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(local_path)


# 5. Get predictions by score - list detection objects with score >= min_score
@app.get("/predictions/score/{min_score}")
def get_predictions_by_score(min_score: float, db_session: Session = Depends(get_db)):
    if min_score < 0.0 or min_score > 1.0:
        raise HTTPException(status_code=400, detail="min_score must be between 0.0 and 1.0")

    objects = (
        db_session
        .query(DetectionObject)
        .filter(DetectionObject.score >= min_score)
        .all()
    )

    return [
        {
            "id": obj.id,
            "prediction_uid": obj.prediction_uid,
            "label": obj.label,
            "score": obj.score,
            "box": obj.box,
        }
        for obj in objects
    ]


# 6. Get predictions by empty label - return error when no label is provided
@app.get("/predictions/label/")
def get_predictions_by_empty_label():
    raise HTTPException(status_code=400, detail="Label cannot be empty")


# 7. Get predictions by label - return all predictions containing the given label
@app.get("/predictions/label/{label}")
def get_predictions_by_label(label: str, db_session: Session = Depends(get_db)):
    if not label.strip():
        raise HTTPException(status_code=400, detail="Label cannot be empty")

    prediction_uids = (
        db_session
        .query(DetectionObject.prediction_uid)
        .filter(DetectionObject.label == label)
        .distinct()
        .all()
    )

    result = []
    for (prediction_uid,) in prediction_uids:
        prediction = db_session.get(PredictionSession, prediction_uid)
        if not prediction:
            continue

        result.append({
            "uid": prediction.uid,
            "timestamp": _format_timestamp(prediction.timestamp),
            "detection_objects": [
                {
                    "id": obj.id,
                    "label": obj.label,
                    "score": obj.score,
                    "box": obj.box,
                }
                for obj in prediction.detection_objects
                if obj.label == label
            ],
        })

    return result


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    init_db()
    uvicorn.run(app, host="0.0.0.0", port=8080)
# cache test
# cache test 2
