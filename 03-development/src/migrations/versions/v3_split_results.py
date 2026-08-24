"""v3_split_results: split tasks.result_json into task_results.

The data-moving revision: every ``tasks.result_json`` row is
copied into a new ``task_results`` table, and the
``tasks.result_json`` column is removed. The downgrade reverses the
copy so a round-trip preserves every sample row byte-for-byte
(AC-7.3).

[FR-07]
Citations:
  - FR-07 §3 v3: 含資料搬遷:把 ``tasks.result_json`` 拆為獨立的
    ``task_results`` 表,搬遷既有資料後移除原欄位.
  - FR-07 AC-7.3: Round-trip reversibility — ``upgrade head`` →
    write sample data → ``downgrade -1`` → ``upgrade head`` leaves
    every sample-data column byte-identical. v3 is where data loss
    is most likely; this revision uses an explicit
    ``INSERT ... SELECT`` copy and a matching downgrade copy so the
    round-trip is structural (not a destructive shortcut).
  - FR-07 AC-7.4: no raw-destructive-SQL shortcuts. The
    downgrade uses Alembic structural ops and an explicit reverse
    data move — structural ops only, not raw SQL shortcuts.
  - FR-07 AC-7.5: v3 MUST be covered by the offline SQL renderer.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "v3_split_results"
down_revision: Union[str, None] = "v2_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Forward copy: every non-null ``tasks.result_json`` becomes a row
# in ``task_results``. The WHERE clause preserves the v1 nullable
# semantics — nulls stay out of the new table, matching the
# reverse-move contract (downgrade copies non-null back into
# ``tasks.result_json``, leaving null tasks alone).
_COPY_RESULT_JSON_TO_TASK_RESULTS_SQL = (
    "INSERT INTO task_results (task_id, result_json) "
    "SELECT id, result_json FROM tasks WHERE result_json IS NOT NULL"
)

# Reverse copy: correlate ``task_results`` rows back onto
# ``tasks`` by ``task_id`` (the FR-07 narrative's join key). The
# ``EXISTS`` guard restricts the UPDATE to rows that actually have
# a matching ``task_results`` row so the reverse move is the
# structural inverse of the forward copy.
_RESTORE_RESULT_JSON_FROM_TASK_RESULTS_SQL = (
    "UPDATE tasks "
    "SET result_json = ("
    "  SELECT result_json FROM task_results "
    "  WHERE task_results.task_id = tasks.id"
    ") "
    "WHERE EXISTS ("
    "  SELECT 1 FROM task_results "
    "  WHERE task_results.task_id = tasks.id"
    ")"
)


def upgrade() -> None:
    """Move ``tasks.result_json`` into a new ``task_results`` table.

    [FR-07]
    Citations:
      - FR-07 §3 v3: the upgrade creates ``task_results``, copies
        every non-null ``tasks.result_json`` row into it, then drops
        the ``result_json`` column from ``tasks``. ``op.drop_column``
        is the Alembic structural op for the column removal — it
        works in both online and offline (AC-7.5 ``--sql``) modes
        because no table reflection is required for a single column
        drop.
      - FR-07 AC-7.3: the copy preserves every payload byte-for-byte
        (no transformation in the SELECT), so the downgrade can
        reverse the move exactly.
    """
    # Step 1: create the new ``task_results`` table.
    op.create_table(
        "task_results",
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("result_json", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
            name="fk_task_results_task_id",
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )

    # Step 2: copy every non-null ``tasks.result_json`` row into
    # ``task_results`` (see ``_COPY_RESULT_JSON_TO_TASK_RESULTS_SQL``).
    op.execute(_COPY_RESULT_JSON_TO_TASK_RESULTS_SQL)

    # Step 3: remove the ``result_json`` column from ``tasks``.
    # ``op.drop_column`` is the Alembic structural op — the
    # AC-7.5 offline SQL renderer emits a single column-removal
    # statement without needing to reflect the table.
    op.drop_column("tasks", "result_json")


def downgrade() -> None:
    """Reverse the v3 split — move ``task_results`` back into ``tasks.result_json``.

    [FR-07]
    Citations:
      - FR-07 §3 v3: 反向搬遷回 ``tasks.result_json`` 後 drop
        ``task_results`` — the downgrade MUST copy the data back
        BEFORE dropping ``task_results`` so no row is lost. The
        order is the AC-7.3 contract.
      - FR-07 AC-7.3: every column of every sample row survives the
        full ``upgrade → seed → downgrade -1 → upgrade`` cycle.
      - FR-07 AC-7.4: this downgrade uses ``op.add_column`` +
        ``op.drop_table`` — Alembic structural ops, not
        raw-destructive-SQL shortcuts.
    """
    # Step 1: re-add the ``result_json`` column on ``tasks``.
    # ``op.add_column`` is the Alembic structural op; the AC-7.5
    # offline SQL renderer emits a single column-addition
    # statement without needing to reflect the table.
    op.add_column(
        "tasks",
        sa.Column("result_json", sa.String(), nullable=True),
    )

    # Step 2: copy the ``task_results`` rows back into
    # ``tasks.result_json`` (see
    # ``_RESTORE_RESULT_JSON_FROM_TASK_RESULTS_SQL``). Null
    # ``result_json`` rows are preserved — they round-trip back to
    # null ``tasks.result_json``, matching the upgrade's
    # ``WHERE result_json IS NOT NULL`` selection.
    op.execute(_RESTORE_RESULT_JSON_FROM_TASK_RESULTS_SQL)

    # Step 3: drop ``task_results``. The data has been moved back
    # already, so the drop is destructive of structure only, not of
    # data — the FR-07 v3 downgrade contract is satisfied.
    op.drop_table("task_results")