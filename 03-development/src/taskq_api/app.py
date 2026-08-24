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
from fastapi.routing import APIRoute

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


def _inline_router(app: FastAPI, router) -> None:
    """Register every route on ``router`` directly on ``app``.

    FR-04 §3 AC-4.3 introspects ``app.routes`` looking for ``APIRoute``
    instances whose ``path`` starts with ``/v1``. FastAPI 0.116+ wraps
    each ``include_router`` call in a ``_IncludedRouter`` proxy whose
    ``path`` attribute is missing — so the test sees zero nested
    routes on system Python (FastAPI 0.141.1) even though the routes
    are fully registered.

    Registering each route directly preserves the AC-4.3 contract on
    every FastAPI version, while keeping the per-FR router modules
    (``taskq_api.api.tasks``, ``taskq_api.api.metrics``) as the
    canonical source of the handler implementations.

    The function is intentionally local to ``app.py`` — moving it to
    a shared util would invite drift between FR-owned routers.
    """
    for r in router.routes:
        # ``add_api_route`` accepts the same kwargs an APIRouter decorator
        # uses (path, endpoint, methods, dependencies, response_model,
        # status_code, response_class, tags, name, ...). Passing
        # ``r.endpoint`` plus the route's existing ``methods`` /
        # ``dependencies`` reproduces the registered handler exactly.
        # Only APIRoute instances carry the attributes add_api_route needs;
        # plain ``Route`` instances (e.g. mounted sub-apps) are skipped.
        if not isinstance(r, APIRoute):
            continue
        app.add_api_route(
            path=r.path,
            endpoint=r.endpoint,
            methods=list(r.methods) if r.methods else None,
            dependencies=list(r.dependencies),
            response_model=r.response_model,
            status_code=r.status_code or 200,
            response_class=r.response_class,
            tags=list(r.tags),
            name=r.name,
            include_in_schema=r.include_in_schema,
        )


def create_app() -> FastAPI:
    """Build the FastAPI app, wire routers + error handlers."""
    app = FastAPI(
        title="taskq-api",
        version="0.1.0",
        description="Task queue REST API. [FR-01]",
    )

    # FR-01 — `/v1/tasks` CRUD. Routes are inlined (NOT include_router'd)
    # so the FR-04 AC-4.3 route-introspection check sees every /v1 route
    # as a direct APIRoute on ``app.routes`` regardless of FastAPI version.
    _inline_router(app, tasks_router)

    # FR-04 — `/v1/metrics` (admin-only); FR-09 replaces the stub body.
    _inline_router(app, metrics_router)

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