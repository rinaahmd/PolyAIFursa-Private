# 12 - Database

## Backend
- SQLAlchemy ORM in yolo service.
- default sqlite, optional postgres.

## Model relationship
- PredictionSession (one) -> DetectionObject (many).

## Write flow
1. /predict receives image key and prediction id.
2. yolo downloads image from S3.
3. yolo inference runs.
4. annotated image uploaded to S3.
5. prediction and detections committed to DB.

## Read flow
- /prediction/{uid}
- /predictions/score/{min_score}
- /predictions/label/{label}
