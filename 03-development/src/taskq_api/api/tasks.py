"""Tasks REST API router — FR-01 + FR-02.

[FR-01, FR-02]
Citations:
  - FR-01 §3 (SPEC.md): Task resource CRUD API contract:
      POST   /v1/tasks         create_task(body)   -> TaskOut | 422
      GET    /v1/tasks/{id}    read_task(id)      -> TaskOut | 404
      GET    /v1/tasks         list_tasks(...)    -> {items, next_cursor, limit} | 422
      DELETE /v1/tasks/{id}    delete_task(id)    -> 204
  - FR-02 §3 (SPEC.md): Task execution API contract:
      POST   /v1/tasks/{id}/run run_task(id)       -> 202 {run_id} | 404
      GET    /v1/tasks/{id}/runs list_runs(id)     -> 200 {items: [RunOut,...]} newest->oldest
"""
from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status

import taskq_api.repository.task_repo as task_repo_mod
from taskq_api.api import deps
from taskq_api.models.schemas import RunList, RunOut, TaskCreate, TaskList, TaskOut
from taskq_api.service.runner import run_task as _run_subprocess


# Router under `/v1/tasks` per FR-01. The app includes it without a
# further prefix so route paths stay short and grep-friendly.
router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


# Scope dependency callables — bind the scope string at handler decoration
# time so FastAPI resolves the inner `Depends(require_api_key)` recursively.
# The returned object is a `_ScopeDep` (a FastAPI `Depends`-shaped wrapper
# that also exposes `.callable`) — it can be passed directly to the
# router's `dependencies=[...]` argument (no outer `Depends(...)`)
# because `_ScopeDep` already carries the `dependency` attribute
# FastAPI's resolver reads.
_require_write = deps.require_scope("write")
_require_read = deps.require_scope("read")
_require_admin = deps.require_scope("admin")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskOut,
    dependencies=[_require_write],
)
def create_task(
    body: TaskCreate,
) -> TaskOut:
    """Create a task. AC-1.1 / AC-1.2.

    [FR-01]
    Citations:
      - FR-01: POST /v1/tasks handler.
      - FR-04: ``dependencies=[_require_write]`` enforces the single
        authz decision point (SPEC.md §6 / FR-04 AC-4.3).
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
    dependencies=[_require_read],
)
def read_task(
    task_id: UUID,
) -> TaskOut:
    """Fetch a single task by id. AC-1.3.

    [FR-01]
    Citations:
      - FR-01: GET /v1/tasks/{id} handler.
      - FR-04: read scope via ``dependencies=[_require_read]``.
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
    dependencies=[_require_read],
)
def list_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> TaskList:
    """Cursor-paginated list. AC-1.4 / AC-1.5.

    [FR-01]
    Citations:
      - FR-01: cursor-based pagination only (no offset parameter);
        default `limit=50`, upper bound `200`, exceeded -> 422.
      - FR-04: read scope via ``dependencies=[_require_read]``.
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
    dependencies=[_require_admin],
)
def delete_task(
    task_id: UUID,
) -> Response:
    """Delete a task and its result rows in one tx. AC-1.6.

    [FR-01]
    Citations:
      - FR-01: DELETE /v1/tasks/{id} handler — task + results in single tx.
      - FR-04: admin scope via ``dependencies=[_require_admin]``.
      - FR-06: delegates to `task_repo.task_repo.delete_with_results`.
    """
    task_repo_mod.task_repo.delete_with_results(str(task_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ===========================================================================
# FR-02 — Task execution endpoints
# ===========================================================================


async def _execute_and_record(task_id: str, command: str, run_id: str) -> None:
    """Background-task shim: spawn subprocess, persist result row.

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.5: invokes `run_task` from the FR-02 service
        module; the service module is responsible for writing the
        result row to `task_results`.
    """
    try:
        await _run_subprocess(
            command=command,
            task_id=task_id,
            run_id=run_id,
        )
    except asyncio.CancelledError:
        # FR-02 §3 / NFR-03: re-raise so the orchestrator observes the
        # cancel signal (does NOT swallow).
        raise
    except Exception:
        # The 202 response has already been sent; surface non-cancel
        # failures via the persisted result row instead of crashing
        # the background worker.
        pass


@router.post(
    "/{task_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[_require_write],
)
async def run_task_endpoint(
    task_id: UUID,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Trigger task execution. AC-2.1.

    Returns ``202 Accepted`` with a 36-char ``run_id`` immediately; the
    actual subprocess is scheduled as a FastAPI BackgroundTask so the
    client does not have to wait for execution.

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.1: returns HTTP 202 + ``run_id``.
      - FR-02 §3: schedules `run_task` from the FR-02 service module.
      - FR-04: write scope via ``dependencies=[_require_write]``.
      - FR-06: delegates `get` / persistence to `task_repo`.
    """
    row = task_repo_mod.task_repo.get(str(task_id))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"task {task_id} not found",
        )
    run_id = str(uuid.uuid4())
    background_tasks.add_task(
        _execute_and_record,
        str(task_id),
        row["command"],
        run_id,
    )
    return {"run_id": run_id}


@router.get(
    "/{task_id}/runs",
    response_model=RunList,
    dependencies=[_require_read],
)
def list_runs_endpoint(
    task_id: UUID,
) -> RunList:
    """List execution history for a task. AC-2.6.

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.6: returns rows newest-to-oldest by `finished_at`.
      - FR-04: read scope via ``dependencies=[_require_read]``.
      - FR-06: delegates to `task_repo.task_repo.list_runs`.
    """
    items = task_repo_mod.task_repo.list_runs(str(task_id))
    return RunList(items=[RunOut(**row) for row in items])
