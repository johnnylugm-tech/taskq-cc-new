"""FastAPI application factory for taskq-api.

[FR-01, FR-09]
Citations:
  - FR-01: `app` is the FastAPI instance the FR-01 tests mount.
  - FR-09: health + metrics endpoints will be wired here in their own FR.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from taskq_api.api.tasks import router as tasks_router


# Stable type URIs for the problem+json responses. Errors reference these
# so clients can branch on `type` instead of parsing `detail`.
_TYPE_VALIDATION = "/errors/validation"
_TYPE_NOT_FOUND = "/errors/not-found"
_TYPE_HTTP = "/errors/http"


def _problem(
    *,
    status_code: int,
    type_uri: str,
    title: str,
    detail: object,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = {
        "type": type_uri,
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    return JSONResponse(
        status_code=status_code,
        content=body,
        headers=headers,
        media_type="application/problem+json",
    )


def create_app() -> FastAPI:
    """Build the FastAPI app, wire routers + error handlers."""
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description="Task queue REST API. [FR-01]",
    )

    # FR-01 — `/v1/tasks` CRUD.
    app.include_router(tasks_router)

    # FR-09 — health/metrics reserved here, registered by their own FR.

    # ---- Error handlers: render application/problem+json for all errors.

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            type_uri=_TYPE_VALIDATION,
            title="Validation error",
            detail=exc.errors(),
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        # Pick a stable `type` based on the status code family. Clients
        # branch on these URIs per SPEC.md §7.
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            type_uri = _TYPE_NOT_FOUND
        else:
            type_uri = _TYPE_HTTP
        return _problem(
            status_code=exc.status_code,
            type_uri=type_uri,
            title=str(exc.detail) if exc.detail is not None else "HTTP error",
            detail=exc.detail,
            headers=exc.headers,
        )

    return app


# Module-level `app` instance — uvicorn entrypoint and tests import this.
app = create_app()