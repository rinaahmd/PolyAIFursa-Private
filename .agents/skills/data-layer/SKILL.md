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
- When refactoring from raw SQL to SQLAlchemy, add regression tests for:
  - timestamp persistence and ISO formatting
  - nested relationship loading for `/prediction/{uid}`
  - filtering and join behavior for `/predictions/score/{min_score}` and `/predictions/label/{label}`
  - isolated database setup with temporary DB files or pytest fixtures
- Prefer fixtures such as `tmp_path` and `monkeypatch.setattr("app.db.DB_PATH", str(tmp_path / "test.db"))` so each test runs against a fresh database

## Adding tests when making changes

When adding new models, endpoints, or modifying existing behavior:

1. **Before refactoring** — Run existing tests to confirm baseline:
   ```bash
   cd services/yolo && pytest -q
   ```

2. **Model changes** — Add model tests in `services/yolo/tests/`:
   - Test that new columns are persisted and returned in API responses
   - Test relationship loading (e.g., `PredictionSession.detection_objects`)
   - Use SQLAlchemy queries or API endpoints to verify data, not raw SQL

3. **Endpoint changes** — Update or add API tests:
   - Assert response status code, JSON keys, and value types
   - Use `TestClient` from FastAPI to make test requests
   - Mock `app.model` and `app.Image` for YOLO inference to speed up tests

4. **Test isolation** — Each test should use a fresh database:
   ```python
   @pytest.fixture(autouse=True)
   def setup_db(tmp_path, monkeypatch):
       db_file = str(tmp_path / "test_predictions.db")
       monkeypatch.setattr("app.db.DB_PATH", db_file)
       from app import init_db
       init_db()
   ```

5. **After changes** — Verify all tests still pass:
   ```bash
   cd services/yolo && pytest -q
   ```

6. **Coverage** — Aim for 90%+ statement coverage in `app.py`; use the generated `htmlcov/` report to identify untested paths.

