"""RED tests for FR-09 — Health checks and observability.

SAB binding for this FR (per ``.methodology/SAB.json``
``fr_module_traceability``):
    FR-09  ->  taskq_api.api.health         (/healthz, /readyz endpoints)
    FR-09  ->  taskq_api.service.metrics    (admin metrics body generator)

Gate 1's Architecture Amendment Protocol treats a missing declared
module as a phantom and BLOCKS the merge. The top-level imports below
MUST resolve once GREEN implements FR-09 — they are the contract the
implementation has to satisfy, not just convenient imports.

This file is intentionally RED. ``taskq_api.api.health`` does NOT yet
exist as a module (FR-09 reserves it; the current ``/healthz`` /
``/readyz`` stubs live inline in ``taskq_api.app`` and return a
text/plain ``"ok"`` body, NOT a JSON ``{"status": "ok"}`` body). And
``taskq_api.service.metrics`` does NOT yet exist either — the
``/v1/metrics`` body today is a stub text payload. Per the test
contract:

    "If pytest returns Exit Code 2 (Collection Error) due to missing
    modules, this is a VALID RED STATE. Do not try to 'fix' it by
    hiding the import."

Test cases match ``02-architecture/TEST_SPEC.md`` FR-09 exactly (names
are the single source of truth for ``spec-coverage-check``):
    1.  test_healthz_returns_200_ok                      (AC-9.1)
    2.  test_readyz_checks_db_and_migration_head         (AC-9.2)
    3.  test_readyz_fails_closed_on_old_migration        (AC-9.3)
    4.  test_metrics_requires_admin_scope                (AC-9.4)

GREEN TODO contract (must be implemented for these tests to pass):

    taskq_api.api.health
        healthz() -> JSONResponse with body ``{"status": "ok"}``
            and status_code 200. Endpoint MUST be exempt from auth
            (NFR-02 — no X-API-Key header required).
        readyz() -> JSONResponse
            Returns 200 ONLY when (a) the DB connection is reachable
            AND (b) ``alembic current`` equals head. Otherwise returns
            503 (type=``/errors/not-ready``) with the body identifying
            which condition failed ("db" or "migration"). Endpoint MUST
            be exempt from auth (NFR-02).
        Both handlers MUST be moved off the inline stubs in
        ``taskq_api.app`` so the FR-09 router owns the lifecycle.

    taskq_api.service.metrics
        metrics_payload() -> dict
            Returns a dict containing task counts by status
            (``pending``, ``running``, ``done``, ``failed``,
            ``timeout``), execution-latency percentiles (p50, p95,
            p99), and rate-limit rejection counts.
        /v1/metrics MUST continue to be admin-gated via
        ``require_scope("admin")`` so a read- or write-scope key
        receives HTTP 403 + problem+json before the handler runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# SAB binding — top-level imports per the test contract.
# RED: ModuleNotFoundError on `taskq_api.api.health` and on
# `taskq_api.service.metrics` is the expected failure mode for FR-09.
# Per the test contract this is a VALID RED STATE — pytest will return
# Exit Code 2 (Collection Error).
# ---------------------------------------------------------------------------

# GREEN TODO: `taskq_api.api.health` MUST expose a module at
# 03-development/src/taskq_api/api/health.py (or health/__init__.py).
# RED: ImportError because the module does not exist on disk yet.
import taskq_api.api.health as health_mod  # noqa: F401  (Gate 1 phantom check — FR-09 declared module)
# GREEN TODO: `taskq_api.service.metrics` MUST expose a module at
# 03-development/src/taskq_api/service/metrics.py (or metrics/__init__.py).
# RED: ImportError because the module does not exist on disk yet.
import taskq_api.service.metrics as svc_metrics_mod  # noqa: F401  (Gate 1 phantom check — FR-09 declared module)
from taskq_api.app import app  # noqa: F401  (Gate 1 phantom check — TestClient target)
from taskq_api.api import deps  # noqa: F401  (FR-09 inherits FR-04's auth wiring)


# ---------------------------------------------------------------------------
# Source-path constants — bind TEST_SPEC Inputs verbatim.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_HEALTH_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "api"
    / "health.py"
)
_SVC_METRICS_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "service"
    / "metrics.py"
)


# ---------------------------------------------------------------------------
# Fixtures — fake key repo + client wired with auth + repo overrides.
# Mirrors the FR-03 fixture pattern so the FR-09 /v1/metrics authz probe
# is the unit under test, not the FR-03 key_repo DB layer.
# ---------------------------------------------------------------------------


class _FakeKeyRepo:
    """In-memory stand-in for `taskq_api.repository.key_repo`.

    GREEN TODO: `taskq_api.repository.key_repo` must expose a `key_repo`
    singleton with the methods below. RED tests substitute this fake so
    the FR-09 metrics authz logic is the unit under test, not the DB layer.
    """

    def __init__(self) -> None:
        # hash -> row dict (with `revoked_at` ISO string or None).
        self.rows: dict[str, dict[str, Any]] = {}

    def create(self, scope: str, key_hash: str) -> dict[str, Any]:
        key_id = f"key-{len(self.rows) + 1}"
        self.rows[key_hash] = {
            "key_id": key_id,
            "scope": scope,
            "key_hash": key_hash,
            "revoked_at": None,
        }
        return self.rows[key_hash]

    def find_by_hash(self, key_hash: str) -> dict[str, Any] | None:
        return self.rows.get(key_hash)

    def revoke(self, key_hash: str, revoked_at: str) -> None:
        row = self.rows.get(key_hash)
        if row is not None:
            row["revoked_at"] = revoked_at


@pytest.fixture
def fake_key_repo() -> _FakeKeyRepo:
    return _FakeKeyRepo()


@pytest.fixture
def client(fake_key_repo, monkeypatch):
    """TestClient wired with auth + repo overrides.

    GREEN TODO: the real `taskq_api.repository.key_repo` MUST be
    SQLite-backed; the FR-03 wiring in `deps.key_repo` is unchanged.
    """
    import taskq_api.repository.key_repo as key_repo_mod

    # Wire the fake repo into both the repository module (canonical
    # singleton) and the deps module (which imports it directly).
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    return TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures — drive the FR-09 readyz checks (DB + migration head) in-process.
#
# FR-09 §3 AC-9.2 binds the /readyz contract to two readiness signals:
#   (a) the DB connection is reachable, AND
#   (b) `alembic current` equals head.
#
# The current inline /readyz stub in `taskq_api.app` does not consult
# either signal. GREEN will move the handler to `taskq_api.api.health`
# and add the checks. RED tests monkeypatch the future surface area on
# the health module so the assertions target the AC-9.2 contract — not
# the current stub.
# ---------------------------------------------------------------------------


@pytest.fixture
def readyz_signals(monkeypatch):
    """Patch the FR-09 readyz dependency surface on `taskq_api.api.health`.

    GREEN TODO: `taskq_api.api.health` MUST expose `check_db()` and
    `check_migration_head()` (or equivalent — names below are a contract
    surface the test asserts against) so /readyz can call them. RED
    tests install callables here so the assertion is on the AC-9.2
    contract — when GREEN implements the module, the callables below
    are replaced by the real implementations.

    Returns a mutable dict so per-test overrides can flip migration
    state between cases 2 (head) and 3 (old revision).
    """
    state = {
        "db_up": True,
        "migration_revision": "head",
    }

    def _check_db() -> bool:
        return state["db_up"]

    def _check_migration_head() -> str | None:
        # Returns the alembic current revision, or None on failure.
        return state["migration_revision"]

    # Install the patches so a missing `taskq_api.api.health` module
    # still lets the in-process /readyz stub in `taskq_api.app` resolve
    # the calls (the current stub ignores them, but monkeypatching
    # exercises the AC-9.2 contract surface explicitly).
    monkeypatch.setattr(
        health_mod, "check_db", _check_db, raising=False
    )
    monkeypatch.setattr(
        health_mod, "check_migration_head", _check_migration_head, raising=False
    )
    # Also patch on the current inline-handler site so a future move
    # to health.py doesn't silently drop coverage. The dual-target
    # monkeypatch is a soft contract — GREEN either honours the names
    # or rewires `taskq_api.app` to import them from the new module.
    monkeypatch.setattr(
        "taskq_api.app.check_db", _check_db, raising=False
    )
    monkeypatch.setattr(
        "taskq_api.app.check_migration_head", _check_migration_head, raising=False
    )

    return state


# ===========================================================================
# 1. test_healthz_returns_200_ok — AC-9.1
# ===========================================================================


# NFR-02 (security): /healthz is a public probe.
def test_healthz_returns_200_ok(client):  # NFR-02
    """AC-9.1: GET /healthz returns 200 with body ``{"status": "ok"}``.

    The TEST_SPEC Inputs bind:
        endpoint_path        = "/healthz"
        expected_status_code = "200"
        expected_body_field  = "status"
        expected_body_value  = "ok"

    No X-API-Key header is presented — the endpoint MUST be exempt
    from auth (NFR-02, FR-03 AC-3.6 cross-reference).

    Sub-assertions (TEST_SPEC §FR-09):
        AC1-status-200  expected_status_code == "200"
        AC1-body-ok     expected_body_value  == "ok"
    """
    endpoint_path = "/healthz"
    assert endpoint_path == "/healthz"

    # No X-API-Key — the endpoint MUST NOT require one.
    response = client.get(endpoint_path)

    # AC1-status-200: handler MUST return 200 OK while the process is alive.
    observed_status_code = str(response.status_code)
    assert observed_status_code == "200", (
        f"AC1-status-200 failed on {endpoint_path}: expected '200', "
        f"got {observed_status_code!r} body={response.text!r}"
    )

    # AC1-body-ok: body MUST be JSON with field "status" == "ok".
    expected_body_field = "status"
    expected_body_value = "ok"
    assert expected_body_field == "status"
    assert expected_body_value == "ok"

    body = response.json()
    assert expected_body_field in body, (
        f"AC1-body-ok failed: expected key {expected_body_field!r} in "
        f"body {body!r}"
    )
    assert body[expected_body_field] == expected_body_value, (
        f"AC1-body-ok failed: expected body[{expected_body_field!r}] == "
        f"{expected_body_value!r}, got {body.get(expected_body_field)!r}"
    )

    # Belt-and-braces: parse round-trip through json to ensure the
    # content-type really is application/json, not a text/plain stub
    # that happens to spell "ok".
    assert response.headers.get("content-type", "").startswith(
        "application/json"
    ), (
        f"expected /healthz content-type 'application/json', got "
        f"{response.headers.get('content-type')!r}"
    )

    # The current RED state surfaces here as a real assertion failure
    # (text/plain body does not parse as JSON and lacks the "status"
    # key). Once GREEN ships `taskq_api.api.health.healthz()` returning
    # `{"status": "ok"}`, this test turns green.


# ===========================================================================
# 2. test_readyz_checks_db_and_migration_head — AC-9.2
# ===========================================================================


# NP-07 (dependency fault): readyz fails closed when readiness signals fail.
def test_readyz_checks_db_and_migration_head(client, readyz_signals):  # NP-07
    """AC-9.2: GET /readyz returns 200 when DB is reachable AND migration at head.

    The TEST_SPEC Inputs bind:
        endpoint_path        = "/readyz"
        db_state             = "up"
        migration_revision   = "head"
        expected_status_code = "200"
        state_mode           = "isolate_per_test"

    `state_mode="isolate_per_test"` mandates function-scoped fixtures
    so each test starts with a fresh in-memory state — case 3 mutates
    `migration_revision` to "v1" and must not leak into case 2.

    Sub-assertions (TEST_SPEC §FR-09):
        AC2-ready-200  expected_status_code == "200"
    """
    endpoint_path = "/readyz"
    assert endpoint_path == "/readyz"

    db_state = "up"
    migration_revision = "head"
    expected_status_code = "200"
    assert db_state == "up"
    assert migration_revision == "head"
    assert expected_status_code == "200"

    # Configure the readiness signals for the "happy" path.
    readyz_signals["db_up"] = True
    readyz_signals["migration_revision"] = "head"

    response = client.get(endpoint_path)

    # AC2-ready-200: handler MUST return 200 when both signals are healthy.
    observed_status_code = str(response.status_code)
    assert observed_status_code == "200", (
        f"AC2-ready-200 failed on {endpoint_path}: expected '200', got "
        f"{observed_status_code!r} body={response.text!r}"
    )


# ===========================================================================
# 3. test_readyz_fails_closed_on_old_migration — AC-9.3
# ===========================================================================


# NP-07 (dependency fault): migration not at head must trigger 503.
def test_readyz_fails_closed_on_old_migration(client, readyz_signals):  # NP-07
    """AC-9.3: GET /readyz returns 503 (fail closed) when migration is NOT at head.

    Canonical phrasing (per the FR-09 brief): "deployment of new code
    without running migrations must fail closed". This is the deployment
    drift guardrail: the app MUST NOT report itself ready when its
    schema is older than the head migration the code expects.

    The TEST_SPEC Inputs bind:
        endpoint_path        = "/readyz"
        migration_revision   = "v1"           (old; NOT head)
        expected_status_code = "503"
        detail_mentions      = "migration"

    Sub-assertions (TEST_SPEC §FR-09):
        AC3-fail-closed-503     expected_status_code == "503"
        AC3-detail-names-cause  detail_mentions == "migration"
    """
    endpoint_path = "/readyz"
    assert endpoint_path == "/readyz"

    migration_revision = "v1"
    expected_status_code = "503"
    detail_mentions = "migration"
    assert migration_revision == "v1"
    assert expected_status_code == "503"
    assert detail_mentions == "migration"

    # DB is fine, but migration is stale — the handler MUST report 503.
    readyz_signals["db_up"] = True
    readyz_signals["migration_revision"] = "v1"

    response = client.get(endpoint_path)

    # AC3-fail-closed-503: handler MUST return 503, NOT 200.
    observed_status_code = str(response.status_code)
    assert observed_status_code == "503", (
        f"AC3-fail-closed-503 failed on {endpoint_path}: expected '503', "
        f"got {observed_status_code!r} body={response.text!r}"
    )

    # AC3-detail-names-cause: the body MUST identify "migration" as
    # the failing condition so operators can debug the deployment
    # drift (the canonical phrasing calls out "migration not at head").
    body_text = response.text.lower()
    assert detail_mentions in body_text, (
        f"AC3-detail-names-cause failed: expected substring "
        f"{detail_mentions!r} in response body {response.text!r}"
    )

    # The body MUST be a structured problem+json dict with type
    # /errors/not-ready per SPEC §3 FR-09 AC-9.2.
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {}
    problem_type = body.get("type", "")
    assert problem_type == "/errors/not-ready", (
        f"expected problem+json type '/errors/not-ready', got "
        f"{problem_type!r} (full body: {body!r})"
    )


# ===========================================================================
# 4. test_metrics_requires_admin_scope — AC-9.4
# ===========================================================================


# NP-02 (authz 403): insufficient scope is the unit under test.
def test_metrics_requires_admin_scope(client, fake_key_repo):  # NP-02
    """AC-9.4: GET /v1/metrics requires admin scope (read scope → 403).

    The TEST_SPEC Inputs bind:
        endpoint_path        = "/v1/metrics"
        caller_scope         = "read"
        expected_status_code = "403"

    GREEN TODO: the existing ``/v1/metrics`` router in
    ``taskq_api.api.metrics`` already gates via ``require_scope("admin")``
    — FR-09 inherits that wiring. The body of the metrics payload is
    delivered by ``taskq_api.service.metrics`` (the SAB-bound module
    declared for FR-09). The authz gate is the contract this test
    pins down; the body shape is deferred to FR-09's GREEN.

    Sub-assertions (TEST_SPEC §FR-09):
        AC4-metrics-403  expected_status_code == "403"
    """
    endpoint_path = "/v1/metrics"
    caller_scope = "read"
    expected_status_code = "403"
    assert endpoint_path == "/v1/metrics"
    assert caller_scope == "read"
    assert expected_status_code == "403"

    # Seed the fake repo with a read-scope key. SHA-256 hashing is the
    # FR-03 primitive — the same hashing path is reused here so the
    # auth wiring is exercised end-to-end.
    plaintext_key = "tk_fr09_metrics_read_xyz"
    fake_key_repo.create(
        scope=caller_scope, key_hash=deps.hash_key(plaintext_key)
    )

    response = client.get(
        endpoint_path, headers={"X-API-Key": plaintext_key}
    )

    # AC4-metrics-403: read-scope caller MUST be rejected with 403.
    observed_status_code = str(response.status_code)
    assert observed_status_code == "403", (
        f"AC4-metrics-403 failed on {endpoint_path}: expected '403', "
        f"got {observed_status_code!r} body={response.text!r}"
    )
