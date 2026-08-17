"""Database setup.

Uses SQLite by default. Override the location with the PODFINDER_DB
environment variable (useful for tests or deploying somewhere else).
"""

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "podfinder.db"
DB_URL = os.environ.get("PODFINDER_DB", f"sqlite:///{DEFAULT_DB_PATH}")

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    from app import models  # noqa: F401  (registers the models with Base)

    Base.metadata.create_all(engine)


def get_session():
    """FastAPI dependency that yields a database session."""
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
