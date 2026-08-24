"""Tasks REST API router — FR-01.

[FR-01]
Citations:
  - FR-01 §3 (SPEC.md): Task resource CRUD API contract:
      POST   /v1/tasks         create_task(body)   -> TaskOut | 422
      GET    /v1/tasks/{id}    read_task(id)      -> TaskOut | 404
      GET    /v1/tasks         list_tasks(...)    -> {items, next_cursor, limit} | 422
      DELETE /v1/tasks/{id}    delete_task(id)    -> 204
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

import taskq_api.repository.task_repo as task_repo_mod
from taskq_api.api import deps
from taskq_api.models.schemas import TaskCreate, TaskList, TaskOut


# Router under `/v1/tasks` per FR-01. The app includes it without a
# further prefix so route paths stay short and grep-friendly.
router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


# Scope dependency callables — wrapped as `def` so the test fixture's override
# of `deps.require_scope` is resolved at request time, not at module-load time.
def _require_write() -> dict:
    return deps.require_scope("write")


def _require_read() -> dict:
    return deps.require_scope("read")


def _require_admin() -> dict:
    return deps.require_scope("admin")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskOut,
)
def create_task(
    body: TaskCreate,
    _principal: dict = Depends(_require_write),
) -> TaskOut:
    """Create a task. AC-1.1 / AC-1.2.

    [FR-01]
    Citations:
      - FR-01: POST /v1/tasks handler.
      - FR-06: delegates to `task_repo.task_repo.create`.
    """
    try:
        row = task_repo_mod.task_repo.create(
            {"command": body.command, "name": body.name},
        )
    except ValueError as exc:
        # Business-rule violation surfaced by the repo layer (e.g. duplicate
        # name). Map to 422 + problem+json so the client sees a single
        # error shape for validation failures.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return TaskOut(**row)


@router.get(
    "/{task_id}",
    response_model=TaskOut,
)
def read_task(
    task_id: UUID,
    _principal: dict = Depends(_require_read),
) -> TaskOut:
    """Fetch a single task by id. AC-1.3.

    [FR-01]
    Citations:
      - FR-01: GET /v1/tasks/{id} handler.
      - FR-06: delegates to `task_repo.task_repo.get`.
    """
    row = task_repo_mod.task_repo.get(str(task_id))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"task {task_id} not found",
        )
    return TaskOut(**row)


@router.get(
    "",
    response_model=TaskList,
)
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _principal: dict = Depends(_require_read),
) -> TaskList:
    """Cursor-paginated list. AC-1.4 / AC-1.5.

    [FR-01]
    Citations:
      - FR-01: cursor-based pagination only (no offset parameter);
        default `limit=50`, upper bound `200`, exceeded -> 422.
      - FR-06: delegates to `task_repo.task_repo.list`.
    """
    items, next_cursor = task_repo_mod.task_repo.list(
        status=status_filter,
        cursor=cursor,
        limit=limit,
    )
    return TaskList(
        items=[TaskOut(**row) for row in items],
        next_cursor=next_cursor,
        limit=limit,
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_task(
    task_id: UUID,
    _principal: dict = Depends(_require_admin),
) -> Response:
    """Delete a task and its result rows in one tx. AC-1.6.

    [FR-01]
    Citations:
      - FR-01: DELETE /v1/tasks/{id} handler — task + results in single tx.
      - FR-06: delegates to `task_repo.task_repo.delete_with_results`.
    """
    task_repo_mod.task_repo.delete_with_results(str(task_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
