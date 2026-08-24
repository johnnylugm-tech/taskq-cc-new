"""Admin-only metrics endpoint.

[FR-04, FR-09]
Citations:
  - FR-04 §3 AC-4.3: every ``/v1/*`` route traverses the same
    ``require_scope(...)`` dependency. ``/v1/metrics`` is admin-gated
    so the AC-4.2 insufficient-scope probe (a write-scope caller
    hitting an admin route) has a canonical target.
  - FR-09: full metrics body is delivered in its own FR; the FR-04
    stub returns a static text payload so the scope guard is the
    unit under test, not the metrics payload.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from taskq_api.api import deps


# Router under `/v1` — separate from the `/v1/tasks` so the prefix
# stays clean and grep-friendly. FR-04 only requires the route exist
# and be admin-gated; the body is replaced by FR-09.
router = APIRouter(prefix="/v1", tags=["metrics"])


_require_admin = deps.require_scope("admin")


@router.get(
    "/metrics",
    response_class=Response,
    dependencies=[_require_admin],
)
def get_metrics() -> Response:
    """Return the admin-only metrics body.

    [FR-04, FR-09]
    Citations:
      - FR-04 §3 AC-4.2 / AC-4.3: gated by ``require_scope("admin")``
        so a write-scope caller is rejected with 403 before the handler
        runs. Declared at the decorator level
        (``dependencies=[_require_admin]``) so route introspection sees
        the authz dependency in ``route.dependencies`` per FR-04 AC-4.3.
      - FR-09: stub body — the real metrics payload lands with FR-09.
    """
    return Response(
        content=b"# FR-09 reserves /v1/metrics\n",
        status_code=status.HTTP_200_OK,
        media_type="text/plain",
    )
