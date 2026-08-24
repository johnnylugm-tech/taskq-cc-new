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
from __future__ import annotations

from typing import Any


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
        "tasks": {
            "pending": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
            "timeout": 0,
        },
        "latency_ms": {
            "p50": 0,
            "p95": 0,
            "p99": 0,
        },
        "rate_limit_rejections": 0,
    }
