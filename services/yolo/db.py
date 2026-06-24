import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").lower()
DB_PATH = os.environ.get("DB_PATH", "predictions.db")
DB_USER = os.environ.get("DB_USER", "postgres")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_NAME = os.environ.get("DB_NAME", "predictions")


def _get_sqlite_url() -> str:
    return f"sqlite:///{DB_PATH}"


def _get_postgres_url() -> str:
    if not DB_USER or DB_PASSWORD is None:
        raise RuntimeError("DB_USER and DB_PASSWORD must be set for Postgres backend")

    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"


def get_database_url() -> str:
    if DB_BACKEND == "postgres":
        return _get_postgres_url()
    return _get_sqlite_url()


def _create_engine():
    connect_args = {"check_same_thread": False} if DB_BACKEND == "sqlite" else {}
    return create_engine(get_database_url(), connect_args=connect_args, future=True)


engine = _create_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


def init_db(db_path: str | None = None):
    global DB_PATH, engine, SessionLocal

    if db_path is not None:
        DB_PATH = db_path

    engine = _create_engine()
    SessionLocal.configure(bind=engine)

    import models
    models.Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
