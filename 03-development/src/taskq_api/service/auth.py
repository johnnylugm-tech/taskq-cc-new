"""API-key hashing + constant-time comparison service.

[FR-03, FR-04, NFR-02, NFR-04]
Citations:
  - FR-03 §3 (SPEC.md): API keys are stored as SHA-256 hashes;
    comparison MUST use ``hmac.compare_digest`` (constant-time).
  - FR-04 §3 (SPEC.md): scope hierarchy ``read`` < ``write`` < ``admin``
    (inclusive). The comparator lives here so the runtime / spec-level
    single source of truth is the same module (``taskq_api.service.auth``)
    that owns credential primitives.
  - NFR-02 (security): the only comparison primitive authorised for
    credential matching is ``hmac.compare_digest``; a naive ``==`` on
    key material is forbidden.
  - NFR-04 (security): plaintext keys must never appear in logs,
    metrics, or persisted state.
"""
from __future__ import annotations

import hashlib
import hmac


# Scope rank — higher rank satisfies a lower one. Defined at module
# import so ``scope_satisfies`` does no allocation per call.
_SCOPE_RANK: dict[str, int] = {"read": 0, "write": 1, "admin": 2}

# Canonical list of recognised scopes. Exposed so callers (e.g. the
# ``require_scope`` factory in ``taskq_api.api.deps``) can validate a
# caller-supplied scope name without re-declaring the rank table.
KNOWN_SCOPES: tuple[str, ...] = tuple(_SCOPE_RANK)


def is_known_scope(scope: str) -> bool:
    """Return True iff ``scope`` is one of the ranked scopes.

    [FR-04]
    Citations:
      - FR-04: this module is the single source of truth for the
        scope name set; anything that needs to validate a caller-
        supplied scope (e.g. ``require_scope`` factory) must call
        here rather than re-declare the table.
    """
    return scope in _SCOPE_RANK


def scope_satisfies(granted: str, required: str) -> bool:
    """Return True iff ``granted`` outranks or equals ``required``.

    [FR-04, NFR-02]
    Citations:
      - FR-04 §3 AC-4.1: ``admin`` ⊇ ``write`` ⊇ ``read``. The
        inclusive hierarchy is implemented as rank ≥ comparison.
      - NFR-02 (security): deny-by-default — any unknown scope
        (typo, empty string, future scope not yet ranked) returns
        ``False`` so a malformed principal can never bypass authz.
    """
    granted_rank = _SCOPE_RANK.get(granted, -1)
    required_rank = _SCOPE_RANK.get(required, -1)
    if granted_rank < 0 or required_rank < 0:
        return False
    return granted_rank >= required_rank


def hash_key(plaintext: str) -> str:
    """Return the 64-char lowercase hex SHA-256 digest of ``plaintext``.

    [FR-03, NFR-02]
    Citations:
      - FR-03 §3 AC-3.2: keys are stored as SHA-256 hashes — plaintext
        is never persisted.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def compare_keys(plaintext: str, stored_hash: str) -> bool:
    """Constant-time comparison of ``plaintext`` against ``stored_hash``.

    Uses ``hmac.compare_digest`` per NFR-02; falls back to ``False``
    (NOT a raise) when the inputs are malformed so the caller can
    surface a uniform 401 without leaking why the comparison failed.

    [FR-03, NFR-02]
    Citations:
      - FR-03 §3 AC-3.3: comparison is constant-time via
        ``hmac.compare_digest``.
      - NFR-02 (security): naive ``==`` on key material is forbidden.
    """
    if not plaintext or not stored_hash:  # early-exit branch for malformed inputs; covered by FR-03 auth test suite
        return False
    return hmac.compare_digest(hash_key(plaintext), stored_hash)