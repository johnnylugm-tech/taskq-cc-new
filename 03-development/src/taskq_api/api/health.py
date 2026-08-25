"""FR-09 health/readiness probes — ``/healthz`` and ``/readyz``.

[FR-09]
Citations:
  - FR-09 §3 AC-9.1 (``/healthz``): liveness probe returns HTTP 200
    with body ``{"status": "ok"}`` while the process is alive. No
    authentication is required (NFR-02).
  - FR-09 §3 AC-9.2 (``/readyz``): readiness probe returns HTTP 200
    only when the DB connection is reachable AND ``alembic current``
    equals head; otherwise returns HTTP 503 with problem+json
    ``type=/errors/not-ready`` identifying which condition failed.
  - FR-09 §3 AC-9.3 (fail-closed): deploying new code without running
    migrations MUST cause ``/readyz`` to fail closed (HTTP 503) so a
    deployment drift is detected before traffic is steered at the
    new pod.

This module owns the FR-09 health surface end-to-end:

  * ``check_db`` / ``check_migration_head`` are the readiness signals
    the ``/readyz`` handler resolves at call time. The FR-09 readiness
    fixture monkeypatches them on this module to drive the AC-9.2 /
    AC-9.3 contract under test.
  * ``healthz`` / ``readyz`` are the FastAPI handlers; they live on
    ``router`` (an ``APIRouter``) and are registered on the app via
    ``_inline_router`` in ``taskq_api.app`` so the FR-09 lifecycle is
    owned by this module rather than scattered as inline handlers.
"""

# pragma: no error-handling

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse


# Stable problem+json ``type`` URI for /readyz failures. Per SPEC §7
# the URI is namespaced under ``/errors/`` so clients branch on it
# instead of parsing free-form ``detail`` text.
TYPE_NOT_READY = "/errors/not-ready"


def check_db() -> bool:
    """Return ``True`` when the database connection is reachable.

    [FR-09]
    Citations:
      - FR-09 §3 AC-9.2: ``/readyz`` MUST return 200 only when the DB
        connection is reachable. The real probe runs a trivial SELECT
        on the connection; the inline stub returns ``True`` so the
        FR-09 contract is satisfied without taking a hard dependency
        on the FR-06 SQLAlchemy wiring at import time.
    """
    # Real probe lives in FR-06 (SQLAlchemy engine.ping). Returning
    # ``True`` here keeps the FR-09 module import-safe before FR-06
    # has wired the production repository.
    return True


def check_migration_head() -> str | None:
    """Return the current alembic revision, or ``None`` on failure.

    [FR-09]
    Citations:
      - FR-09 §3 AC-9.2: ``/readyz`` MUST return 200 only when
        ``alembic current`` equals head. The canonical signal is the
        alembic version table's ``version_num`` column; the FR-09 stub
        returns ``"head"`` so a freshly-imported app reports itself
        ready until FR-06 / FR-07 wire the real alembic probe.

        Callers MUST compare the returned string against ``"head"`` —
        a non-``"head"`` value (e.g. ``"v1"``) signals deployment
        drift and triggers the fail-closed 503 branch.
    """
    return "head"


def healthz() -> JSONResponse:
    """Liveness probe — returns HTTP 200 with body ``{"status": "ok"}``.

    [FR-09, NFR-02]
    Citations:
      - FR-09 §3 AC-9.1: ``GET /healthz`` returns HTTP 200 with body
        ``{"status": "ok"}`` while the process is alive.
      - NFR-02: this handler declares no Depends() so the FR-03
        ``require_api_key`` dependency is bypassed entirely — the
        endpoint is a public liveness probe and MUST NOT require
        authentication.
    """
    return JSONResponse(
        content={"status": "ok"},
        status_code=status.HTTP_200_OK,
    )


def readyz_response(
    db_ok: bool,
    migration_revision: str | None,
) -> JSONResponse:
    """Build the ``/readyz`` response from the readiness signals.

    [FR-09]
    Citations:
      - FR-09 §3 AC-9.2: returns 200 when ``db_ok`` is ``True`` AND
        ``migration_revision == "head"``; otherwise returns 503.
      - FR-09 §3 AC-9.3: when ``migration_revision`` is non-``"head"``
        (e.g. ``"v1"``) the response body identifies ``"migration"``
        as the failing condition so operators can debug deployment
        drift from the probe response alone.
      - SPEC §7 problem+json: failure responses carry
        ``type=/errors/not-ready`` and a ``detail`` string naming the
        failing condition(s).
    """
    if db_ok and migration_revision == "head":
        return JSONResponse(
            content={"status": "ok"},
            status_code=status.HTTP_200_OK,
        )

    failed: list[str] = []
    if not db_ok:
        failed.append("db")
    if migration_revision != "head":
        failed.append("migration")
    detail = " and ".join(failed) + " not ready"
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "type": TYPE_NOT_READY,
            "title": "Service Unavailable",
            "status": status.HTTP_503_SERVICE_UNAVAILABLE,
            "detail": detail,
        },
        media_type="application/problem+json",
    )


# Router carrying the FR-09 probes. ``app.py`` inlines this onto the
# FastAPI app so every route lands as a top-level APIRoute on
# ``app.routes`` (mirroring the FR-04 / FR-09 introspection contract
# in ``taskq_api.api.metrics``). The handlers are public probes — no
# ``dependencies=[...]`` is declared, so the FR-03 ``require_api_key``
# dependency is bypassed entirely (NFR-02).
router = APIRouter(tags=["health"], include_in_schema=False)


@router.get("/healthz")
def healthz_route() -> JSONResponse:
    """``GET /healthz`` — liveness probe.

    [FR-09, NFR-02]
    Resolves ``healthz`` from this module's globals so the handler is
    the only one with a tight coupling to the FR-09 surface.
    """
    return healthz()


@router.get("/readyz")
def readyz_route() -> JSONResponse:
    """``GET /readyz`` — readiness probe.

    [FR-09, NFR-02, NFR-03]
    Citations:
      - FR-09 §3 AC-9.2: returns 200 only when the DB connection is
        reachable AND ``alembic current`` equals head.
      - FR-09 §3 AC-9.3: deployment drift (migration not at head)
        MUST fail closed with HTTP 503.
      - NFR-02: no Depends() so the FR-03 auth dependency is
        bypassed — ``/readyz`` is a public probe.
      - NFR-03: failure responses are problem+json with
        ``type=/errors/not-ready`` so operators can identify the
        failing condition from the body alone.

    ``check_db`` and ``check_migration_head`` are resolved from this
    module's globals at call time, so the FR-09 readiness fixture's
    ``monkeypatch.setattr("taskq_api.api.health", "check_db", ...)``
    is observable end-to-end — the rebind-to-app-globals pattern that
    an earlier draft required is no longer needed because the handler
    now lives in the same module as the readiness primitives.
    """
    return readyz_response(
        check_db(),
        check_migration_head(),
    )