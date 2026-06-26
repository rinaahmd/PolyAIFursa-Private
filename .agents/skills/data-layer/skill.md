---
name: yolo-api-data-layer
description: Use this skill when the YOLO FastAPI service requires SQLAlchemy database refactors, model changes, or backend configuration updates while preserving existing API behavior.
---

# YOLO API Data Layer Skill

Use this skill for database and model changes in the YOLO service that must switch from raw SQLite to SQLAlchemy or extend the SQLAlchemy data model.



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
- Configure database backend with environment variables: `DB_BACKEND`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_NAME`
- Default to SQLite `sqlite:///./predictions.db`; use PostgreSQL URL only when `DB_BACKEND == "postgres"`
- Apply `connect_args={"check_same_thread": False}` only for SQLite
- Update `services/yolo/requirements.txt` when new database libraries are needed:
  - `sqlalchemy>=2.0.0` for the ORM
  - `psycopg2-binary>=2.9.0` when adding or validating PostgreSQL backend support

## Testing guidance

- Keep tests black-box and assert API response status and JSON shape
- Do not use raw SQLite queries in tests
- If tests inspect stored rows, prefer SQLAlchemy models from `services.yolo.models` or API endpoints
- Add coverage for any new endpoints or model changes

### Regression tests for refactors

When refactoring from raw SQL to SQLAlchemy:

1. **Timestamp persistence** — Verify timestamps are server-side generated and stored:
   ```python
   def test_get_prediction_timestamp_is_persisted(client):
       uid = create_prediction_with_mock(client)
       response = client.get(f"/prediction/{uid}")
       assert response.status_code == 200
       data = response.json()
       assert data["timestamp"] is not None
       datetime.fromisoformat(data["timestamp"])  # Validate ISO format
   ```

2. **Relationship loading** — Confirm nested objects are fully hydrated:
   ```python
   response = client.get(f"/prediction/{uid}")
   data = response.json()
   assert "detection_objects" in data
   assert len(data["detection_objects"]) > 0
   assert all("label" in obj for obj in data["detection_objects"])
   ```

3. **Database session cleanup** — Run tests in isolation with temp DB files or fixtures
   - Use `monkeypatch.setattr("app.DB_PATH", str(tmp_path / "test.db"))`
   - Call `init_db()` in `setUp()` or pytest fixture with `autouse=True`

4. **Query correctness** — Test filter, order, and join logic:
   - `/predictions/score/{min_score}` returns only objects with `score >= min_score`
   - `/predictions/label/{label}` joins PredictionSession to DetectionObject correctly
   - Duplicate predictions are avoided when multiple objects have the same label

