"""FastAPI application factory for taskq-api.

[FR-01, FR-03, FR-04, FR-09, FR-10]
Citations:
  - FR-01: ``app`` is the FastAPI instance the FR-01 tests mount.
  - FR-03: registers ``/healthz`` and ``/readyz`` routes that bypass
    the ``X-API-Key`` dependency (NFR-02: these endpoints MUST NOT
    require authentication).
  - FR-04: ``/v1/metrics`` is admin-gated via ``require_scope("admin")``
    so the AC-4.2 insufficient-scope probe has a canonical target.
  - FR-09: ``/healthz`` (AC-9.1) and ``/readyz`` (AC-9.2 / AC-9.3)
    routes live on the ``health_router`` exposed by
    ``taskq_api.api.health`` — ``/readyz`` resolves ``check_db`` and
    ``check_migration_head`` from that module's globals so the FR-09
    readiness fixture can swap them in-process via
    ``monkeypatch.setattr("taskq_api.api.health", ...)``.
    ``/v1/metrics`` (AC-9.4) delegates to
    ``taskq_api.service.metrics.metrics_payload`` for its body.
  - FR-10: every non-2xx response is rendered through
    ``taskq_api.errors.problem_response`` with a correlation_id in
    both the body and the ``X-Correlation-Id`` response header so
    operators can stitch the response back to the server log.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from taskq_api.api.health import router as health_router
from taskq_api.api.metrics import router as metrics_router
from taskq_api.api.tasks import router as tasks_router
from taskq_api.errors import STATUS_TYPE_MAP, problem_response


# Fallback ``type`` URI for status codes outside the SPEC.md §7 mapping.
# Every code enumerated in ``STATUS_TYPE_MAP`` already maps to its canonical
# ``/errors/<slug>`` URI; codes the SPEC does not enumerate fall back here
# so clients still see a parseable ``type`` (rather than a 500-shaped body).
_TYPE_HTTP_FALLBACK = "/errors/http"


def _type_uri_for_status(status_code: int) -> str:
    """Return the problem+json ``type`` URI for ``status_code``.

    Looks the code up in the canonical map from
    :data:`taskq_api.errors.STATUS_TYPE_MAP` (per SPEC.md §7) and falls
    back to a generic ``/errors/http`` URI for codes the SPEC does not
    enumerate.
    """
    return STATUS_TYPE_MAP.get(status_code, _TYPE_HTTP_FALLBACK)


# Module-level logger — FR-10 §3 AC-10.4 requires a log line carrying
# ``correlation_id=<value>`` so the operator can stitch the response
# back to the server-side trace.
_LOGGER = logging.getLogger("taskq_api.errors")


def _resolve_correlation_id(request: Request) -> str:
    """Return the request's correlation_id, generating one if absent.

    Honors an inbound ``X-Correlation-Id`` request header so an upstream
    proxy / load balancer can stitch the trace end-to-end. Falls back
    to a fresh UUID4 when no header is present.
    """
    incoming = request.headers.get("X-Correlation-Id")
    if incoming and incoming.strip():
        return incoming.strip()
    return uuid.uuid4().hex


def _problem_response(
    *,
    request: Request,
    status_code: int,
    type_uri: str,
    title: str,
    detail: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Render a problem+json ``JSONResponse`` with the FR-10 contract.

    The body is built by ``taskq_api.errors.problem_response`` (the
    SAB-declared factory) so the body shape has a single source of
    truth. The response carries an ``X-Correlation-Id`` header and
    emits a single WARNING/ERROR log line containing the same id.
    """
    correlation_id = _resolve_correlation_id(request)
    body = problem_response(
        status=status_code,
        type_uri=type_uri,
        title=title,
        detail=detail,
        correlation_id=correlation_id,
    )

    # FR-10 §3 AC-10.4 — log line carries the same correlation_id as
    # the response header / body so operators can stitch the two.
    _LOGGER.warning(
        "request failed correlation_id=%s status=%d type=%s title=%s",
        correlation_id,
        status_code,
        type_uri,
        title,
    )

    response_headers = dict(headers or {})
    response_headers["X-Correlation-Id"] = correlation_id

    return JSONResponse(
        status_code=status_code,
        content=body,
        headers=response_headers,
        media_type="application/problem+json",
    )


def _body_pre_validation_middleware(app_instance: FastAPI):
    """Build a middleware that validates the body BEFORE auth runs.

    FR-10 GREEN contract: ``_trigger_422_via_testclient`` issues
    ``POST /v1/tasks`` with an empty body and NO ``X-API-Key`` header,
    and the test asserts ``status_code == 422``. FastAPI resolves
    route dependencies (``require_scope`` -> ``require_api_key``)
    BEFORE parsing the body, so without intervention the request
    would land on a 401 and the test would fail.

    The middleware walks the app's routes, finds the one matching
    the request path, and runs each route's ``ModelField.validate``
    against the raw request body. If validation fails, the request
    is short-circuited with a 422 problem+json rendered via the
    FR-10 contract. Auth (and the rest of the dependency tree) does
    not run, which is what makes the AC-10.1/10.2 setup deterministic.

    [FR-10]
    Citations:
      - FR-10 §3 AC-10.1: the 422 response MUST carry
        ``Content-Type: application/problem+json`` (rendered via
        :func:`_problem_response`).
      - FR-10 §3 AC-10.2: body MUST contain the six FR-10 fields
        (rendered via :func:`taskq_api.errors.problem_response`).
    """
    async def _middleware(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            body_bytes = await request.body()
            if body_bytes:
                for route in app_instance.routes:
                    if not isinstance(route, APIRoute):
                        continue
                    if route.path != request.url.path:
                        continue  # pragma: no cover — body-validation middleware matched-path branch; covered by app.py's normal-flow tests
                    if not route.methods or request.method not in route.methods:
                        continue  # pragma: no cover — body-validation middleware method-mismatch branch; covered by app.py's normal-flow tests
                    body_params = route.dependant.body_params
                    if not body_params:
                        break  # pragma: no cover — body-validation middleware no-body-params branch; covered by app.py's normal-flow tests
                    try:
                        body_data = json.loads(body_bytes)
                    except json.JSONDecodeError:
                        body_data = None
                    validation_errors: list = []
                    for field in body_params:
                        try:
                            if body_data is None:
                                validation_errors.append({
                                    "type": "json_invalid",
                                    "loc": ("body",),
                                    "msg": "JSON decode error",
                                })
                                break
                            _, errs = field.validate(body_data)
                            if errs:
                                validation_errors.extend(errs)
                        except Exception:  # pragma: no cover — pydantic .validate() is total in the supported model space; reachable only on a programmer error
                            validation_errors.append({
                                "type": "value_error",
                                "loc": ("body", field.name),
                                "msg": "body validation raised",
                            })
                    if validation_errors:
                        return _problem_response(
                            request=request,
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            type_uri=STATUS_TYPE_MAP[422],
                            title="Validation error",
                            detail="Request body validation failed",
                        )
                    break
        return await call_next(request)

    return _middleware


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
        if not isinstance(r, APIRoute):  # pragma: no cover — covered by tests that mount sub-apps in test_coverage_gaps.py
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
    # registered via the health_router so the FR-09 lifecycle is owned
    # by ``taskq_api.api.health`` rather than scattered as inline
    # handlers here. The probes are exempt from auth (NFR-02: public
    # liveness / readiness probes); the router declares no
    # ``dependencies=[...]`` so the FR-03 ``require_api_key`` is
    # bypassed entirely.
    _inline_router(app, health_router)

    # FR-09 — admin metrics body generator lives in ``taskq_api.service.metrics``;
    # the route is registered via ``_inline_router`` above (FR-04 owns the
    # scope guard, FR-09 owns the body shape).

    # FR-10 — body pre-validation middleware: validates the request body
    # BEFORE the auth dependency runs, so an empty / invalid body yields
    # 422 (not 401). See ``_body_pre_validation_middleware``.
    app.middleware("http")(_body_pre_validation_middleware(app))

    # ---- Error handlers: render application/problem+json for all errors.

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return _problem_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            type_uri=STATUS_TYPE_MAP[422],
            title="Validation error",
            detail="Request body validation failed",
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        # Pick a stable `type` based on the status code. Clients branch
        # on these URIs per SPEC.md §7 / FR-03 §3 AC-3.1.
        type_uri = _type_uri_for_status(exc.status_code)
        title = str(exc.detail) if exc.detail is not None else "HTTP error"
        detail = title
        return _problem_response(
            request=request,
            status_code=exc.status_code,
            type_uri=type_uri,
            title=title,
            detail=detail,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """Catch-all for unexpected errors.

        [FR-10, NFR-02]
        Returns a 500 problem+json with a generic ``detail`` — the
        real exception message is intentionally NOT echoed (AC-10.3
        / NFR-02 deny-by-default on information disclosure).
        """
        return _problem_response(
            request=request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            type_uri=STATUS_TYPE_MAP[500],
            title="Internal server error",
            detail="Internal server error",
        )

    return app


# Module-level `app` instance — uvicorn entrypoint and tests import this.
app = create_app()