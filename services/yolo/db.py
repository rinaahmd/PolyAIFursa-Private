import os
from sqlalchemy import create_engine
from sqlalchemy.engine.url import URL
from sqlalchemy.orm import sessionmaker

DB_BACKEND = os.environ.get("DB_BACKEND", "sqlite").lower()
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST = os.environ.get("DB_HOST", "")
DB_NAME = os.environ.get("DB_NAME", "predictions.db")

engine = None
SessionLocal = None


def get_database_url(db_name: str | None = None) -> str:
    if DB_BACKEND == "postgres":
        return str(
            URL.create(
                drivername="postgresql+psycopg2",
                username=DB_USER or None,
                password=DB_PASSWORD or None,
                host=DB_HOST or None,
                database=db_name or DB_NAME,
            )
        )
    sqlite_name = db_name or DB_NAME
    return f"sqlite:///{sqlite_name}"


def configure_session(db_name: str | None = None):
    global engine, SessionLocal
    database_url = get_database_url(db_name)
    connect_args = {"check_same_thread": False} if DB_BACKEND != "postgres" else {}
    engine = create_engine(database_url, connect_args=connect_args, echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine


# Create the default session at import time.
configure_session()


def get_db():
    if SessionLocal is None:
        raise RuntimeError("Database session is not configured")

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
