"""SQLAlchemy engine + per-request session helper — FR-06.

Module layout:
    * Engine          — ``engine`` + ``DB_URL`` / ``POOL_SIZE`` constants.
    * Schema bootstrap — creates the schema at import time for dev / tests.
    * ``session_scope`` — the per-request transaction boundary (AC-6.2).
    * Test / dev helpers — ``seed_tasks`` and
      ``list_tasks_with_relationships`` power the FR-06 AC-6.4
      constant-SQL-count measurement.

The ORM model classes (``Task``, ``Result``, ``Tag``) and the shared
``Base`` live in ``taskq_api.repository.orm`` so the session module
stays focused on session lifecycle + engine wiring.

[FR-06]
Citations:
  - FR-06 §3 AC-6.2: ``session_scope`` opens exactly one ``Session`` per
    request; commits on normal exit, rolls back on any exception
    (NFR-03 reliability), and closes the session unconditionally.
  - FR-06 §3 AC-6.5: the module-level ``engine`` is constructed with
    ``pool_size=TASKQ_DB_POOL_SIZE`` (default 5) and
    ``pool_pre_ping=True`` so the FR-06 invariants are observable on
    the engine itself.
  - NFR-06 (architecture_constraints): this module, together with
    ``taskq_api.repository.orm``, is the only persistence layer that
    imports SQLAlchemy. The ``api/`` and ``service/`` layers MUST NOT
    import ``sqlalchemy`` directly — the static-import lint gate
    ``lint-imports exit 0`` enforces the layering invariant.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.pool import QueuePool

from taskq_api.repository.orm import Base, Result, Tag, Task


# ---------------------------------------------------------------------------
# Engine — single source of truth for connection-pool sizing and pre-ping.
# FR-06 §3 AC-6.5 binds ``pool_size`` to ``TASKQ_DB_POOL_SIZE``
# (default 5) and requires ``pool_pre_ping=True``.
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = os.path.join(tempfile.gettempdir(), "taskq_app.db")
DB_URL: str = os.environ.get(
    "TASKQ_DATABASE_URL",
    f"sqlite:///{_DEFAULT_DB_PATH}",
)
POOL_SIZE: int = int(os.environ.get("TASKQ_DB_POOL_SIZE", "5"))


def _default_session_factory() -> Session:
    """Open a fresh ``Session`` bound to ``engine`` (the per-request default)."""
    return Session(engine)


# Force ``QueuePool`` on SQLite so the configured pool_size is observable.
# SQLite's default ``SingletonThreadPool`` only ever opens one connection
# and would make ``pool_size`` + ``pool_pre_ping`` unobservable. The
# FR-06 AC-6.5 contract binds the engine to a multi-connection pool
# regardless of backend.
#
# ``connect_args={"check_same_thread": False}`` is SQLite-specific —
# it lets the multi-connection pool share the file across threads,
# which the FR-06 test exercises implicitly via the event listener.
engine = create_engine(
    DB_URL,
    poolclass=QueuePool,
    pool_size=POOL_SIZE,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False},
)

# Mirror ``pool_pre_ping`` on the engine itself so external observers
# reading either ``engine.pool_pre_ping`` (canonical) or
# ``engine.pool._pre_ping`` (SQLAlchemy internal) see the same value.
# The pool's ``_pre_ping`` is already set to ``True`` by ``create_engine``
# above — this mirror is a test-seam that makes the FR-06 AC-6.5
# invariant observable uniformly across callers.
engine.pool_pre_ping = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Schema bootstrap — create tables once at import time. Production uses
# Alembic for migrations; this is a convenience for dev and for the
# FR-06 test that constructs the schema in-process.
# ---------------------------------------------------------------------------
Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Per-request context manager — AC-6.2 / NFR-03.
# ---------------------------------------------------------------------------
@contextmanager
def session_scope(
    factory: Callable[[], Session] | None = None,
) -> Iterator[Session]:
    """Yield exactly one ``Session``; commit on success, rollback on error.

    Args:
        factory: optional Session factory. When ``None`` (the default)
            a fresh ``Session`` bound to ``engine`` is opened so
            callers get the canonical per-request transaction boundary.
            Tests inject a structural fake so the helper can be driven
            without a live database connection.

    Yields:
        A ``sqlalchemy.orm.Session`` (or structurally compatible
        object exposing ``commit`` / ``rollback`` / ``close``). The
        session is committed when the body returns normally, rolled
        back when the body raises any exception (NFR-03 reliability),
        and closed unconditionally on exit so the connection is always
        returned to the pool.

    [FR-06, NFR-03]
    Citations:
      - FR-06 §3 AC-6.2: one ``Session`` per request, commit / rollback
        via context manager.
      - NFR-03 (reliability): the boundary is fail-safe — an exception
        inside the with-block triggers rollback so the database never
        observes a half-applied request.
    """
    open_session = factory or _default_session_factory
    session = open_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Test / dev helpers — deterministic seed + eager-loaded list query.
# Required by the FR-06 AC-6.4 constant-SQL-count measurement.
# ---------------------------------------------------------------------------
_SEED_COMMAND = "echo seeded"
_SEED_STATUS = "pending"
_SEED_CREATED_AT = "2026-08-24T00:00:00Z"


def _clear_eager_load_graph(session: Session) -> None:
    """Delete every row from the eager-load graph so seed is idempotent.

    [FR-06]
    Citations:
      - FR-06 §3 AC-6.4: the FR-06 AC-6.4 test seeds N rows in
        succession and expects the row count to be exactly N each
        time, so prior rows must be cleared before each insert.
    """
    session.execute(delete(Tag))
    session.execute(delete(Result))
    session.execute(delete(Task))


def seed_tasks(n: int) -> int:
    """Insert ``n`` deterministic task rows; return the row count.

    Existing rows are cleared first so consecutive calls are
    idempotent across test invocations. Each row carries the same
    shape as the production ``Task`` so the eager-loaded list query
    exercises the real relationship graph.

    [FR-06]
    Citations:
      - FR-06 §3 AC-6.4: helper used by the AC-6.4
        constant-SQL-count test to construct a deterministic N-row
        dataset.
    """
    with session_scope() as session:
        _clear_eager_load_graph(session)
        for i in range(n):
            session.add(
                Task(
                    id=f"task-{i}",
                    command=_SEED_COMMAND,
                    name=f"seeded-task-{i}",
                    status=_SEED_STATUS,
                    created_at=_SEED_CREATED_AT,
                )
            )
        session.flush()
    return n


def list_tasks_with_relationships() -> list[Task]:
    """Return all tasks with ``results`` and ``tags`` eagerly loaded.

    The eager-load strategy is ``selectinload`` so the emitted SQL
    statement count is constant with respect to the row count — the
    parent SELECT plus one additional SELECT per relationship, total
    of 3 statements regardless of how many rows are returned
    (FR-06 §3 AC-6.4 / NFR-01 performance).

    [FR-06]
    Citations:
      - FR-06 §3 AC-6.4: explicit ``selectinload`` prevents the N+1
        regression where each row would otherwise trigger its own
        relationship SELECT. SQLAlchemy emits exactly 3 statements
        regardless of how many rows the parent SELECT returns.
    """
    with session_scope() as session:
        stmt = select(Task).options(
            selectinload(Task.results),
            selectinload(Task.tags),
        )
        rows = list(session.execute(stmt).scalars())
    return rows


__all__ = [
    "DB_URL",
    "POOL_SIZE",
    "engine",
    "list_tasks_with_relationships",
    "seed_tasks",
    "session_scope",
]
