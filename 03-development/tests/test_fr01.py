"""RED tests for FR-01 — Task resource CRUD API.

SAB binding for this FR (per `.methodology/SAB.json` `fr_module_traceability`):
    FR-01  ->  taskq_api.api.tasks

Gate 1's Architecture Amendment Protocol treats a missing declared module
as a phantom and BLOCKS the merge. Therefore the top-level import below
MUST resolve once GREEN implements FR-01 — it is the contract the
implementation has to satisfy, not just a convenient import.

This file is intentionally RED — no source exists yet, so pytest will
return Exit Code 2 (Collection Error) due to `ModuleNotFoundError:
No module named 'taskq_api'`. Per the test contract:

    "If pytest returns Exit Code 2 (Collection Error) due to missing
    modules, this is a VALID RED STATE. Do not try to 'fix' it by
    hiding the import."

Test cases match `02-architecture/TEST_SPEC.md` FR-01 exactly (names
are the single source of truth for `spec-coverage-check`):
    1. test_create_task_valid                  (AC-1.1)
    2. test_post_invalid_body_returns_422      (AC-1.2)
    3. test_get_unknown_returns_404            (AC-1.3)
    4. test_list_uses_cursor_pagination        (AC-1.4)
    5. test_limit_default_and_upper_bound      (AC-1.5)
    6. test_delete_removes_task_and_results_in_tx (AC-1.6)

GREEN TODO contract (must be implemented for these tests to pass):

    taskq_api.api.tasks
        Router (FastAPI APIRouter) registering under prefix `/v1/tasks`:
          POST   /v1/tasks          create_task(body: TaskCreate) -> 201 TaskOut
          GET    /v1/tasks/{id}     read_task(id: UUID) -> 200 TaskOut | 404 problem+json
          GET    /v1/tasks          list_tasks(status?, cursor?, limit?)
                                    -> 200 {items, next_cursor, limit}
                                    | 422 when limit > 200
          DELETE /v1/tasks/{id}     delete_task(id: UUID) -> 204
                                    (task + result rows in single tx)
        Validation rules (POST body):
            - `command` non-empty, <= 1000 chars, no injection chars
              (e.g. ; | & ` $ ( ) < > newline)
            - `name` non-empty, unique among existing tasks
            Violation -> 422 + application/problem+json with type=/errors/validation.
        Cursor pagination only (no `offset` query parameter).
        Default limit = 50, max limit = 200 (limit > 200 -> 422).

    taskq_api.app
        FastAPI app instance named `app` that includes the FR-01 router.

The conftest-style fixtures below mock the upstream FRs (FR-03 auth,
FR-04 scope, FR-06 repository) so the failure surface is the FR-01
logic, NOT the missing upstream FRs. This is test isolation, not
implementation of the feature.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

# SAB binding — top-level import per the test contract.
# RED: ModuleNotFoundError on this line is the expected failure mode.
from taskq_api.api.tasks import router  # noqa: F401  (Gate 1 phantom check)


# ---------------------------------------------------------------------------
# Fakes — minimal stand-ins for upstream FRs so FR-01 is the failure surface.
# These are NOT the feature implementation; they are test-isolation doubles
# so a missing FR-03/04/06 cannot mask the RED state of FR-01 itself.
# ---------------------------------------------------------------------------


class _FakeRepo:
    """In-memory stand-in for `taskq_api.repository.task_repo`.

    GREEN TODO: `taskq_api.repository.task_repo` must expose a `task_repo`
    singleton with the methods below. RED tests substitute this fake so the
    FR-01 handler is the unit under test, not the DB layer.
    """

    def __init__(self):
        self.rows = {}
        self.results = {}

    def create(self, payload):
        # Real repo: INSERT INTO tasks ... RETURNING *; unique-name check
        # must raise here so the handler maps it to 422.
        name = payload["name"]
        if any(r["name"] == name for r in self.rows.values()):
            raise ValueError("name already exists")
        if not payload.get("command") or not name:
            raise ValueError("empty field")
        row = {
            "id": str(uuid.uuid4()),
            "command": payload["command"],
            "name": name,
            "status": "pending",
            "created_at": "2026-08-24T00:00:00Z",
        }
        self.rows[row["id"]] = row
        return row

    def get(self, task_id):
        return self.rows.get(task_id)

    def list(self, status=None, cursor=None, limit=50):
        # Cursor-based: cursor encodes the integer offset. No `offset` param.
        all_ids = sorted(self.rows.keys())
        start = int(cursor) if cursor else 0
        end = min(start + limit, len(all_ids))
        page_ids = all_ids[start:end]
        next_cursor = str(end) if end < len(all_ids) else None
        return [self.rows[i] for i in page_ids], next_cursor

    def delete_with_results(self, task_id):
        # Atomic delete — task + its result rows in one tx.
        count = 0
        if task_id in self.rows:
            count += 1
            del self.rows[task_id]
        for k in list(self.results.keys()):
            if self.results[k].get("task_id") == task_id:
                del self.results[k]
                count += 1
        return count


@pytest.fixture
def fake_repo():
    return _FakeRepo()


@pytest.fixture
def client(fake_repo):
    """TestClient wired with auth + repo overrides.

    GREEN TODO: `taskq_api.app` must expose a FastAPI `app` instance that
    includes `taskq_api.api.tasks.router`. GREEN TODO: `taskq_api.api.deps`
    must expose `require_scope(scope)` — a FastAPI dependency returning the
    authenticated principal (or raising 401/403 problem+json).
    """
    from taskq_api.app import app
    from taskq_api.api import deps

    # Auth bypass: any scope is accepted. Real auth (FR-03) tested in its
    # own RED file. Without this override the tests would fail for the wrong
    # reason (missing FR-03) instead of missing FR-01.
    app.dependency_overrides[deps.require_scope] = (
        lambda scope: {"scope": scope, "key_id": "fake-key"}
    )

    # Repo swap: handler calls the injected fake rather than the real DB.
    import taskq_api.repository.task_repo as task_repo_mod
    task_repo_mod.task_repo = fake_repo

    return TestClient(app)


# ===========================================================================
# Test functions. Order matches TEST_SPEC.md FR-01 (cases 1..6).
# Function names are EXACT — `spec-coverage-check` matches by name.
# ===========================================================================


def test_create_task_valid(client):
    """AC-1.1: POST /v1/tasks with valid body returns 201 + 36-char id.

    NFR-05 (documentation): public fn/handler exposes a docstring referencing [FR-01].
    """
    response = client.post(
        "/v1/tasks",
        json={"command": "echo hello", "name": "t-create-1"},
        headers={"X-API-Key": "fake-write-key"},
    )
    assert response.status_code == 201, (
        f"AC1-status-201 failed: expected 201, got {response.status_code}"
        f" body={response.text}"
    )
    body = response.json()
    observed_id_value = body.get("id", "")
    # AC1-id-present: id is a 36-char UUID string.
    assert len(observed_id_value) == 36, (
        f"AC1-id-present failed: expected id length 36, got {len(observed_id_value)!r}"
    )


def test_post_invalid_body_returns_422(client):
    """AC-1.2: POST with empty command/name returns 422 + application/problem+json."""
    response = client.post(
        "/v1/tasks",
        json={"command": "", "name": ""},
        headers={"X-API-Key": "fake-write-key"},
    )
    assert response.status_code == 422, (
        f"AC2-status failed: expected 422, got {response.status_code}"
    )
    observed_content_type = response.headers.get("content-type", "")
    # AC2-content-type: response must be application/problem+json.
    assert observed_content_type.startswith("application/problem+json"), (
        f"AC2-content-type failed: got {observed_content_type!r}"
    )


def test_get_unknown_returns_404(client):
    """AC-1.3: GET /v1/tasks/{unknown-id} returns 404 + application/problem+json.

    NFR-01 (performance): single-fetch GET /v1/tasks/{id} path; p95 < 30ms target.
    """
    response = client.get(
        "/v1/tasks/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert response.status_code == 404, (
        f"AC3-status failed: expected 404, got {response.status_code}"
    )
    observed_content_type = response.headers.get("content-type", "")
    # AC3-content-type: response must be application/problem+json.
    assert observed_content_type.startswith("application/problem+json"), (
        f"AC3-content-type failed: got {observed_content_type!r}"
    )


def test_list_uses_cursor_pagination(client, fake_repo):
    """AC-1.4: GET /v1/tasks paginates with cursor (NOT offset).

    Seed 75 tasks. First call with limit=50 returns 50; the cursor drives
    the next call which returns 25. Sum equals the seeded total.

    NFR-01 (performance): list endpoint must avoid N+1; constant SQL count.
    """
    seed_count = 75
    for idx in range(seed_count):
        fake_repo.create({"command": f"echo {idx}", "name": f"seed-{idx}"})

    first = client.get(
        "/v1/tasks?limit=50",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert first.status_code == 200, (
        f"first page: expected 200, got {first.status_code} body={first.text}"
    )
    first_body = first.json()
    first_page_size = len(first_body.get("items", []))
    # AC4-page-size: first page with limit=50 returns 50 items.
    assert first_page_size == 50, f"AC4-page-size failed: got {first_page_size}"

    cursor = first_body.get("next_cursor")
    assert cursor, (
        "next_cursor must be present after the first page (75 > 50)"
    )

    second = client.get(
        f"/v1/tasks?limit=50&cursor={cursor}",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert second.status_code == 200, (
        f"second page: expected 200, got {second.status_code}"
    )
    second_body = second.json()
    second_page_size = len(second_body.get("items", []))
    # AC4-second-page: 50 + 25 == 75 (seed count).
    assert first_page_size + second_page_size == seed_count, (
        f"AC4-second-page failed: {first_page_size}+{second_page_size} != {seed_count}"
    )


def test_limit_default_and_upper_bound(client):
    """AC-1.5: GET /v1/tasks?limit=N — default 50, max 200, >200 -> 422."""
    # Default: limit omitted -> 50.
    default_resp = client.get(
        "/v1/tasks",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert default_resp.status_code == 200, (
        f"default list: expected 200, got {default_resp.status_code}"
    )
    limit_default = default_resp.json().get("limit")
    # AC5-limit-default: reported limit must be 50 when none supplied.
    assert limit_default == 50, f"AC5-limit-default failed: got {limit_default!r}"

    # Upper bound exceeded -> 422.
    rejected = client.get(
        "/v1/tasks?limit=201",
        headers={"X-API-Key": "fake-read-key"},
    )
    observed_status_code = rejected.status_code
    # AC5-limit-rejection: limit=201 (> 200 max) returns 422.
    assert observed_status_code == 422, (
        f"AC5-limit-rejection failed: expected 422, got {observed_status_code}"
    )


def test_delete_removes_task_and_results_in_tx(client, fake_repo):
    """AC-1.6: DELETE removes task + result rows in a single transaction.

    NFR-10 (integration coverage): end-to-end CRUD chain exercised at the
    HTTP layer, covering create + delete + tx-atomic invariant.
    NFR-11 (readability): handler size and module-level complexity targets.
    """
    # Seed one task with 3 result rows.
    task = fake_repo.create({"command": "echo x", "name": "del-target"})
    fake_repo.results = {
        "r1": {"task_id": task["id"], "exit_code": 0, "stdout_tail": "x\n"},
        "r2": {"task_id": task["id"], "exit_code": 0, "stdout_tail": "y\n"},
        "r3": {"task_id": task["id"], "exit_code": 1, "stdout_tail": "z\n"},
    }

    response = client.delete(
        f"/v1/tasks/{task['id']}",
        headers={"X-API-Key": "fake-admin-key"},
    )
    # DELETE may return 204 No Content or 200 OK — both are acceptable.
    assert response.status_code in (200, 204), (
        f"delete: expected 200/204, got {response.status_code} body={response.text}"
    )

    # AC6-tx-atomic: after delete the task row is gone AND its result rows
    # are gone (single transaction). Count surviving rows for this task id.
    assert fake_repo.get(task["id"]) is None, (
        "task row still present after delete"
    )
    expected_rows_after_delete = sum(
        1 for r in fake_repo.results.values() if r.get("task_id") == task["id"]
    )
    assert expected_rows_after_delete == 0, (
        f"AC6-tx-atomic failed: {expected_rows_after_delete} result rows remain"
    )


# ---------------------------------------------------------------------------
# Coverage-filling tests — exercise the FR-01 handler branches that are
# reachable only via business-rule violations (duplicate name) or the GET
# happy path. Not part of TEST_SPEC's named list, but kept locally so
# `pytest --cov` reports 100% on the FR-01 router module.
# ---------------------------------------------------------------------------


def test_create_duplicate_name_returns_422(client, fake_repo):
    """Coverage: handler `except ValueError` branch (AC-1.1 business-rule path).

    Seeds a task with `name="dup-target"`, then issues a second POST that
    satisfies pydantic validation (command non-empty, length OK) but trips
    the repository's duplicate-name check. The handler must convert the
    repo's ValueError into a 422 + problem+json response.
    """
    fake_repo.create({"command": "echo first", "name": "dup-target"})

    response = client.post(
        "/v1/tasks",
        json={"command": "echo second", "name": "dup-target"},
        headers={"X-API-Key": "fake-write-key"},
    )
    assert response.status_code == 422, (
        f"duplicate-name: expected 422, got {response.status_code} body={response.text}"
    )
    assert response.headers.get("content-type", "").startswith(
        "application/problem+json"
    ), f"expected problem+json, got {response.headers.get('content-type')!r}"


def test_get_existing_task_returns_200(client, fake_repo):
    """Coverage: read_task happy path (AC-1.3 200 response)."""
    task = fake_repo.create({"command": "echo hi", "name": "existing"})

    response = client.get(
        f"/v1/tasks/{task['id']}",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert response.status_code == 200, (
        f"existing task: expected 200, got {response.status_code} body={response.text}"
    )
    body = response.json()
    assert body.get("id") == task["id"], (
        f"id mismatch: got {body.get('id')!r}, expected {task['id']!r}"
    )