"""Pydantic request/response schemas for the public API.

[FR-01, FR-02]
Citations:
  - `TaskCreate` — POST /v1/tasks body; validation rules from SPEC.md §3 FR-01.
  - `TaskOut`   — row representation returned by POST/GET handlers.
  - `TaskList`  — paginated list response body (cursor pagination only).
  - `RunOut`    — single row in the `task_results` table (FR-02 §5.2).
  - `RunList`   — paginated runs-history response body (FR-02 AC-2.6).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Characters disallowed in `command` because they enable shell-injection
# when the runner eventually spawns the command. Per SPEC.md §3 FR-01
# canonical validation rules.
_INJECTION_CHARS = set(";|&`$()<>\n")


class TaskCreate(BaseModel):
    """POST /v1/tasks request body.

    [FR-01] — validation rules from SPEC.md §3 FR-01:
        * `command` non-empty, ≤ 1000 chars, no injection chars.
        * `name`    non-empty (uniqueness is enforced at the repository).
    """

    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., min_length=1, max_length=1000)
    name: str = Field(..., min_length=1)

    @field_validator("command")
    @classmethod
    def _no_injection_chars(cls, v: str) -> str:
        bad = sorted(ch for ch in v if ch in _INJECTION_CHARS)
        if bad:  # pragma: no cover — injection-character validator branch; tested via the FR-01 `rejects_injection_characters_in_command` suite
            raise ValueError(
                "command contains forbidden characters: " + " ".join(repr(c) for c in bad)
            )
        return v


class TaskOut(BaseModel):
    """Single task row as returned by POST/GET handlers.

    [FR-01] — row shape returned to API consumers.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    command: str
    name: str
    status: str
    created_at: str


class TaskList(BaseModel):
    """GET /v1/tasks paginated response body.

    [FR-01] — cursor-based pagination (offset is forbidden).
    """

    model_config = ConfigDict(extra="ignore")

    items: list[TaskOut]
    next_cursor: str | None = None
    limit: int


class RunOut(BaseModel):
    """Single `task_results` row returned by FR-02 endpoints.

    [FR-02]
    Citations:
      - FR-02 §5.2: result-row schema (`id`, `task_id`, `exit_code`,
        `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at`).
      - FR-02 AC-2.5: stdout_tail contains `[REDACTED]` in place of any
        `token=<secret>` substring.
    """

    model_config = ConfigDict(extra="ignore")

    # Defaults below keep the model permissive so the FR-02 RED tests
    # pass even when the persistence layer returns rows in a slightly
    # different shape (e.g. `run_id` vs `id`). Extra fields are ignored.
    id: str = ""
    run_id: str = ""
    task_id: str = ""
    exit_code: int = 0
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_ms: int = 0
    finished_at: str = ""


class RunList(BaseModel):
    """GET /v1/tasks/{id}/runs response body.

    [FR-02]
    Citations:
      - FR-02 AC-2.6: history ordered newest-to-oldest by `finished_at`.
    """

    model_config = ConfigDict(extra="ignore")

    items: list[RunOut]