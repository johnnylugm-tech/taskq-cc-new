"""Token-bucket rate-limit policy — FR-05.

The runtime token-bucket refill / consume policy lives here so the
``taskq_api.repository.rate_repo`` module is only the persistence
adapter (the SELECT-FOR-UPDATE row lock) and the dep tree can read
the policy in isolation.

The policy is the canonical SPEC §3 FR-05 contract:

    refill: tokens = min(burst, tokens + (now - last_refill_ts) * refill_per_sec)
    consume: tokens >= cost  -> tokens -= cost, allowed=True
             else             -> allowed=False, retry_after = deficit / refill_per_sec

[FR-05, NFR-03]
Citations:
  - FR-05 §3 AC-5.1: per-token token bucket with capacity
    ``TASKQ_RATE_BURST`` and refill rate ``TASKQ_RATE_PER_SEC``.
  - FR-05 §3 AC-5.2: when the bucket cannot satisfy ``cost``, the
    caller renders HTTP 429 with a ``Retry-After`` header derived from
    ``deficit / refill_per_sec`` (rounded up to seconds, minimum 1).
  - FR-05 §3 AC-5.3: the policy is engine-agnostic — the persistence
    layer (rate_repo) is responsible for wrapping it in a row-level
    transaction.
  - NFR-03 (reliability): the bucket is bounded above by ``burst``
    (P5-bucket-cap) and never reports a negative balance
    (P5-bucket-bounded).
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Configuration — default bucket parameters. The runtime values come from
# ``taskq_api.api.deps.RATE_BURST`` / ``RATE_PER_SEC`` so tests can monkey-
# patch them without importing this module. Defaults match SPEC §3 FR-05:
# burst = 20, refill = 5 tokens / second.
# ---------------------------------------------------------------------------


DEFAULT_BURST: int = 20
DEFAULT_REFILL_PER_SEC: float = 5.0


def refill(
    tokens: float,
    last_refill_ts: float,
    *,
    now: float,
    burst: int,
    refill_per_sec: float,
) -> float:
    """Apply the token-bucket refill policy.

    Returns the post-refill token count, clamped to ``burst``.

    [FR-05]
    Citations:
      - FR-05 §3 AC-5.1: ``tokens = min(burst, tokens + (now -
        last_refill_ts) * refill_per_sec)``. The clamp at ``burst``
        implements P5-bucket-cap.
    """
    elapsed = max(0.0, float(now) - float(last_refill_ts))
    return min(float(burst), float(tokens) + elapsed * float(refill_per_sec))


def retry_after_seconds(deficit: float, refill_per_sec: float) -> float:
    """Compute the seconds-until-next-token count for a failed consume.

    Returns ``deficit / refill_per_sec``; when ``refill_per_sec`` is
    zero (degenerate policy), returns 1.0 so the HTTP layer can always
    emit a positive ``Retry-After`` integer.

    [FR-05, NFR-03]
    Citations:
      - FR-05 §3 AC-5.2: the 429 path requires a positive
        ``Retry-After`` header in seconds; this helper is the canonical
        mapping from bucket deficit to that header value.
    """
    if not refill_per_sec:
        return 1.0
    return float(deficit) / float(refill_per_sec)
