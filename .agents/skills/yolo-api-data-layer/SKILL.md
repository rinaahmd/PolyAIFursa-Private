---

name: yolo-api-data-layer
description: Use this skill for YOLO FastAPI service tasks that modify, refactor, test, or extend the API database layer, including SQLAlchemy refactors, database configuration, models, queries, new tables, new columns, database-backed endpoints, and related tests.
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# YOLO API Data Layer Skill

Use this skill when working on the YOLO service database layer.

Examples:

* refactor the api to use sqlalchemy
* add an endpoint GET /predictions/recent
* add a UserFeedback table
* write tests for the /predict endpoint
* fix the database architecture
* delete a prediction session by uid
* add a processing_time_ms column
* make the database backend configurable

## Goal

Refactor and maintain the database layer using SQLAlchemy.

Support:

* SQLite for development
* PostgreSQL for production

Keep database logic separate from API logic.

## Rules

### Rule 1 - Preserve API behavior

Do not change existing:

* endpoints
* status codes
* response shapes
* response field names

unless explicitly requested.

Existing endpoints:

* GET /health
* POST /predict
* GET /prediction/{uid}
* GET /prediction/{uid}/image
* GET /predictions/label/{label}
* GET /predictions/score/{min_score}

### Rule 2 - Prefer SQLAlchemy

Use:

```python
db.add(...)
db.commit(...)
db.query(...)
```

Avoid:

```python
sqlite3
cursor.execute(...)
conn.execute(...)
```

### Rule 3 - Keep responsibilities separated

Preferred structure:

```text
services/yolo/
├── app.py
├── db.py
├── models.py
└── tests/
```

* app.py → API routes
* db.py → database configuration and sessions
* models.py → SQLAlchemy models
* tests/ → API tests

### Rule 4 - Database configuration

Support environment-based configuration.

Examples:

```text
DATABASE_URL
DB_BACKEND
DB_USER
DB_PASSWORD
DB_HOST
DB_PORT
DB_NAME
```

### Rule 5 - New features

When requested:

* Add new SQLAlchemy models.
* Add new columns.
* Add new endpoints.
* Add tests for all new functionality.

Examples:

* GET /predictions/recent
* UserFeedback table
* processing_time_ms column

### Rule 6 - Testing

Use:

* pytest
* FastAPI TestClient

Prefer testing through HTTP endpoints.

If the project contains a `yolo-api-tests` skill, update it to use SQLAlchemy-compatible testing patterns.

### Rule 7 - Verification

Before completing work:

* Run tests.
* Verify existing endpoints still work.
* Verify response shapes are unchanged.
* Verify SQLite and PostgreSQL configurations work.
* Verify new functionality includes tests.
