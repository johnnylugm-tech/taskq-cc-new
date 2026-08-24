"""FastAPI dependencies for auth + scope checks.

The `require_scope(scope)` factory returns a dependency that yields the
authenticated principal when the caller's API key carries `scope`; otherwise
it raises 401/403. Real auth/scope enforcement lives in FR-03 / FR-04 — this
module is the shared stub they both depend on, exposed here so that FR-01
handlers can declare the dependency today.

[FR-01, FR-03, FR-04, FR-05]
Citations:
  - `require_scope` is invoked by FR-01 handlers via
    `Depends(require_scope("write"))`. The required scope is bound at handler
    decoration time so FastAPI can introspect the inner dependency without
    treating `scope` as a query parameter.
  - The dependency override mechanism maps this module's `require_scope`
    symbol to a test-only callable; FastAPI invokes that callable with the
    same positional arg the real `require_scope` would receive.
"""
from __future__ import annotations

from typing import Callable

from fastapi import HTTPException, status


def require_scope(scope: str = "read") -> Callable[[], dict]:
    """Return a FastAPI dependency that enforces `scope`.

    Args:
        scope: Minimum scope required by the calling route (e.g. `"write"`,
            `"admin"`). Bound at handler decoration time
            (`Depends(require_scope("write"))`) so FastAPI can introspect
            the inner callable without promoting `scope` to a query
            parameter.

    Returns:
        Dependency callable. The inner function returns
        `{"scope": <granted_scope>, "key_id": <key_id>}` on success.

    Raises:
        HTTPException: 401 when no API key is present or it is invalid;
            403 when the principal's scope is insufficient.

    [FR-01, FR-03, FR-04, FR-05]
    Citations:
      - FR-01: declared as the route dependency on every `/v1/tasks` handler.
      - FR-03: real implementation extracts the principal from the API key.
      - FR-04: real implementation compares principal.scope vs requested scope.
    """
    # Real implementation (FR-03 / FR-04) is provided per route below.
    # For FR-01 RED tests this dependency is overridden in the test fixture
    # by `app.dependency_overrides[deps.require_scope]`. The override is
    # invoked with the same positional args the real factory would receive.
    def _dependency() -> dict:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return _dependency