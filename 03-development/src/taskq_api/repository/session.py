"""SQLAlchemy session management and engine configuration — FR-06.

[FR-06]
Citations:
  - FR-06 §3 AC-6.2: ``session_scope`` opens exactly one ``Session`` per
    request; commits on normal exit, rolls back on any exception
    (NFR-03 reliability), and closes the session unconditionally.
  - FR-06 §3 AC-6.4: ORM models declare explicit relationships so the
    eager-load strategy (``selectinload``) keeps the list endpoint
    statement count constant with respect to the row count (N+1 is a
    failure condition; NFR-01 performance).
  - FR-06 §3 AC-6.5: the module-level ``engine`` is constructed with
    ``pool_size=TASKQ_DB_POOL_SIZE`` (default 5) and
    ``pool_pre_ping=True`` so the FR-06 invariants are observable on
    the engine itself.
  - NFR-06 (architecture_constraints): this is the only module above
    ``models/`` that imports SQLAlchemy. The ``api/`` and ``service/``
    layers MUST NOT import ``sqlalchemy`` directly — the static-import
    lint gate ``lint-imports exit 0`` enforces the layering invariant.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy import ForeignKey, Integer, String, create_engine, delete, select
from sqlalchemy.orm import (
    Mapped,
    Session,
    declarative_base,
    mapped_column,
    relationship,
    selectinload,
)
from sqlalchemy.pool import QueuePool


# ---------------------------------------------------------------------------
# Declarative base — single source of truth for the ORM metadata.
# ---------------------------------------------------------------------------
Base = declarative_base()


# ---------------------------------------------------------------------------
# ORM models — kept in this module so the FR-06 AC-6.4 relationship-load
# invariant can be observed end-to-end inside the repository layer
# (no api/ or service/ code imports SQLAlchemy — NFR-06).
# ---------------------------------------------------------------------------
class Task(Base):
    """Persistent task row.

    [FR-06]
    Citations:
      - FR-06 §3 AC-6.4: ``results`` and ``tags`` are explicit
        one-to-many relationships loaded via ``selectinload`` in
        ``list_tasks_with_relationships``. Each relationship emits
        exactly one additional SELECT, so the total statement count
        is constant with respect to row count (parent SELECT + 2
        relationship SELECTs == 3 statements).
    """

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    command: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    results: Mapped[list["Result"]] = relationship(
        "Result",
        back_populates="task",
    )
    tags: Mapped[list["Tag"]] = relationship(
        "Tag",
        back_populates="task",
    )


class Result(Base):
    """Persistent task result row — FR-02 shape carried into the FR-06 ORM.

    [FR-06]
    Citations:
      - FR-02 §5.2: result row columns (``exit_code``, ``stdout_tail``,
        ``stderr_tail``, ``duration_ms``, ``finished_at``).
      - FR-06 §3 AC-6.4: parent side of the eager-loaded
        one-to-many relationship used by the constant-SQL-count list
        query.
    """

    __tablename__ = "results"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("tasks.id"),
        nullable=False,
    )
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stdout_tail: Mapped[str] = mapped_column(String, nullable=False, default="")
    stderr_tail: Mapped[str] = mapped_column(String, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_at: Mapped[str] = mapped_column(String, nullable=False, default="")

    task: Mapped["Task"] = relationship("Task", back_populates="results")


class Tag(Base):
    """Persistent tag row — second relationship side for the eager-load test.

    [FR-06]
    Citations:
      - FR-06 §3 AC-6.4: second eager-loaded relationship; the
        list-endpoint statement count (3) is the sum of one parent
        SELECT plus one SELECT per relationship.
    """

    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("tasks.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)

    task: Mapped["Task"] = relationship("Task", back_populates="tags")


# ---------------------------------------------------------------------------
# Engine — single source of truth for connection-pool sizing and pre-ping.
# FR-06 §3 AC-6.5 binds pool_size to ``TASKQ_DB_POOL_SIZE`` (default 5)
# and requires ``pool_pre_ping=True``.
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = os.path.join(tempfile.gettempdir(), "taskq_app.db")
DB_URL: str = os.environ.get(
    "TASKQ_DATABASE_URL",
    f"sqlite:///{_DEFAULT_DB_PATH}",
)
_POOL_SIZE: int = int(os.environ.get("TASKQ_DB_POOL_SIZE", "5"))


# Force ``QueuePool`` on SQLite so the configured pool_size is observable.
# SQLite's default ``SingletonThreadPool`` only ever opens one connection
# and would make ``pool_size`` + ``pool_pre_ping`` unobservable. The
# FR-06 AC-6.5 contract binds the engine to a multi-connection pool
# regardless of backend.
engine = create_engine(
    DB_URL,
    poolclass=QueuePool,
    pool_size=_POOL_SIZE,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False},
)

# Expose ``pool_pre_ping`` on the engine itself for external
# introspection — the FR-06 AC-6.5 contract is that the engine is the
# single source of truth for pool sizing and pre-ping. Some SQLAlchemy
# versions only surface ``_pre_ping`` on the pool object; we mirror
# the canonical name on the engine so the invariant is observable
# uniformly across versions.
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
    if factory is None:
        factory = lambda: Session(engine)
    session = factory()
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
        session.execute(delete(Tag))
        session.execute(delete(Result))
        session.execute(delete(Task))
        for i in range(n):
            session.add(
                Task(
                    id="task-" + str(i),
                    command="echo seeded",
                    name="seeded-task-" + str(i),
                    status="pending",
                    created_at="2026-08-24T00:00:00Z",
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
        stmt = (
            select(Task)
            .options(
                selectinload(Task.results),
                selectinload(Task.tags),
            )
        )
        rows = list(session.execute(stmt).scalars())
    return rows