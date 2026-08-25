"""FR-09 admin metrics body generator.

[FR-09]
Citations:
  - FR-09 §3 AC-9.4: ``GET /v1/metrics`` is admin-gated via
    ``require_scope("admin")`` and the response body reports
    (a) task counts grouped by status (``pending``, ``running``,
    ``done``, ``failed``, ``timeout``), (b) execution-latency
    percentiles (``p50``, ``p95``, ``p99``), and (c) the number of
    rate-limit rejections observed since process start.

    The function lives in ``service/`` rather than ``api/`` so the
    metrics body can be reused from background jobs, admin scripts,
    and tests without dragging the FastAPI dependency tree along.
    The ``/v1/metrics`` route in ``taskq_api.api.metrics`` consumes
    this payload as its admin-gated handler body.

    NFR-04 (security/redaction): the payload MUST NOT contain DSN
    fragments, API keys, or any other secret material. The shape
    here is intentionally aggregate-only.
"""

# pragma: no error-handling

from __future__ import annotations

from typing import Any


# Status buckets the FR-09 AC-9.4 contract requires. Tuple order is
# preserved in the JSON response so operators see a stable shape.
TASK_STATUSES: tuple[str, ...] = ("pending", "running", "done", "failed", "timeout")

# Latency percentile labels reported under ``latency_ms``. Tuple
# order is preserved so charting clients see a stable shape.
LATENCY_PERCENTILES: tuple[str, ...] = ("p50", "p95", "p99")


def _task_counts() -> dict[str, int]:
    """Return ``{status: count}`` for every status in ``TASK_STATUSES``.

    Real counts land once FR-06 wires the task_repository; this stub
    returns zero for each status so the response shape is stable from
    process start (operators can chart the keys without conditional
    rendering on the client side).
    """
    return {status: 0 for status in TASK_STATUSES}


def _latency_percentiles() -> dict[str, int]:
    """Return ``{percentile: ms}`` for every percentile in ``LATENCY_PERCENTILES``.

    Real percentiles land once FR-07 wires the run_history source;
    this stub returns zero so the response shape is stable.
    """
    return {percentile: 0 for percentile in LATENCY_PERCENTILES}


def _rate_limit_rejections() -> int:
    """Return the cumulative rate-limit rejection count.

    Real counter lands once FR-05 wires a rejection counter alongside
    the bucket state; this stub returns zero so the response shape
    is stable from process start.
    """
    return 0


def metrics_payload() -> dict[str, Any]:
    """Return the admin-only metrics body for FR-09 AC-9.4.

    [FR-09]
    Citations:
      - FR-09 §3 AC-9.4: the dict MUST include task counts grouped
        by status (pending/running/done/failed/timeout),
        execution-latency percentiles (p50/p95/p99), and the
        rate-limit rejection count.
      - NFR-04: the body MUST NOT include DSN fragments, plaintext
        keys, or any other secret material — counts and
        percentiles only.

    Returns a fully-populated dict with zero-valued placeholders so
    the response shape is stable from process start (operators can
    chart the keys without conditional rendering on the client
    side). The real counts/percentiles land once FR-06 / FR-07 wire
    the task_repository and rate_repository sources.
    """
    return {
        "tasks": _task_counts(),
        "latency_ms": _latency_percentiles(),
        "rate_limit_rejections": _rate_limit_rejections(),
    }