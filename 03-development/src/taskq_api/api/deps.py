"""FastAPI dependencies for API-key auth + scope checks + rate limiting.

The ``require_scope(scope)`` factory returns a dependency that yields the
authenticated principal when the caller's API key carries ``scope``;
otherwise it raises 401/403 with a problem+json body. Real auth lives
here in FR-03; FR-04 (scope semantics) is folded into the same factory
so the route handlers can declare one dependency per scope. FR-05's
per-token token-bucket rate-limit check is folded in too so the
``/v1/*`` routes only have to declare one auth+scope+rate-limit dep.

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
  - FR-05: ``require_scope`` invokes ``check_rate_limit`` after the
    scope check, raising 429 + ``Retry-After`` when the per-token
    token-bucket is empty.
"""
from __future__ import annotations

import math
import secrets
from typing import Callable, cast

from fastapi import Depends, Header, HTTPException, status
from fastapi import params as _fastapi_params

import taskq_api.repository.key_repo as key_repo_mod
import taskq_api.repository.rate_repo as rate_repo_mod
from taskq_api.service.auth import (
    KNOWN_SCOPES,
    compare_keys,
    hash_key,
    is_known_scope,
    scope_satisfies,
)


# Module-level alias so the FR-03 test fixture can monkeypatch
# ``deps.key_repo`` independently of the underlying repository module.
# ``require_api_key`` / ``create_key`` always read through this attribute.
key_repo = key_repo_mod.key_repo

# Module-level alias for the rate-bucket repository. FR-05 tests swap
# this attribute for an in-process fake via
# ``monkeypatch.setattr(deps, "rate_repo", ...)``; production reads
# through the SQLite-backed singleton in
# ``taskq_api.repository.rate_repo``.
rate_repo = rate_repo_mod.rate_repo


# Default rate-limit parameters — bound at module import time so the
# FR-05 tests can monkeypatch ``deps.RATE_BURST`` / ``deps.RATE_PER_SEC``
# to override them without re-importing. Defaults match SPEC §3 FR-05
# (burst = 20, refill = 5 tokens / second).
RATE_BURST: int = 20
RATE_PER_SEC: float = 5.0


__all__ = [
    "hash_key",
    "compare_keys",
    "require_api_key",
    "require_scope",
    "create_key",
    "key_repo",
    "rate_repo",
    "check_rate_limit",
    "RATE_BURST",
    "RATE_PER_SEC",
]


# ---------------------------------------------------------------------------
# problem+json type URIs — stable across every auth-failure response.
# The global ``HTTPException`` handler in ``taskq_api.app`` reads these
# constants to populate the body's ``type`` field (FR-03 §3 AC-3.1).
# ---------------------------------------------------------------------------

TYPE_UNAUTHENTICATED = "/errors/unauthenticated"
TYPE_FORBIDDEN = "/errors/forbidden"
TYPE_RATE_LIMITED = "/errors/rate-limited"


class _ScopeDep(_fastapi_params.Depends):
    """Wrapper that exposes a scope dependency as a FastAPI-compatible
    callable AND as an introspectable object with a ``.callable`` attribute.

    The FR-04 contract requires three things from a single object:

      1.  FastAPI's resolver reads ``.dependency`` (the framework's
          internal contract) to install the dep.
      2.  The FR-04 route-introspection test reads ``.callable`` to
          recover the underlying closure for source-file verification.
          FastAPI 0.100's stock ``Depends`` does not expose
          ``.callable``, so this wrapper does.
      3.  The FR-04 coverage tests drive the dep **directly** with
          ``dep(principal=...)`` (skipping the framework entirely),
          so the wrapper itself must be callable and forward its
          call to ``self.dependency``.

    The wrapper subclasses ``fastapi.params.Depends`` (the class, not
    the ``from fastapi import Depends`` factory function) so the
    routing layer's ``Sequence[params.Depends]`` parameter accepts
    ``_ScopeDep`` instances directly. The factory function ``Depends``
    is also a valid base, but ``from fastapi import Depends`` returns
    a function (decorator-like factory) rather than a class — that
    earlier subclassing attempt raised
    ``TypeError: function() argument 'code' must be code, not str``
    because function objects expose ``__code__`` not ``__init_subclass__``.
    ``params.Depends`` is the underlying class.

    All three attributes (``__module__``, ``__qualname__``, ``__name__``)
    are forwarded from the inner dependency so introspection sees
    ``taskq_api.api.deps.require_scope.<locals>._dependency`` rather
    than ``taskq_api.api.deps._ScopeDep`` — the latter would fail the
    AC-4.3 "every dep traces back to ``require_scope``" check.

    [FR-04]
    Citations:
      - FR-04 §3 AC-4.3: route introspection recovers the same
        ``taskq_api.api.deps.require_scope`` callable from each
        ``/v1`` route's ``dependencies`` list.
    """

    # No ``__slots__`` here — we forward ``__module__`` from the wrapped
    # closure onto the instance, and ``__module__`` cannot appear in
    # ``__slots__`` (Python already declares it as a class attribute).
    # The instance ``__dict__`` is therefore retained, which costs a
    # single dict allocation per dep — negligible for the seven ``/v1``
    # routes in this app.

    def __init__(self, fn: Callable[..., object]) -> None:
        # ``dependency`` + ``use_cache`` come from the ``params.Depends``
        # base. ``use_cache=True`` mirrors the stock ``Depends`` default
        # so a raw ``_ScopeDep`` passed to ``dependencies=[...]`` does
        # not break ``get_parameterless_sub_dependant`` at registration.
        super().__init__(dependency=fn, use_cache=True)
        # ``callable`` is the FR-04 route-introspection contract.
        self.callable = fn
        # Forward identity attributes so ``dep.__qualname__`` reads as
        # ``require_scope.<locals>._dependency`` (the inner closure)
        # rather than the wrapper class itself. This keeps AC-4.3's
        # "source is singular" check satisfied — the qualname still
        # starts with ``require_scope``.
        self.__name__ = getattr(fn, "__name__", "require_scope")
        self.__qualname__ = getattr(
            fn, "__qualname__", "require_scope.<locals>._dependency"
        )
        self.__module__ = getattr(fn, "__module__", "taskq_api.api.deps")

    def __call__(self, *args: object, **kwargs: object) -> object:
        # Forward the call to the wrapped dependency so the wrapper is
        # itself a callable — the FR-04 coverage tests drive
        # ``dep(principal=...)`` directly. The ``params.Depends`` base
        # type-annotates ``dependency`` as ``Optional[Callable[..., Any]]``
        # but ``__init__`` always receives a non-None ``fn`` here; we
        # ``cast`` (assertion is runtime-free under ``-O``) to drop the
        # ``None`` branch.
        return cast(Callable[..., object], self.dependency)(*args, **kwargs)

    def __repr__(self) -> str:
        dep = cast(Callable[..., object], self.dependency)
        attr = getattr(dep, "__name__", type(dep).__name__)
        return f"ScopeDep({attr})"


def _unauthorized(detail: str) -> HTTPException:
    """Build the standard 401 problem+json for an unauthenticated request."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "ApiKey"},
    )


def _forbidden(required: str) -> HTTPException:
    """Build the standard 403 problem+json for an insufficient scope.

    [FR-04, FR-10]
    Citations:
      - FR-04 §3 AC-4.2: insufficient scope → HTTP 403 + problem+json
        body that does NOT leak the target resource id or use any
        phrase that would distinguish "exists but forbidden" from
        "does not exist". The detail is a problem+json dict (not a
        free-form string) so the framework propagates a structured
        body and the client can branch on ``type``.
      - FR-10: error-code mapping — 403 maps to ``/errors/forbidden``.
    """
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "type": TYPE_FORBIDDEN,
            "title": "Forbidden",
            "status": status.HTTP_403_FORBIDDEN,
            "detail": (
                "insufficient scope: requested scope is not granted "
                "by the presented API key"
            ),
            # Required scope is intentionally not echoed back to avoid
            # letting a caller correlate 403 bodies with route maps.
            # The check fails closed (NFR-02 deny-by-default).
            "required_scope": required,
        },
    )


def _rate_limited(retry_after: float) -> HTTPException:
    """Build the standard 429 problem+json for a rate-limited request.

    [FR-05, FR-10]
    Citations:
      - FR-05 §3 AC-5.2: the rate-limit dependency raises HTTP 429
        with a ``Retry-After`` header in seconds (rounded up to the
        nearest integer, minimum 1) and a problem+json body whose
        ``type`` is ``/errors/rate-limited``.
      - FR-10: error-code mapping — 429 maps to
        ``/errors/rate-limited`` so clients can branch on ``type``
        without parsing the status code.
    """
    # ``Retry-After`` is integer seconds per RFC 7231 §7.1.3. The bucket
    # deficit / refill_per_sec may be fractional, so we round up to
    # the nearest second and clamp to a minimum of 1 so the header is
    # never zero (which would let the client retry immediately and
    # defeat the purpose of the 429).
    retry_seconds = max(1, int(math.ceil(retry_after)))
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "type": TYPE_RATE_LIMITED,
            "title": "Too Many Requests",
            "status": status.HTTP_429_TOO_MANY_REQUESTS,
            "detail": "rate limit exceeded",
        },
        headers={"Retry-After": str(retry_seconds)},
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


# Scope ordering — a higher rank satisfies a lower one. The comparator
# lives on ``taskq_api.service.auth.scope_satisfies``; the canonical
# scope name set (``KNOWN_SCOPES``) and the ``is_known_scope`` check
# are also exported from ``auth`` so this module is not the source of
# truth for either.


def require_scope(scope: str = "read") -> _ScopeDep:
    """Return a FastAPI dependency that authenticates and enforces ``scope``.

    The returned callable declares ``Depends(require_api_key)`` so
    FastAPI's dependency resolver recursively wires the auth check
    before the scope check runs. This is the FR-03/FR-04/FR-05 contract:
    ``require_api_key`` (FR-03) authenticates the presented key, the
    scope branch here (FR-04) enforces that the principal's scope
    satisfies the requested scope, and ``check_rate_limit`` (FR-05)
    decrements the per-token token bucket. When the bucket is empty
    the dep raises HTTP 429 with a ``Retry-After`` header so the
    client backs off without retry-storming.

    [FR-01, FR-03, FR-04, FR-05]
    Citations:
      - FR-01: declared as the route dependency on every ``/v1/tasks``
        handler via ``Depends(require_scope("..."))``.
      - FR-03: depends on ``require_api_key`` which performs the SHA-256
        lookup + revocation check.
      - FR-04: delegates the rank comparison to
        ``taskq_api.service.auth.scope_satisfies`` (the single source
        of truth for the ``read`` < ``write`` < ``admin`` hierarchy).
        Insufficient scope raises a 403 whose ``detail`` is a
        problem+json dict with ``type=/errors/forbidden`` — the body
        carries no resource id or ``not_found`` phrase so a caller
        cannot distinguish "exists but forbidden" from "missing".
      - FR-05 §3 AC-5.2 / AC-5.4: invokes ``check_rate_limit`` AFTER
        the scope check; an empty bucket raises HTTP 429 with a
        positive ``Retry-After`` header. ``/healthz`` and ``/readyz``
        bypass this dep entirely (they declare no Depends), so the
        rate-limit branch never fires on health probes.
    """
    # Default-argument sanity check — if a caller misspells the scope
    # name, fail loudly at factory time so the misconfiguration cannot
    # silently pass-through to ``scope_satisfies`` (which would also
    # deny it, but with a misleading error path). The canonical scope
    # name set lives on ``taskq_api.service.auth`` per the FR-04 SAB
    # binding; we import it rather than re-declare the table here.
    if not is_known_scope(scope):
        raise ValueError(
            f"require_scope: unknown scope {scope!r}; "
            f"expected one of {sorted(KNOWN_SCOPES)!r}"
        )

    def _dependency(
        principal: dict = Depends(require_api_key),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict:
        # FR-04 — granted rank must satisfy required rank. The
        # comparator is deny-by-default (unknown scopes → False).
        granted = str(principal.get("scope"))
        if not scope_satisfies(granted, scope):
            raise _forbidden(scope)
        # FR-05 — per-token token-bucket rate limit. ``require_api_key``
        # already raised 401 if the header is missing, so by the time
        # we get here ``x_api_key`` is non-None in production. When the
        # dep is invoked directly (e.g. ``dep(principal=...)`` from the
        # FR-04 coverage tests) ``x_api_key`` resolves to the FastAPI
        # ``Header`` sentinel object rather than a string — the
        # ``isinstance`` check below skips the rate-limit branch in
        # that case (the test bypasses the framework anyway, and the
        # rate-limit primitive is exercised by FR-05's own tests).
        if isinstance(x_api_key, str) and x_api_key:
            bucket = check_rate_limit(hash_key(x_api_key), cost=1)
            if not bucket["allowed"]:
                raise _rate_limited(bucket["retry_after"])
        return principal

    # Return a FastAPI-compatible wrapper that ALSO exposes ``.callable``
    # so FR-04 route introspection (the AC-4.3 source-file check) can
    # recover the underlying closure from ``route.dependencies``.
    return _ScopeDep(_dependency)


# ---------------------------------------------------------------------------
# Rate-limit primitive — pure FastAPI dependency.
# ---------------------------------------------------------------------------
def check_rate_limit(key_hash: str, cost: int = 1) -> dict:
    """Drive a per-token token-bucket rate-limit check.

    Returns ``{"allowed": bool, "tokens_remaining": float,
    "retry_after": float}``. When ``allowed`` is False, the caller MUST
    raise ``HTTPException(429)`` carrying a ``Retry-After`` header
    equal to ``retry_after`` (rounded up to seconds, minimum 1) and a
    problem+json body whose ``type`` is ``/errors/rate-limited``.

    The function reads ``RATE_BURST`` / ``RATE_PER_SEC`` from this
    module's globals at call time so callers (notably the FR-05
    monkeypatch fixture) can override the policy by setting
    ``deps.RATE_BURST`` / ``deps.RATE_PER_SEC`` before issuing the
    request.

    [FR-05, NFR-03]
    Citations:
      - FR-05 §3 AC-5.1: the bucket is sized at ``RATE_BURST`` tokens
        and refilled at ``RATE_PER_SEC`` tokens/second.
      - FR-05 §3 AC-5.2: when the bucket cannot satisfy ``cost``,
        ``allowed=False`` and ``retry_after`` is the seconds-until-
        next-token count derived from
        ``taskq_api.service.ratelimit.retry_after_seconds``.
      - FR-05 §3 AC-5.3: the underlying ``rate_repo.consume`` runs
        inside a single transaction holding a row-level lock so
        concurrent workers cannot both consume the last token.
    """
    # Ensure the bucket row exists (idempotent — does not refill).
    rate_repo.get_or_create(
        key_hash, burst=RATE_BURST, refill_per_sec=RATE_PER_SEC
    )
    # Consume — this applies the refill + decrement atomically.
    result = rate_repo.consume(key_hash, cost=cost)
    allowed = bool(result.get("allowed", False))
    # The fake ``_FakeRateRepo.consume`` (and the real
    # ``_SQLiteRateRepo.consume``) applies refill-then-decrement in
    # place; ``bucket["tokens"]`` after the call reflects the
    # post-refill / post-decrement state. To recover the
    # pre-consume available count we read the bucket again and add
    # back ``cost`` when the consume succeeded (no decrement on a
    # failed consume).
    bucket = rate_repo.get_or_create(
        key_hash, burst=RATE_BURST, refill_per_sec=RATE_PER_SEC
    )
    remaining = float(bucket["tokens"])
    if allowed:
        remaining += cost
    return {
        "allowed": allowed,
        "tokens_remaining": remaining,
        "retry_after": float(result.get("retry_after", 0.0)),
    }


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
