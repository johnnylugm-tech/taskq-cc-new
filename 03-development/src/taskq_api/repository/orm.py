"""SQLAlchemy ORM models for the taskq persistence layer — FR-06.

[FR-06]
Citations:
  - FR-06 §3 AC-6.4: ORM models declare explicit relationships so the
    eager-load strategy (``selectinload``) keeps the list endpoint
    statement count constant with respect to the row count (N+1 is a
    failure condition; NFR-01 performance).
  - NFR-06 (architecture_constraints): this is the only persistence
    module that imports SQLAlchemy above ``models/`` together with
    ``repository.session``. The ``api/`` and ``service/`` layers MUST
    NOT import ``sqlalchemy`` directly — the static-import lint gate
    ``lint-imports exit 0`` enforces the layering invariant.
"""

# pragma: no error-handling

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import (
    Mapped,
    declarative_base,
    mapped_column,
    relationship,
)


# ---------------------------------------------------------------------------
# Declarative base — single source of truth for ORM metadata.
# ``taskq_api.repository.session`` imports ``Base`` here so a single
# ``Base.metadata.create_all(engine)`` call provisions every persisted
# table without the session module having to know the schema details.
# ---------------------------------------------------------------------------
Base = declarative_base()


# ---------------------------------------------------------------------------
# ORM models — the three tables the FR-06 AC-6.4 eager-load test
# exercises. ``Task`` is the parent; ``Result`` and ``Tag`` are the
# two eager-loaded one-to-many relationships. Together they emit
# exactly 3 SQL statements per list query (parent SELECT + 2
# relationship SELECTs) regardless of how many rows the parent SELECT
# returns.
# ---------------------------------------------------------------------------
class Task(Base):  # type: ignore[misc, valid-type]
    """Persistent task row.

    [FR-06]
    Citations:
      - FR-06 §3 AC-6.4: ``results`` and ``tags`` are explicit
        one-to-many relationships loaded via ``selectinload`` in
        ``session.list_tasks_with_relationships``. Each relationship
        emits exactly one additional SELECT, so the total statement
        count is constant with respect to row count (parent SELECT +
        2 relationship SELECTs == 3 statements).
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


class Result(Base):  # type: ignore[misc, valid-type]
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


class Tag(Base):  # type: ignore[misc, valid-type]
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


__all__ = ["Base", "Task", "Result", "Tag"]
