"""Task repository — module-level singleton used by FR-01 handlers.

The `task_repo` attribute on this module is the singleton FR-01 handlers
talk to. It exposes:

    create(payload)            -> row
    get(task_id)               -> row | None
    list(status, cursor, limit) -> (items, next_cursor)
    delete_with_results(id)    -> count

FR-01 tests swap this attribute for an in-memory fake; production wires
it up to the SQLite-backed implementation in the same shape.

[FR-01, FR-06]
Citations:
  - FR-01: handlers import this module and read `.task_repo` at request time.
  - FR-06: the real SQLite/SQLAlchemy implementation lives elsewhere.
"""
from __future__ import annotations

from typing import Any


class _InMemoryTaskRepo:
    """In-process fallback. Replaced by a SQLite-backed class in FR-06.

    Kept here so `task_repo` is always defined — handlers can import the
    module before FR-06 has wired the production implementation.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        import uuid
        name = payload["name"]
        if any(r["name"] == name for r in self.rows.values()):
            raise ValueError("name already exists")
        if not payload.get("command") or not name:
            raise ValueError("empty field")
        row = {
            "id": str(uuid.uuid4()),
            "command": payload["command"],
            "name": name,
            "status": "pending",
            "created_at": "2026-08-24T00:00:00Z",
        }
        self.rows[row["id"]] = row
        return row

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.rows.get(task_id)

    def list(
        self,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], str | None]:
        all_ids = sorted(self.rows.keys())
        start = int(cursor) if cursor else 0
        end = min(start + limit, len(all_ids))
        page_ids = all_ids[start:end]
        next_cursor = str(end) if end < len(all_ids) else None
        return [self.rows[i] for i in page_ids], next_cursor

    def delete_with_results(self, task_id: str) -> int:
        count = 0
        if task_id in self.rows:
            count -= 1
            del self.rows[task_id]
        for k in list(self.results.keys()):
            if self.results[k].get("task_id") == task_id:
                del self.results[k]
                count += 1
        return count

    # ------------------------------------------------------------------
    # FR-02 surface — state-machine persistence and run-history queries.
    # ------------------------------------------------------------------
    def update_status(self, task_id: str, status: str) -> None:
        """Set ``rows[task_id]["status"] = status`` (no-op when absent)."""
        row = self.rows.get(task_id)
        if row is not None:
            row["status"] = status

    def write_result(self, **fields: Any) -> dict[str, Any]:
        """Append a row to ``results`` and return it (test seam for FR-02)."""
        import uuid

        result_id = str(uuid.uuid4())
        row: dict[str, Any] = {"id": result_id}
        row.update(fields)
        # Key under the supplied run_id when present, else under result_id,
        # so `delete_with_results` can still sweep results by task_id.
        key = str(row.get("run_id") or result_id)
        self.results[key] = row
        return row

    def list_runs(
        self,
        task_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return rows whose ``task_id`` matches, newest-to-oldest.

        Ordering is by ``finished_at`` descending when present, falling back
        to insertion order so FR-02 tests get a deterministic newest-first
        view of the in-memory store.
        """
        matching = [
            row for row in self.results.values() if row.get("task_id") == task_id
        ]
        matching.sort(
            key=lambda r: str(r.get("finished_at") or ""),
            reverse=True,
        )
        return matching[:limit]


# Singleton — tests assign `task_repo_mod.task_repo = fake_repo` to swap it.
task_repo = _InMemoryTaskRepo()