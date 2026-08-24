"""FastAPI application factory for taskq-api.

[FR-01, FR-03, FR-04, FR-09]
Citations:
  - FR-01: `app` is the FastAPI instance the FR-01 tests mount.
  - FR-03: registers ``/healthz`` and ``/readyz`` routes that bypass
    the ``X-API-Key`` dependency (NFR-02: these endpoints MUST NOT
    require authentication).
  - FR-04: ``/v1/metrics`` is admin-gated via ``require_scope("admin")``
    so the AC-4.2 insufficient-scope probe has a canonical target.
  - FR-09: ``/healthz`` (AC-9.1) and ``/readyz`` (AC-9.2 / AC-9.3)
    route handlers live here but delegate to the helpers in
    ``taskq_api.api.health`` (``healthz``, ``readyz_response``).
    The handlers import ``check_db`` and ``check_migration_head``
    at module load time so the FR-09 readiness fixture can swap
    them in-process via ``monkeypatch.setattr`` on this module's
    globals. ``/v1/metrics`` (AC-9.4) delegates to
    ``taskq_api.service.metrics.metrics_payload`` for its body.
"""
from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from taskq_api.api import health as health_mod
from taskq_api.api.deps import TYPE_FORBIDDEN, TYPE_RATE_LIMITED, TYPE_UNAUTHENTICATED
from taskq_api.api.metrics import router as metrics_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.service.metrics import metrics_payload

# Re-bind the FR-09 readiness primitives into this module's globals so
# the FR-09 readiness fixture's ``monkeypatch.setattr("taskq_api.app.check_db", ...)``
# lands on the function the ``readyz`` handler resolves at call time.
# The handler reads ``check_db`` from its enclosing module's globals
# (NOT from ``health_mod``), so the rebind below is what makes the
# fixture's dual-target patch observable end-to-end.
check_db = health_mod.check_db
check_migration_head = health_mod.check_migration_head


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
    status.HTTP_429_TOO_MANY_REQUESTS: TYPE_RATE_LIMITED,
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
    headers: Mapping[str, str] | None = None,
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

    # FR-09 — `/healthz` (AC-9.1) and `/readyz` (AC-9.2 / AC-9.3) are
    # exempt from auth (NFR-02: public liveness / readiness probes).
    # The handler bodies delegate to ``taskq_api.api.health`` so the
    # readiness fixture's monkeypatch on ``taskq_api.app.check_db`` /
    # ``taskq_api.app.check_migration_head`` is observable end-to-end:
    # the helpers are bound into this module's globals above, and the
    # handler resolves them from those globals at call time.
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        """Liveness probe — returns ``200 OK`` with body ``{"status":"ok"}``.

        [FR-09, NFR-02]
        Citations:
          - FR-09 §3 AC-9.1: ``GET /healthz`` returns 200 with body
            ``{"status":"ok"}`` while the process is alive.
          - NFR-02: this handler declares no Depends() so the FR-03
            ``require_api_key`` dependency is bypassed entirely.
        """
        return health_mod.healthz()

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> JSONResponse:
        """Readiness probe — 200 when DB up AND migration at head, else 503.

        [FR-09, NFR-02, NFR-03]
        Citations:
          - FR-09 §3 AC-9.2: returns 200 only when the DB connection
            is reachable AND ``alembic current`` equals head.
          - FR-09 §3 AC-9.3: deployment drift (migration not at head)
            MUST fail closed with HTTP 503.
          - NFR-02: no Depends() so the FR-03 auth dependency is
            bypassed — ``/readyz`` is a public probe.
          - NFR-03: failure responses are problem+json with
            ``type=/errors/not-ready`` so operators can identify the
            failing condition from the body alone.
        """
        return health_mod.readyz_response(
            check_db(),
            check_migration_head(),
        )

    # FR-09 — admin metrics body generator lives in ``taskq_api.service.metrics``;
    # the route is registered via ``_inline_router`` above (FR-04 owns the
    # scope guard, FR-09 owns the body shape).

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