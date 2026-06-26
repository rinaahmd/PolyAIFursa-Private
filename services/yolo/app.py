from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import FileResponse
from prometheus_fastapi_instrumentator import Instrumentator
from ultralytics import YOLO
from PIL import Image
import logging
import os
import uuid
import shutil
import time
import signal
import sys

import torch
from sqlalchemy.orm import Session, joinedload

import db
import models

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Disable GPU usage
torch.cuda.is_available = lambda: False

app = FastAPI()

# Expose /metrics endpoint with default process metrics + FastAPI HTTP metrics
Instrumentator().instrument(app).expose(app)




















is_shutting_down = False

def handle_sigterm(signum, frame):
    global is_shutting_down
    is_shutting_down = True
    logging.info("Received SIGTERM. Shutting down gracefully...")
    logging.info("Cleanup done. Exiting.")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)


####heyyyy













# Confidence threshold for object detection (0.0 - 1.0).
# Detections below this score are discarded.
# Override with: export CONFIDENCE_THRESHOLD=0.7
_raw_threshold = os.environ.get("CONFIDENCE_THRESHOLD")
if _raw_threshold is not None:
    CONFIDENCE_THRESHOLD = float(_raw_threshold)
    logging.info(f"CONFIDENCE_THRESHOLD set to {CONFIDENCE_THRESHOLD} (from environment)")
else:# pragma: no cover
    CONFIDENCE_THRESHOLD = 0.5
    logging.info(f"CONFIDENCE_THRESHOLD not set, using default: {CONFIDENCE_THRESHOLD}")

UPLOAD_DIR = "uploads/original"
PREDICTED_DIR = "uploads/predicted"
DB_PATH = "predictions.db"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PREDICTED_DIR, exist_ok=True)

# Download the AI model (tiny model ~6MB)
model = YOLO("yolov8n.pt")


def init_db(db_path: str | None = None):
    global DB_PATH
    if db_path is not None:
        DB_PATH = db_path
    db.configure_session(DB_PATH)
    models.Base.metadata.create_all(bind=db.engine)


@app.on_event("startup")
def on_startup():
    models.Base.metadata.create_all(bind=db.engine)



# 1. Health - return service status for readiness checks
@app.get("/health")
def health():
    """Health check endpoint"""
    return {"status": "ok"}


@app.get("/RINA")
def rina():
    """Health check endpoint"""
    return {"status": "ok"}







@app.get("/ready")
def ready():
    if is_shutting_down:
        raise HTTPException(status_code=503, detail="Service is shutting down")
    return {"status": "ready"}





# 2. Predict - upload an image, run object detection, and save results
@app.post("/predict")
def predict(file: UploadFile = File(...), db_session: Session = Depends(db.get_db)):
    """
    Predict objects in an image
    """
    start_time = time.time()

    allowed_extensions = (".jpg", ".jpeg", ".png")
    if not file.filename.lower().endswith(allowed_extensions):
        raise HTTPException(
            status_code=400,
            detail="Only image files are supported"
        )

    ext = os.path.splitext(file.filename)[1]

    uid = str(uuid.uuid4())
    original_path = os.path.join(UPLOAD_DIR, uid + ext)
    predicted_path = os.path.join(PREDICTED_DIR, uid + ext)

    with open(original_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    results = model(original_path, device="cpu", conf=CONFIDENCE_THRESHOLD)

    annotated_frame = results[0].plot()
    annotated_image = Image.fromarray(annotated_frame)
    annotated_image.save(predicted_path)

    prediction_session = models.PredictionSession(
        uid=uid,
        original_image=original_path,
        predicted_image=predicted_path,
    )
    db_session.add(prediction_session)

    detected_labels = []
    for box in results[0].boxes:
        label_idx = int(box.cls[0].item())
        label = model.names[label_idx]
        score = float(box.conf[0])
        bbox = box.xyxy[0].tolist()

        detection_object = models.DetectionObject(
            prediction_uid=uid,
            label=label,
            score=score,
            box=str(bbox),
        )
        prediction_session.detection_objects.append(detection_object)
        detected_labels.append(label)

    db_session.commit()
    processing_time = round(time.time() - start_time, 2)

    return {
        "prediction_uid": uid,
        "detection_count": len(results[0].boxes),
        "labels": detected_labels,
        "time_took": processing_time
    }











# 3. Get prediction by UID - return session details and detected objects
@app.get("/prediction/{uid}")
def get_prediction_by_uid(uid: str, db_session: Session = Depends(db.get_db)):
    """Get prediction session by uid with all detected objects"""
    prediction_session = (
        db_session.query(models.PredictionSession)
        .options(joinedload(models.PredictionSession.detection_objects))
        .filter_by(uid=uid)
        .first()
    )

    if not prediction_session:
        raise HTTPException(status_code=404, detail="Prediction not found")

    return {
        "uid": prediction_session.uid,
        "timestamp": prediction_session.timestamp.isoformat() if prediction_session.timestamp else None,
        "original_image": prediction_session.original_image,
        "predicted_image": prediction_session.predicted_image,
        "detection_objects": [
            {
                "id": obj.id,
                "label": obj.label,
                "score": obj.score,
                "box": obj.box,
            }
            for obj in prediction_session.detection_objects
        ]
    }


# 4. Get prediction image - return the annotated image file for a prediction
@app.get("/prediction/{uid}/image")
def get_prediction_image(uid: str, db_session: Session = Depends(db.get_db)):
    """Return the annotated (bounding-box) image for a prediction"""
    prediction_session = db_session.query(models.PredictionSession).filter_by(uid=uid).first()

    if not prediction_session or not os.path.exists(prediction_session.predicted_image):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(prediction_session.predicted_image)













# 5. Get predictions by score - list detection objects with score >= min_score
@app.get("/predictions/score/{min_score}")
def get_predictions_by_score(min_score: float, db_session: Session = Depends(db.get_db)):
    if min_score < 0.0 or min_score > 1.0:
        raise HTTPException(
            status_code=400,
            detail="min_score must be between 0.0 and 1.0"
        )

    objects = (
        db_session.query(models.DetectionObject)
        .filter(models.DetectionObject.score >= min_score)
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
def get_predictions_by_label(label: str, db_session: Session = Depends(db.get_db)):
    if not label.strip():
        raise HTTPException(status_code=400, detail="Label cannot be empty")

    predictions = (
        db_session.query(models.PredictionSession)
        .join(models.DetectionObject)
        .filter(models.DetectionObject.label == label)
        .options(joinedload(models.PredictionSession.detection_objects))
        .distinct(models.PredictionSession.uid)
        .all()
    )

    return [
        {
            "uid": prediction.uid,
            "timestamp": prediction.timestamp.isoformat() if prediction.timestamp else None,
            "detection_objects": [
                {
                    "id": obj.id,
                    "label": obj.label,
                    "score": obj.score,
                    "box": obj.box,
                }
                for obj in prediction.detection_objects
                if obj.label == label
            ]
        }
        for prediction in predictions
    ]











if __name__ == "__main__":# pragma: no cover
    import uvicorn

    init_db()
    
    uvicorn.run(app, host="0.0.0.0", port=8080)
