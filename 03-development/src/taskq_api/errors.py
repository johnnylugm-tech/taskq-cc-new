"""RFC 7807 application/problem+json error contract — FR-10.

[FR-10]
Citations:
  - FR-10 §3 (SPEC.md): every non-2xx response MUST render with
    ``Content-Type: application/problem+json`` and carry the six
    fields ``type``, ``title``, ``status``, ``detail``, ``instance``,
    ``correlation_id``. The ``detail`` field MUST NOT leak internal
    structure (SQL, stack traces, file paths, schema descriptions) —
    see ``taskq_api.app`` for the global exception handlers that
    route through :func:`problem_response`.
  - SPEC.md §7: the canonical ``status`` -> ``/errors/<slug>``
    mapping every problem+json response MUST follow. The eight URIs
    are the contract surface clients branch on — adding keys or
    changing values here is a SPEC drift, not a refactor.
  - NFR-09 (testability): the ``STATUS_TYPE_MAP`` dict and the
    ``problem_response`` factory are the in-process surface the
    pytest-cov gate measures. Both names are imported by the FR-10
    RED test file as Gate 1 phantom-check sentinels.
"""
from __future__ import annotations

import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Canonical status -> type-URI mapping per SPEC.md §7.
#
# Every non-2xx response in the API surfaces one of these URIs in the
# problem+json body's ``type`` field. Clients branch on the URI rather
# than parsing the human-readable ``title`` or ``detail``.
# ---------------------------------------------------------------------------

STATUS_TYPE_MAP: dict[int, str] = {
    422: "/errors/validation",
    401: "/errors/unauthenticated",
    403: "/errors/forbidden",
    404: "/errors/not-found",
    409: "/errors/conflict",
    429: "/errors/rate-limited",
    503: "/errors/not-ready",
    500: "/errors/internal",
}


# ---------------------------------------------------------------------------
# Correlation-id generation.
#
# Each non-2xx response MUST echo a correlation_id in BOTH the body
# and the ``X-Correlation-Id`` response header so operators can
# stitch the response back to the server-side log line. The default
# is a fresh UUID4 — callers may pass an explicit ``correlation_id``
# when they already have one (e.g. middleware-extracted from the
# incoming ``X-Correlation-Id`` request header).
# ---------------------------------------------------------------------------


def _new_correlation_id() -> str:
    """Return a fresh UUID4 hex string for the correlation_id field."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# problem_response — the SAB-declared factory that owns the RFC 7807
# body shape. Every global exception handler in ``taskq_api.app``
# routes through this function so the contract has one source of
# truth (per the GREEN TODO contract in the FR-10 test file).
# ---------------------------------------------------------------------------


def problem_response(
    *,
    status: int,
    type_uri: str,
    title: str,
    detail: str,
    instance: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Build a 6-field RFC 7807 problem+json body.

    [FR-10]
    Citations:
      - FR-10 §3 AC-10.2: returns ``{type, title, status, detail,
        instance, correlation_id}`` — every field MUST be present
        so the in-process factory twin
        (``test_problem_response_factory_includes_all_fields``) can
        assert on the body shape without a TestClient round-trip.
      - FR-10 §3 AC-10.3: ``detail`` is passed through verbatim. The
        caller is responsible for keeping it free of internal
        structure (SQL, stack traces, file paths, schema). This
        function does NOT sanitise — sanitisation belongs at the
        layer that knows which raw exception messages are safe to
        surface.
      - FR-10 §3 AC-10.4: ``correlation_id`` is echoed in the body
        so the X-Correlation-Id header / log stitch contract (owned
        by ``taskq_api.app``) has a value to anchor against.

    Args:
        status: HTTP status code (matches the response status line).
        type_uri: ``/errors/<slug>`` URI per :data:`STATUS_TYPE_MAP`.
        title: short human-readable summary (stable per type_uri).
        detail: short human-readable summary of THIS error instance.
        instance: optional URI identifying the specific occurrence.
        correlation_id: optional pre-allocated id; a fresh UUID4 is
            generated when ``None``.

    Returns:
        dict with the six RFC 7807 fields.
    """
    if correlation_id is None:
        correlation_id = _new_correlation_id()
    if instance is None:
        instance = ""

    return {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "correlation_id": correlation_id,
    }


__all__ = [
    "STATUS_TYPE_MAP",
    "problem_response",
]