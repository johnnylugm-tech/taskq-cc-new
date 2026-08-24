"""Pydantic request/response schemas for the public API.

[FR-01]
Citations:
  - `TaskCreate` — POST /v1/tasks body; validation rules from SPEC.md §3 FR-01.
  - `TaskOut`   — row representation returned by POST/GET handlers.
  - `TaskList`  — paginated list response body (cursor pagination only).
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
        if bad:
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