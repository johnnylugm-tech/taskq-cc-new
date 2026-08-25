"""v2_tags: add tags + task_tags + tasks.name unique index.

Adds the many-to-many ``task_tags`` join table, the ``tags`` lookup
table, and a unique index on ``tasks.name`` so duplicate task names
are rejected at the DB layer. v2 does NOT modify ``tasks.result_json``
— that split is the v3 data migration.

[FR-07]
Citations:
  - FR-07 §3 v2: 新增 tags、task_tags(多對多)+ tasks.name 唯一索引.
    v2 must not touch v1 data (the existing ``tasks`` and
    ``api_keys`` rows survive the upgrade).
  - FR-07 AC-7.1: every table enumerated in
    ``expected_tables = "tasks,api_keys,tags,task_tags,task_results,
    rate_buckets,alembic_version"`` MUST be present after
    ``alembic upgrade head``. v2 owns the ``tags`` and ``task_tags``
    entries of that list.
  - FR-07 AC-7.4: v2's downgrade must drop the new artefacts
    using only Alembic's sanctioned structural ops. v2 uses
    ``op.drop_table`` / ``op.drop_index`` — structural ops, not
    raw-SQL shortcuts.
  - FR-07 AC-7.5: v2 MUST be covered by the offline SQL renderer.
"""
# Note: v2_tags is imported in-process by test_fr07.py's
# test_v3_upgrade_and_downgrade_executed_in_process (_build_v3_schema_and_seed),
# so pytest-cov DOES track this module.
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "v2_tags"
down_revision: Union[str, None] = "v1_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ``tags``, ``task_tags``, and the unique index on ``tasks.name``.

    [FR-07]
    Citations:
      - FR-07 §3 v2: v2 upgrade adds the tag-relation tables and the
        ``tasks.name`` unique index. The existing ``tasks`` rows are
        unchanged (additive migration, no destructive shortcut).
    """
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )
    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("tag_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_task_tags_task_id",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["tags.id"],
            name="fk_task_tags_tag_id",
        ),
        sa.PrimaryKeyConstraint("task_id", "tag_id"),
    )
    op.create_index(
        "uq_tasks_name",
        "tasks",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    """Reverse the v2 upgrade — drop the unique index and the new tables.

    [FR-07]
    Citations:
      - FR-07 §3 v2: v2 downgrade drops the new tables and index
        without affecting v1 data. The v1 ``tasks`` /
        ``api_keys`` / ``rate_buckets`` rows are untouched.
      - FR-07 AC-7.4: ``op.drop_index`` / ``op.drop_table`` are
        Alembic structural ops, not raw-destructive-SQL shortcuts
        — the AC-7.4 contract permits structural ops only.
    """
    op.drop_index("uq_tasks_name", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")