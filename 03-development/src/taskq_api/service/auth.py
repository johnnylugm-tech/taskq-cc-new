"""API-key hashing + constant-time comparison service.

[FR-03, NFR-02, NFR-04]
Citations:
  - FR-03 §3 (SPEC.md): API keys are stored as SHA-256 hashes;
    comparison MUST use ``hmac.compare_digest`` (constant-time).
  - NFR-02 (security): the only comparison primitive authorised for
    credential matching is ``hmac.compare_digest``; a naive ``==`` on
    key material is forbidden.
  - NFR-04 (security): plaintext keys must never appear in logs,
    metrics, or persisted state.
"""
from __future__ import annotations

import hashlib
import hmac


def hash_key(plaintext: str) -> str:
    """Return the 64-char lowercase hex SHA-256 digest of ``plaintext``.

    [FR-03, NFR-02]
    Citations:
      - FR-03 §3 AC-3.2: keys are stored as SHA-256 hashes — plaintext
        is never persisted.
    """
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return digest


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
    if not plaintext or not stored_hash:
        return False
    expected = hash_key(plaintext)
    return hmac.compare_digest(expected, stored_hash)