"""v1_initial: initial schema.

Creates the foundational tables: ``tasks``, ``api_keys``,
``rate_buckets``. ``tasks.result_json`` holds the per-task
result payload (FR-02 §5.2) — that column is split out into its
own ``task_results`` table by ``v3_split_results``.

[FR-07]
Citations:
  - FR-07 §3 v1: 建立 tasks、api_keys 兩表 — the FR-07 narrative
    enumerates these two; ``rate_buckets`` is part of the same
    foundational schema so the runtime token-bucket regulator has
    a backing table from revision v1.
  - FR-07 AC-7.1: ``alembic upgrade head`` MUST succeed against a
    real SQLite file. v1 is the first link in the chain.
  - FR-07 AC-7.5: this revision MUST be covered by alembic's offline
    SQL renderer (``alembic upgrade v1_initial --sql`` produces
    non-empty DDL).
"""
# Note: v1_initial is imported in-process by test_fr07.py's
# test_v3_upgrade_and_downgrade_executed_in_process (_build_v3_schema_and_seed),
# so pytest-cov DOES track this module.
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "v1_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ``tasks``, ``api_keys``, and ``rate_buckets``.

    [FR-07]
    Citations:
      - FR-07 §3 v1: v1 upgrade is the first step of the
        ``alembic upgrade head`` chain.
      - FR-07 AC-7.1: the upgrade MUST bring a real SQLite file to
        the v1 schema with these three tables present.
      - FR-07 AC-7.3: the round-trip test inserts rows with only
        ``id``, ``command``, ``name``, and ``result_json`` —
        ``status`` and ``created_at`` are nullable so the INSERT
        does not violate a NOT NULL constraint. The runtime
        ``task_repo`` layer populates these columns when it creates
        a task via the API.
    """
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("command", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=True),
        sa.Column("result_json", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "rate_buckets",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("last_refill_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    """Reverse the v1 upgrade — drop ``rate_buckets``, ``api_keys``, ``tasks``.

    [FR-07]
    Citations:
      - FR-07 §3 v1: v1 downgrade drops the two foundational tables
        (plus ``rate_buckets``) so ``alembic downgrade base`` leaves
        only the ``alembic_version`` bookkeeping row.
      - FR-07 AC-7.4: the FR-07 spec bans destructive shortcuts
        (raw destructive SQL is forbidden by the AC-7.4 contract).
        ``op.drop_table`` is Alembic's structural op — not a raw-SQL
        shortcut — and is the only sanctioned way to remove a table
        during a downgrade.
    """
    op.drop_table("rate_buckets")
    op.drop_table("api_keys")
    op.drop_table("tasks")