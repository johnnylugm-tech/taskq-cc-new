"""FastAPI dependencies for API-key auth + scope checks.

The ``require_scope(scope)`` factory returns a dependency that yields the
authenticated principal when the caller's API key carries ``scope``;
otherwise it raises 401/403 with a problem+json body. Real auth lives
here in FR-03; FR-04 (scope semantics) is folded into the same factory
so the route handlers can declare one dependency per scope.

The factory pattern keeps the original FR-01 / FR-02 calling convention
(``Depends(require_scope("write"))``) so route handlers do not need to
declare the sub-dependency themselves. Auth is invoked inline by the
returned callable (which receives the ``X-API-Key`` header as an
explicit parameter) because FastAPI only resolves ``Depends(...)`` on
the registered dependency tree, not on values returned by a factory
call.

[FR-01, FR-03, FR-04, FR-05]
Citations:
  - ``require_scope`` is invoked by FR-01 handlers via
    ``Depends(require_scope("write"))``. The required scope is bound at
    handler decoration time so FastAPI can introspect the inner
    dependency without treating ``scope`` as a query parameter.
  - FR-03: ``require_api_key`` extracts the ``X-API-Key`` header,
    looks up its SHA-256 hash in ``key_repo``, and rejects 401 on
    missing / unrecognised / revoked rows.
  - FR-04: ``require_scope`` calls ``require_api_key`` first, then
    enforces the requested scope.
"""
from __future__ import annotations

import secrets
from typing import Callable

from fastapi import Depends, Header, HTTPException, status

import taskq_api.repository.key_repo as key_repo_mod
from taskq_api.service.auth import compare_keys, hash_key


# Module-level alias so the FR-03 test fixture can monkeypatch
# ``deps.key_repo`` independently of the underlying repository module.
# ``require_api_key`` / ``create_key`` always read through this attribute.
key_repo = key_repo_mod.key_repo


__all__ = [
    "hash_key",
    "compare_keys",
    "require_api_key",
    "require_scope",
    "create_key",
    "key_repo",
]


# ---------------------------------------------------------------------------
# problem+json type URIs — stable across every auth-failure response.
# The global ``HTTPException`` handler in ``taskq_api.app`` reads these
# constants to populate the body's ``type`` field (FR-03 §3 AC-3.1).
# ---------------------------------------------------------------------------

TYPE_UNAUTHENTICATED = "/errors/unauthenticated"
TYPE_FORBIDDEN = "/errors/forbidden"


def _unauthorized(detail: str) -> HTTPException:
    """Build the standard 401 problem+json for an unauthenticated request."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "ApiKey"},
    )


def _forbidden(detail: str) -> HTTPException:
    """Build the standard 403 problem+json for an insufficient scope."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Auth primitive — pure FastAPI dependency.
# ---------------------------------------------------------------------------
def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> dict:
    """FastAPI dependency that authenticates the request's ``X-API-Key``.

    On success returns the principal dict ``{"key_id": ..., "scope": ...}``
    so downstream dependencies (``require_scope``) can branch on it.

    [FR-03, NFR-02]
    Citations:
      - FR-03 §3 AC-3.1: missing / invalid / revoked keys return 401
        with ``type=/errors/unauthenticated`` in the problem+json body.
      - FR-03 §3 AC-3.5: a non-null ``revoked_at`` is treated as invalid.
      - FR-03 §3 AC-3.2: lookup is by SHA-256 hash of the presented key.
    """
    if not x_api_key:
        raise _unauthorized("missing X-API-Key header")

    row = key_repo.find_by_hash(hash_key(x_api_key))
    if row is None:
        raise _unauthorized("invalid X-API-Key")

    if row.get("revoked_at"):
        raise _unauthorized("revoked X-API-Key")

    return {"key_id": row.get("key_id"), "scope": row.get("scope")}


# Scope ordering — a higher rank satisfies a lower one.
_SCOPE_RANK = {"read": 0, "write": 1, "admin": 2}


def require_scope(scope: str = "read") -> Callable[[], dict]:
    """Return a FastAPI dependency that authenticates and enforces ``scope``.

    The returned callable declares ``Depends(require_api_key)`` so
    FastAPI's dependency resolver recursively wires the auth check
    before the scope check runs. This is the FR-03/FR-04 contract:
    ``require_api_key`` (FR-03) authenticates the presented key, and
    the scope branch here (FR-04) enforces that the principal's scope
    satisfies the requested scope.

    [FR-01, FR-03, FR-04, FR-05]
    Citations:
      - FR-01: declared as the route dependency on every ``/v1/tasks``
        handler via ``Depends(require_scope("..."))``.
      - FR-03: depends on ``require_api_key`` which performs the SHA-256
        lookup + revocation check.
      - FR-04: enforces that the principal's scope satisfies the
        requested scope; insufficient scope → 403.
    """
    required_rank = _SCOPE_RANK.get(scope, 99)

    def _dependency(principal: dict = Depends(require_api_key)) -> dict:
        # Scope (FR-04): higher rank satisfies a lower one.
        granted = str(principal.get("scope"))
        if _SCOPE_RANK.get(granted, -1) < required_rank:
            raise _forbidden(f"scope '{scope}' required")
        return principal

    return _dependency


# ---------------------------------------------------------------------------
# Key creation — plaintext returned to the caller once, never persisted.
# ---------------------------------------------------------------------------
def create_key(scope: str) -> str:
    """Generate a fresh API key, persist its hash, return the plaintext.

    The plaintext is returned exactly once (the contract of FR-03 §3
    AC-3.4); the caller is responsible for handing it to the user.
    Only the SHA-256 hash is persisted via ``key_repo.create`` so the
    plaintext never appears in any persisted state.

    [FR-03, NFR-02, NFR-04]
    Citations:
      - FR-03 §3 AC-3.2: stored value is a 64-char hex SHA-256 hash.
      - FR-03 §3 AC-3.4: plaintext printed exactly once at creation.
      - NFR-04 (security): plaintext MUST NOT appear in any persisted
        file (logs, metrics, DB rows).
    """
    # 32 bytes of entropy -> 43-char URL-safe base64 (well above the
    # 16-char threshold the FR-03 stdout-token regex matches).
    plaintext = secrets.token_urlsafe(32)
    key_repo.create(scope=scope, key_hash=hash_key(plaintext))
    return plaintext
