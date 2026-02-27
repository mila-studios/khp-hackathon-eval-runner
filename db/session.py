from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


engine = None
SessionLocal: sessionmaker[Session] | None = None


def init_db(database_url: str | None = None) -> None:
    """Initialise the global engine and session factory.

    Called once at API startup (via FastAPI lifespan) or by DbStageReporter.
    """
    global engine, SessionLocal
    url = database_url or get_database_url()
    engine = create_engine(url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session per request."""
    if SessionLocal is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_session(database_url: str | None = None) -> Session:
    """Create a standalone session (used by DbStageReporter in background threads)."""
    url = database_url or get_database_url()
    eng = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    return factory()
