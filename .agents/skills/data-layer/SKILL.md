---
name: yolo-api-data-layer
description: Use this skill when the YOLO FastAPI service requires SQLAlchemy database refactors, model changes, or backend configuration updates while preserving existing API behavior.
---

# YOLO API Data Layer Skill

Use this skill for database and model changes in the YOLO service that must switch from raw SQLite to SQLAlchemy or extend the SQLAlchemy data model.

## When to use

- "refactor the api to use sqlalchemy"
- "add an endpoint GET /predictions/recent that returns the 10 most recent sessions"
- "add a UserFeedback table to track user ratings per prediction"
- "write tests for the /predict endpoint"
- "the database layer doesn't follow our architectural design, fix it"
- "delete a prediction session and all its detection objects by uid"
- "add a column processing_time_ms to the prediction_sessions table"
- "make the database backend configurable so we can use postgres in production"

## What this skill must produce

- `services/yolo/models.py` containing SQLAlchemy models with `declarative_base()`
- `services/yolo/db.py` containing `create_engine`, `SessionLocal`, and `get_db()`
- `services/yolo/app.py` updated to use `Depends(get_db)` and SQLAlchemy ORM queries
- No raw SQL strings, no `sqlite3` imports, no manual `conn.execute(...)`, and no obsolete `init_db()` logic
- Existing endpoints, status codes, and response shapes must remain unchanged
- New database behavior should be covered by pytest tests at the API level

## SQLAlchemy expectations

- Use model classes for tables: `PredictionSession` and `DetectionObject`
- Map `PredictionSession` to `prediction_sessions` and `DetectionObject` to `detection_objects`
- Use `db.add(...)`, `db.commit()`, `db.query(...).filter_by(...)`, `.order_by(...)`, `.limit(...)`, and SQLAlchemy deletion semantics
- Configure database backend with environment variables: `DB_BACKEND`, `DB_USER`, `DB_PASSWORD`
- Default to SQLite `sqlite:///./predictions.db`; use PostgreSQL URL only when `DB_BACKEND == "postgres"`
- Apply `connect_args={"check_same_thread": False}` only for SQLite

## Testing guidance

- Keep tests black-box and assert API response status and JSON shape
- Do not use raw SQLite queries in tests
- If tests inspect stored rows, prefer SQLAlchemy models from `services.yolo.models` or API endpoints
- Add coverage for any new endpoints or model changes

