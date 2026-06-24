---
name: yolo-api-tests
description: Use this skill when writing or updating HTTP API tests for the YOLO FastAPI service, especially tests for /predict, /health, /prediction/{uid}, /prediction/{uid}/image, /predictions/label/{label}, /predictions/score/{min_score}, and database-backed model changes.
---

# YOLO API Testing Skill

When writing tests for the YOLO service, always verify behavior at the HTTP API level using FastAPI `TestClient`.

## Testing framework

Use pytest.

Test files must start with:

```text
test_
```

## Database model guidance

- When the data layer is refactored to SQLAlchemy, keep tests black-box and assert the API response shape and status codes.
- Do not rely on raw SQLite queries in tests.
- If you need to inspect stored rows, prefer importing SQLAlchemy models from `services.yolo.models` or using the API endpoints directly.
- Ensure tests still cover endpoints after model changes, including `/predict`, `/health`, `/prediction/{uid}`, `/prediction/{uid}/image`, `/predictions/label/{label}`, `/predictions/score/{min_score}`, and any newly requested endpoint such as `/predictions/recent`.

