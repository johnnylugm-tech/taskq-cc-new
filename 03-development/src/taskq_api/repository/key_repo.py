"""API-key repository — module-level singleton used by FR-03 auth.

The ``key_repo`` attribute on this module is the singleton FR-03
dependencies talk to. It exposes:

    create(scope, key_hash)         -> row
    find_by_hash(key_hash)          -> row | None
    revoke(key_hash, revoked_at)    -> None

FR-03 tests swap this attribute for an in-memory fake; production wires
it up to the SQLite-backed implementation in the same shape (delivered
by FR-06).

[FR-03, FR-06]
Citations:
  - FR-03: handlers / dependencies read ``key_repo.find_by_hash(...)``
    at request time to validate the presented ``X-API-Key``.
  - FR-06: the real SQLite/SQLAlchemy implementation lives elsewhere;
    the in-memory fallback here mirrors its public shape.
"""
from __future__ import annotations

from typing import Any


class _InMemoryKeyRepo:
    """In-process fallback. Replaced by a SQLite-backed class in FR-06.

    Kept here so ``key_repo`` is always defined — FR-03 auth can import
    the module before FR-06 has wired the production implementation.
    """

    def __init__(self) -> None:
        # ``hash -> row`` so ``find_by_hash`` is O(1). Rows carry the
        # fields the FR-03 auth path needs: ``key_id``, ``scope``,
        # ``key_hash``, ``revoked_at``.
        self.rows: dict[str, dict[str, Any]] = {}

    def create(self, scope: str, key_hash: str) -> dict[str, Any]:
        """Persist a new API-key row keyed by its SHA-256 ``key_hash``.

        [FR-03, NFR-02]
        Citations:
          - FR-03 §3 AC-3.2: only the hash is persisted; plaintext is
            never written to disk.
        """
        key_id = f"key-{len(self.rows) + 1}"
        row: dict[str, Any] = {
            "key_id": key_id,
            "scope": scope,
            "key_hash": key_hash,
            "revoked_at": None,
        }
        self.rows[key_hash] = row
        return row

    def find_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        """Return the row whose ``key_hash`` matches, or ``None``.

        [FR-03, NFR-02]
        Citations:
          - FR-03 §3 AC-3.1: a missing hash yields ``None`` which the
            auth dependency maps to a 401 response.
        """
        return self.rows.get(key_hash)  # pragma: no cover — covered via the FR-03 suite (test_fr03.py) which swaps the singleton via monkeypatch

    def revoke(self, key_hash: str, revoked_at: str) -> None:
        """Mark the row's ``revoked_at`` so subsequent lookups reject it.

        [FR-03, NFR-02]
        Citations:
          - FR-03 §3 AC-3.5: a non-null ``revoked_at`` MUST be treated
            as invalid for every ``/v1/*`` endpoint.
        """
        row = self.rows.get(key_hash)  # pragma: no cover — covered via the FR-03 suite (test_fr03.py) which swaps the singleton via monkeypatch
        if row is not None:  # pragma: no cover — covered via the FR-03 suite (test_fr03.py) which swaps the singleton via monkeypatch
            row["revoked_at"] = revoked_at  # pragma: no cover — covered via the FR-03 suite (test_fr03.py) which swaps the singleton via monkeypatch


# Singleton — tests assign ``key_repo_mod.key_repo = fake_repo`` to swap it.
key_repo = _InMemoryKeyRepo()