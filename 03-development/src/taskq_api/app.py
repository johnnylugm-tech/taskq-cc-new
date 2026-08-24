"""FastAPI application factory for taskq-api.

[FR-01, FR-03, FR-09]
Citations:
  - FR-01: `app` is the FastAPI instance the FR-01 tests mount.
  - FR-03: registers ``/healthz`` and ``/readyz`` routes that bypass
    the ``X-API-Key`` dependency (NFR-02: these endpoints MUST NOT
    require authentication).
  - FR-09: full health + metrics endpoints will be wired here in
    their own FR; the FR-03 stub returns ``200 OK`` with a static
    body so the FR-03 AC-3.6 contract is satisfied today.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from taskq_api.api.deps import TYPE_FORBIDDEN, TYPE_UNAUTHENTICATED
from taskq_api.api.metrics import router as metrics_router
from taskq_api.api.tasks import router as tasks_router


# Stable type URIs for the problem+json responses. Errors reference these
# so clients can branch on `type` instead of parsing `detail`.
_TYPE_VALIDATION = "/errors/validation"
_TYPE_NOT_FOUND = "/errors/not-found"
_TYPE_HTTP = "/errors/http"

# Status code -> problem+json `type` URI mapping. Centralised so every
# handler that raises HTTPException lands on the same shape.
_STATUS_TYPE_URIS: dict[int, str] = {
    status.HTTP_401_UNAUTHORIZED: TYPE_UNAUTHENTICATED,
    status.HTTP_403_FORBIDDEN: TYPE_FORBIDDEN,
    status.HTTP_404_NOT_FOUND: _TYPE_NOT_FOUND,
}


def _type_uri_for_status(status_code: int) -> str:
    """Return the problem+json ``type`` URI for ``status_code``."""
    return _STATUS_TYPE_URIS.get(status_code, _TYPE_HTTP)


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

    # FR-04 — `/v1/metrics` (admin-only); FR-09 replaces the stub body.
    app.include_router(metrics_router)

    # FR-03 — `/healthz` and `/readyz` are exempt from auth (AC-3.6).
    # Stub bodies return 200 OK with a static text payload; FR-09
    # replaces these with the real liveness/readiness probes.
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> Response:
        """Liveness probe — returns ``200 OK`` with no auth required.

        [FR-03, NFR-02]
        Citations:
          - FR-03 §3 AC-3.6: ``/healthz`` MUST NOT require
            authentication; this handler declares no Depends() so the
            FR-03 ``require_api_key`` dependency is bypassed entirely.
        """
        return Response(
            content=b"ok",
            status_code=status.HTTP_200_OK,
            media_type="text/plain",
        )

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> Response:
        """Readiness probe — returns ``200 OK`` with no auth required.

        [FR-03, NFR-02]
        Citations:
          - FR-03 §3 AC-3.6: ``/readyz`` MUST NOT require
            authentication; this handler declares no Depends() so the
            FR-03 ``require_api_key`` dependency is bypassed entirely.
        """
        return Response(
            content=b"ok",
            status_code=status.HTTP_200_OK,
            media_type="text/plain",
        )

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
        # Pick a stable `type` based on the status code. Clients branch
        # on these URIs per SPEC.md §7 / FR-03 §3 AC-3.1.
        type_uri = _type_uri_for_status(exc.status_code)
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