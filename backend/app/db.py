"""SQLAlchemy engine + session factory for the SQLite catalog."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

settings.ensure_dirs()

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _migrate() -> None:
    """Idempotent, dependency-free column adds for an existing SQLite catalog.

    ``create_all`` adds new *tables* but never new *columns* on existing ones. SQLite supports
    ``ALTER TABLE ... ADD COLUMN``; we guard each with a PRAGMA check so it's safe every startup.
    """
    adds = {
        "place_clusters": {"manual": "INTEGER DEFAULT 0", "reviewed": "INTEGER DEFAULT 0"},
        "media": {
            "source": "TEXT DEFAULT 'takeout'",
            "thumbnail_sha256": "TEXT DEFAULT NULL",
            "clip_embedding": "BLOB DEFAULT NULL",
            "face_area_ratio": "REAL DEFAULT NULL",
            "archived_at": "TIMESTAMP DEFAULT NULL",
            "archived_from": "TEXT DEFAULT NULL",
        },
        "gp_operations": {"args": "TEXT DEFAULT NULL"},
        "dup_groups": {"keeper_reason": "TEXT DEFAULT NULL"},
    }
    with engine.begin() as conn:
        for table, cols in adds.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for col, decl in cols.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))


def init_db() -> None:
    """Create all tables, then run lightweight column migrations. Safe to call repeatedly."""
    from . import models  # noqa: F401 — register mappers

    models.Base.metadata.create_all(engine)
    _migrate()


def get_session() -> Session:
    return SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for pipeline code."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# FastAPI dependency
def db_dependency() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


# Run one backend worker: review/queue state transitions must be indivisible across HTTP threads.
from functools import wraps
from threading import RLock
mutation_lock = RLock()
def serialized(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with mutation_lock:
            return fn(*args, **kwargs)
    return wrapped
