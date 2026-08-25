"""Admin-only metrics endpoint.

[FR-04, FR-09]
Citations:
  - FR-04 §3 AC-4.3: every ``/v1/*`` route traverses the same
    ``require_scope(...)`` dependency. ``/v1/metrics`` is admin-gated
    so the AC-4.2 insufficient-scope probe (a write-scope caller
    hitting an admin route) has a canonical target.
  - FR-09 §3 AC-9.4: ``/v1/metrics`` reports task counts by status,
    execution-latency percentiles, and rate-limit rejection counts.
    The body shape is delivered by ``taskq_api.service.metrics``;
    this module owns the route registration + admin scope guard.
"""

# pragma: no error-handling

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from taskq_api.api import deps
from taskq_api.service.metrics import metrics_payload


# Router under `/v1` — separate from the `/v1/tasks` so the prefix
# stays clean and grep-friendly. FR-04 only requires the route exist
# and be admin-gated; FR-09 owns the body shape.
router = APIRouter(prefix="/v1", tags=["metrics"])


_require_admin = deps.require_scope("admin")


@router.get(
    "/metrics",
    dependencies=[_require_admin],
)
def get_metrics() -> JSONResponse:
    """Return the admin-only metrics body.

    [FR-04, FR-09, NFR-04]
    Citations:
      - FR-04 §3 AC-4.2 / AC-4.3: gated by ``require_scope("admin")``
        so a write-scope caller is rejected with 403 before the handler
        runs. Declared at the decorator level
        (``dependencies=[_require_admin]``) so route introspection sees
        the authz dependency in ``route.dependencies`` per FR-04 AC-4.3.
      - FR-09 §3 AC-9.4: body is delivered by
        ``taskq_api.service.metrics.metrics_payload`` (task counts
        by status, execution-latency percentiles p50/p95/p99, and
        rate-limit rejection counts).
      - NFR-04: response is JSON-only; no DSN, key material, or
        secret leakage.
    """
    return JSONResponse(
        content=metrics_payload(),
        status_code=status.HTTP_200_OK,
    )
