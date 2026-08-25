"""Rate-bucket repository — FR-05.

Persists per-token token-bucket state in the database so two workers
cannot both consume the last token. The bucket row is selected with
``SELECT ... FOR UPDATE`` (SQLAlchemy ``with_for_update``) so the
read-modify-write cycle holds a row-level lock for the duration of the
update transaction (AC-5.3 / NFR-03).

The public surface (``get_or_create``, ``consume``) is intentionally
shaped like the FR-03 ``key_repo`` so the FR-05 tests can swap in an
in-process fake via ``monkeypatch.setattr(deps, "rate_repo", ...)``
without rewiring their assertions.

[FR-05, FR-06, NFR-03]
Citations:
  - FR-05 §3 AC-5.3: bucket state MUST live in the database and MUST be
    updated within a single transaction holding a row-level lock so two
    concurrent workers cannot both consume the last token. The lock is
    acquired via ``select(...).with_for_update`` inside
    ``session.begin()`` so the row-level lock holds for the duration
    of the read-modify-write cycle.
  - FR-05 §3 AC-5.1 / AC-5.2: ``consume`` applies the token-bucket
    refill (``min(burst, tokens + elapsed * refill_per_sec)``) and
    decrements by ``cost`` when ``tokens >= cost``; otherwise it
    computes ``retry_after = deficit / refill_per_sec`` so the caller
    can render an HTTP ``Retry-After`` header (in seconds, ceil ≥ 1).
  - FR-06: the SQLite-backed implementation lives here; FR-05 tests
    swap this attribute for an in-memory fake.
  - NFR-03 (reliability): the row-level lock is the FR-05 mitigation
    for risk R12 in TRACEABILITY_MATRIX.md §5 ("rate bucket 競態導致
    超放行").
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import Float, Integer, String, create_engine, select
from sqlalchemy.orm import Mapped, Session, declarative_base, mapped_column

from taskq_api.service.ratelimit import refill, retry_after_seconds


# ---------------------------------------------------------------------------
# ORM model — the ``rate_buckets`` table.
# ---------------------------------------------------------------------------

_Base = declarative_base()


class RateBucket(_Base):  # type: ignore[misc, valid-type]
    """Persistent representation of a per-token token bucket.

    [FR-05]
    Citations:
      - FR-05 §3 AC-5.3: one row per ``key_hash``; the row's ``tokens``
        and ``last_refill_ts`` fields are read+updated under a
        row-level lock per consume.
    """

    __tablename__ = "rate_buckets"

    # Mapped[...] + mapped_column give pyright accurate attribute types
    # (str / float / int) instead of the legacy ``Column[T]`` descriptors
    # which static analysers see as the Column object itself rather than
    # the runtime-mapped value type.
    key_hash: Mapped[str] = mapped_column(String, primary_key=True)
    tokens: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    burst: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    refill_per_sec: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    last_refill_ts: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


# ---------------------------------------------------------------------------
# SQLite-backed implementation.
# ---------------------------------------------------------------------------


class _SQLiteRateRepo:
    """SQLite-backed rate-bucket repository.

    [FR-05, FR-06]
    Citations:
      - FR-05 §3 AC-5.3: every ``consume`` runs inside a single
        transaction; the SELECT that reads the bucket holds a row-level
        lock (``with_for_update``) until the UPDATE commits so two
        workers cannot both consume the last token.
      - FR-06: this is the production persistence adapter; the
        in-memory fake the FR-05 tests inject mirrors its surface.
    """

    def __init__(self, db_url: str = "sqlite:///:memory:") -> None:
        self.engine = create_engine(db_url)
        _Base.metadata.create_all(self.engine)

    def get_or_create(
        self,
        key_hash: str,
        *,
        burst: int,
        refill_per_sec: float,
    ) -> dict[str, float]:
        """Fetch the bucket row or create it at the requested capacity.

        [FR-05]
        Citations:
          - FR-05 §3 AC-5.1: a fresh bucket starts at ``burst`` tokens
            so the first request sees a full quota (TEST_SPEC §FR-05
            AC1-initial-cap).
        """
        with Session(self.engine) as session:
            with session.begin():
                row = session.execute(
                    select(RateBucket).where(RateBucket.key_hash == key_hash)
                ).scalar_one_or_none()
                if row is None:
                    row = RateBucket(
                        key_hash=key_hash,
                        tokens=float(burst),
                        burst=int(burst),
                        refill_per_sec=float(refill_per_sec),
                        last_refill_ts=time.monotonic(),
                    )
                    session.add(row)
                return {
                    "tokens": float(row.tokens),
                    "burst": float(row.burst),
                    "refill_per_sec": float(row.refill_per_sec),
                    "last_refill_ts": float(row.last_refill_ts),
                }

    def consume(self, key_hash: str, *, cost: int) -> dict[str, Any]:
        """Atomically refill + decrement the bucket under a row-level lock.

        [FR-05, NFR-03]
        Citations:
          - FR-05 §3 AC-5.3: SELECT ... FOR UPDATE (``with_for_update``)
            inside ``session.begin()`` — the row lock holds until the
            UPDATE commits, so two concurrent workers cannot both
            consume the last token.
          - FR-05 §3 AC-5.1: refill is ``min(burst, tokens + elapsed *
            refill_per_sec)``; the bucket is bounded above by
            ``burst`` (P5-bucket-cap).
          - FR-05 §3 AC-5.2: when the bucket cannot satisfy ``cost``,
            ``retry_after = deficit / refill_per_sec`` so the caller
            can populate the ``Retry-After`` header (ceil ≥ 1 second).
          - NFR-03 (reliability): R12 mitigation per
            TRACEABILITY_MATRIX.md §5 — the row-level lock prevents
            concurrent over-issuance.
        """
        with Session(self.engine) as session:
            with session.begin():
                row = session.execute(
                    select(RateBucket)
                    .where(RateBucket.key_hash == key_hash)
                    .with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    # Conservative default if consume is called on a
                    # key that get_or_create hasn't warmed yet — the
                    # next call to get_or_create will refresh burst /
                    # refill_per_sec from the caller-supplied policy.
                    row = RateBucket(
                        key_hash=key_hash,
                        tokens=0.0,
                        burst=20,
                        refill_per_sec=5.0,
                        last_refill_ts=time.monotonic(),
                    )
                    session.add(row)
                    session.flush()

                now = time.monotonic()
                refill_per_sec = float(row.refill_per_sec)
                burst_capacity = float(row.burst)
                # FR-05 §3 AC-5.1: apply the canonical refill policy
                # via ``taskq_api.service.ratelimit.refill`` so the
                # repository is the persistence adapter only — the
                # bucket math lives in one place.
                new_tokens = refill(
                    tokens=float(row.tokens),
                    last_refill_ts=float(row.last_refill_ts),
                    now=now,
                    burst=int(row.burst),
                    refill_per_sec=refill_per_sec,
                )

                if new_tokens >= cost:
                    new_tokens -= cost
                    allowed = True
                    retry_after = 0.0
                else:
                    # FR-05 §3 AC-5.2: positive Retry-After in seconds;
                    # the helper handles the degenerate zero-refill
                    # policy (returns 1.0) so the HTTP layer always
                    # sees a positive value.
                    retry_after = retry_after_seconds(
                        deficit=cost - new_tokens,
                        refill_per_sec=refill_per_sec,
                    )
                    allowed = False

                row.tokens = new_tokens
                row.last_refill_ts = now

                return {
                    "allowed": allowed,
                    "retry_after": retry_after,
                    "tokens": new_tokens,
                    "burst": burst_capacity,
                    "refill_per_sec": refill_per_sec,
                    "last_refill_ts": now,
                }


# Module-level singleton — FR-05 tests swap this attribute for an
# in-memory fake via ``monkeypatch.setattr(deps, "rate_repo", ...)``.
rate_repo = _SQLiteRateRepo()
