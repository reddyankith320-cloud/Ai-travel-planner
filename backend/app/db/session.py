"""Database engine and session management.

Defaults to a local SQLite file so the app runs with no infrastructure at all;
point `DATABASE_URL` at PostgreSQL for anything real. Both are supported by the
same models.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from .models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: Settings) -> Engine:
    """Process-wide engine."""
    global _engine
    if _engine is None:
        url = settings.database_url
        kwargs: dict[str, object] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            # SQLite needs this to be used from FastAPI's threadpool.
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)
        _engine = create_engine(url, **kwargs)  # type: ignore[arg-type]
        logger.info("Database engine ready (%s)", url.split("://", 1)[0])
    return _engine


def get_session_factory(settings: Settings) -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(settings), autoflush=False, expire_on_commit=False
        )
    return _session_factory


def init_db(settings: Settings) -> None:
    """Create tables if they are missing.

    Fine for SQLite and for getting started on PostgreSQL. For a managed
    PostgreSQL deployment, run the Alembic migrations instead so schema
    changes are versioned -- see `backend/alembic/`.
    """
    Base.metadata.create_all(bind=get_engine(settings))
    logger.info("Database schema ensured")


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    """Transactional session: commit on success, roll back on error."""
    factory = get_session_factory(settings)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/session factory. Used by tests."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
