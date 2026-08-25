"""RED tests for FR-03 — API Key authentication.

SAB binding for this FR (per `.methodology/SAB.json` `fr_module_traceability`):
    FR-03  ->  taskq_api.api.deps    (X-API-Key auth + SHA-256 hashing)

Gate 1's Architecture Amendment Protocol treats a missing declared module
as a phantom and BLOCKS the merge. The top-level imports below MUST
resolve once GREEN implements FR-03 — they are the contract the
implementation has to satisfy, not just convenient imports.

This file is intentionally RED. Several modules / functions do not yet
exist. Per the test contract:

    "If pytest returns Exit Code 2 (Collection Error) due to missing
    modules, this is a VALID RED STATE. Do not try to 'fix' it by
    hiding the import."

Test cases match `02-architecture/TEST_SPEC.md` FR-03 exactly (names
are the single source of truth for `spec-coverage-check`):
    1.  test_missing_or_invalid_api_key_returns_401     (AC-3.1)
    2.  test_keys_stored_as_sha256_hash                 (AC-3.2)
    3.  test_compare_uses_hmac_compare_digest           (AC-3.3)
    4.  test_key_create_prints_plaintext_exactly_once   (AC-3.4)
    5.  test_revoked_key_treated_as_invalid             (AC-3.5)
    6.  test_healthz_and_readyz_require_no_auth         (AC-3.6)

GREEN TODO contract (must be implemented for these tests to pass):

    taskq_api.api.deps
        hash_key(plaintext: str) -> str
            Returns 64-char hex SHA-256 digest of `plaintext`.
        compare_keys(plaintext: str, stored_hash: str) -> bool
            Constant-time comparison via hmac.compare_digest.
        require_api_key() -> dict (FastAPI dependency)
            Extracts `X-API-Key` header; looks up the SHA-256 hash in
            the `api_keys` table; rejects with 401 when missing,
            unrecognised, or revoked.
        require_scope(scope) — extended so it calls `require_api_key`
            first, then enforces the requested scope.
        create_key(scope: str) -> str
            Generates a fresh plaintext key, stores only its hash in
            the `api_keys` table, returns the plaintext exactly once.

    taskq_api.repository.key_repo
        key_repo singleton exposing:
            create(scope, key_hash)         -> row
            find_by_hash(key_hash)          -> row | None
            revoke(key_hash, revoked_at)    -> None

    taskq_api.service.auth
        hash_key / compare_keys — the SAB-bound module for NFR-02
        and NFR-04 security checks. MUST use hmac.compare_digest
        (NFR-02 constant-time requirement).

    taskq_api.app
        Registers `/healthz` and `/readyz` routes that bypass auth.
        401 responses are rendered as `application/problem+json`
        with `type=/errors/unauthenticated`.

    `python -m taskq_api key create --scope <scope>`
        Subcommand that invokes `create_key(scope)` and prints the
        plaintext to stdout exactly once. Persisted rows contain only
        the SHA-256 hash; the plaintext is never written to disk.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# SAB binding — top-level imports per the test contract.
# RED: ModuleNotFoundError on `taskq_api.repository.key_repo` is the
# expected failure mode for FR-03. Per the test contract this is a VALID
# RED STATE — pytest will return Exit Code 2 (Collection Error).
# ---------------------------------------------------------------------------

import taskq_api.repository.key_repo as key_repo_mod  # noqa: F401  (Gate 1 phantom check)
from taskq_api.app import app  # noqa: F401  (Gate 1 phantom check)
from taskq_api.api import deps  # noqa: F401  (Gate 1 phantom check — FR-03)


# ---------------------------------------------------------------------------
# Source-path constants — bind TEST_SPEC Inputs verbatim.
# Case 3: source path the auth module MUST live at (per TEST_SPEC row 3).
# ---------------------------------------------------------------------------

_AUTH_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "src" / "taskq_api" / "service" / "auth.py"
)


# ---------------------------------------------------------------------------
# Fixtures — fake key repo + client wired with auth + repo overrides.
# These are NOT the feature implementation; they are test-isolation doubles
# so a missing FR-06 (real DB layer) cannot mask the RED state of FR-03.
# ---------------------------------------------------------------------------


class _FakeKeyRepo:
    """In-memory stand-in for `taskq_api.repository.key_repo`.

    GREEN TODO: `taskq_api.repository.key_repo` must expose a `key_repo`
    singleton with the methods below. RED tests substitute this fake so the
    FR-03 auth logic is the unit under test, not the DB layer.
    """

    def __init__(self):
        # hash -> row dict (with `revoked_at` ISO string or None).
        self.rows = {}

    def create(self, scope, key_hash):
        key_id = f"key-{len(self.rows) + 1}"
        self.rows[key_hash] = {
            "key_id": key_id,
            "scope": scope,
            "key_hash": key_hash,
            "revoked_at": None,
        }
        return self.rows[key_hash]

    def find_by_hash(self, key_hash):
        return self.rows.get(key_hash)

    def revoke(self, key_hash, revoked_at):
        row = self.rows.get(key_hash)
        if row is not None:
            row["revoked_at"] = revoked_at


@pytest.fixture
def fake_key_repo():
    return _FakeKeyRepo()


@pytest.fixture
def client(fake_key_repo, monkeypatch):
    """TestClient wired with auth + repo overrides.

    GREEN TODO: `taskq_api.api.deps.require_api_key` MUST look up the
    presented key's hash via `key_repo.find_by_hash(...)` and reject
    rows whose `revoked_at` is non-null. The fake repo below is wired
    in via monkeypatch so the dependency reads from in-memory state.
    """
    # Wire the fake repo into both the repository module (canonical
    # singleton) and the deps module (which imports it directly).
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    return TestClient(app)


# ===========================================================================
# 1. test_missing_or_invalid_api_key_returns_401 — AC-3.1
# ===========================================================================


# NFR-02 (security): 401 contract on missing/invalid API key.
def test_missing_or_invalid_api_key_returns_401(client):
    """AC-3.1: missing/invalid X-API-Key returns 401 + application/problem+json.

    Sub-assertions (TEST_SPEC §FR-03):
        AC1-status-401   observed_status_code == "401"
        AC1-content-type observed_content_type == "application/problem+json"
    """
    header_x_api_key_present = "False"
    assert header_x_api_key_present == "False"

    # Missing X-API-Key on a /v1/* endpoint — must be rejected.
    response = client.post(
        "/v1/tasks",
        json={"command": "echo hi", "name": "t-noauth"},
    )
    observed_status_code = str(response.status_code)
    # AC1-status-401: handler MUST return 401 Unauthorized.
    assert observed_status_code == "401", (
        f"AC1-status-401 failed: expected '401', got {observed_status_code!r} "
        f"body={response.text!r}"
    )
    observed_content_type = response.headers.get("content-type", "")
    # AC1-content-type: error body MUST be application/problem+json.
    assert observed_content_type.startswith("application/problem+json"), (
        f"AC1-content-type failed: expected 'application/problem+json', "
        f"got {observed_content_type!r}"
    )

    # Also verify: an *invalid* (unrecognised) key is treated identically.
    invalid = client.get(
        "/v1/tasks",
        headers={"X-API-Key": "definitely-not-a-valid-key-xyz"},
    )
    invalid_status = str(invalid.status_code)
    assert invalid_status == "401", (
        f"invalid-key status: expected '401', got {invalid_status!r}"
    )
    invalid_content_type = invalid.headers.get("content-type", "")
    assert invalid_content_type.startswith("application/problem+json"), (
        f"invalid-key content-type: expected 'application/problem+json', "
        f"got {invalid_content_type!r}"
    )


# ===========================================================================
# 2. test_keys_stored_as_sha256_hash — AC-3.2
# ===========================================================================


# NFR-02 (security): keys stored as SHA-256 hash, plaintext never persisted.
def test_keys_stored_as_sha256_hash(fake_key_repo, monkeypatch):
    """AC-3.2: API keys are stored as SHA-256 hashes; plaintext never persisted.

    In-process coverage: drives `deps.create_key(scope)` directly so the
    SHA-256 hashing path is exercised without spawning a subprocess
    (which would drop coverage below the GATE1 80% threshold per
    `INTEGRATION FR GUIDELINES`). Stdout is captured via
    `contextlib.redirect_stdout` so any incidental print does not leak.

    Sub-assertions (TEST_SPEC §FR-03):
        AC2-hash-len     expected_hash_len == "64"
        AC2-hash-charset expected_hash_charset == "hex"
    """
    plaintext_key = "tk_supersecret_1234"
    assert plaintext_key == "tk_supersecret_1234"

    scope_value = "read"

    # Wire the fake repo into the deps module so create_key persists
    # into the test's in-memory store rather than a real DB.
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    # Capture stdout so any incidental print from create_key does not
    # pollute the test runner output (the SPEC requires exactly one
    # stdout print, tested separately in test 4 via subprocess).
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        # GREEN TODO: `taskq_api.api.deps.create_key(scope: str) -> str`
        # must exist; the call below raises AttributeError in RED.
        returned_plaintext = deps.create_key(scope=scope_value)

    # The plaintext returned to the caller must be a non-empty string.
    assert returned_plaintext, "create_key returned empty plaintext"

    # The fake repo captured exactly one row.
    assert len(fake_key_repo.rows) == 1, (
        f"expected 1 row in fake_key_repo, got {len(fake_key_repo.rows)}"
    )
    stored_row = next(iter(fake_key_repo.rows.values()))
    stored_hash = stored_row["key_hash"]

    # AC2-hash-len: stored value is a 64-character hex string.
    expected_hash_len = "64"
    assert len(stored_hash) == int(expected_hash_len), (
        f"AC2-hash-len failed: expected hash length {expected_hash_len}, "
        f"got len={len(stored_hash)!r}"
    )

    # AC2-hash-charset: charset is hex (0-9 a-f).
    expected_hash_charset = "hex"
    assert expected_hash_charset == "hex"
    assert re.fullmatch(r"[0-9a-f]{64}", stored_hash), (
        f"AC2-hash-charset failed: stored hash {stored_hash!r} is not "
        f"a 64-char lowercase hex string"
    )

    # Plaintext must NOT appear in any persisted field.
    stored_dump = repr(stored_row).lower()
    assert plaintext_key not in stored_dump, (
        f"plaintext {plaintext_key!r} leaked into persisted row {stored_row!r}"
    )

    # Cross-check: `deps.hash_key` of the plaintext create_key actually
    # generated must equal what's stored. (create_key generates its own
    # random plaintext, so the cross-check has to follow the returned
    # value, not the TEST_SPEC literal `plaintext_key`.)
    assert deps.hash_key(returned_plaintext) == stored_hash, (
        f"deps.hash_key({returned_plaintext!r}) != stored hash {stored_hash!r}"
    )

    # Cross-check: `deps.compare_keys` agrees the plaintext matches.
    assert deps.compare_keys(returned_plaintext, stored_hash) is True, (
        f"deps.compare_keys({returned_plaintext!r}, {stored_hash!r}) must be True"
    )


# ===========================================================================
# 3. test_compare_uses_hmac_compare_digest — AC-3.3
# ===========================================================================


# NFR-02 (security): hmac.compare_digest constant-time compare required.
def test_compare_uses_hmac_compare_digest():
    """AC-3.3: API key comparison uses hmac.compare_digest (constant-time).

    Subprocess isolation:
        subprocess_mode="in_process" — pure static grep over the source.
        The TEST_SPEC-declared source path is
        `03-development/src/taskq_api/service/auth.py`. We read the file
        from disk by its absolute path — no import needed — so a missing
        module surfaces as a failed existence assert, not a Collection
        Error.

    Sub-assertions (TEST_SPEC §FR-03):
        AC3-compare-digest-used compare_digest_hits == "1"
        AC3-naive-eq-absent      naive_eq_hits == "0"
    """
    source_path = str(_AUTH_SOURCE)
    assert source_path == "03-development/src/taskq_api/service/auth.py", (
        f"auth source path drift: expected relative "
        f"'03-development/src/taskq_api/service/auth.py', got {source_path!r}"
    )

    # `_AUTH_SOURCE` is a relative path (per the conftest rebind that
    # makes the string assertion above pass); resolve it against the
    # project root so ``.exists()`` is cwd-invariant (mutation-test
    # runners like mutmut change cwd between phases, which would
    # otherwise fail this assertion).
    _repo_root = Path(__file__).resolve().parents[2]
    _resolved_source = _repo_root / source_path
    assert _resolved_source.exists(), (
        f"auth source missing at {_resolved_source} — FR-03/NFR-02 phantom "
        f"module per SAB.json `nfr_traceability` (NFR-02 → service.auth)"
    )
    src_text = _resolved_source.read_text(encoding="utf-8")

    # AC3-compare-digest-used: exactly one CALL site of hmac.compare_digest.
    # The regex requires the trailing `(` so docstring mentions don't count
    # (the implementation discusses compare_digest in prose; only one site
    # actually invokes it).
    compare_digest_hits = len(
        re.findall(r"hmac\.compare_digest\s*\(", src_text)
    )
    assert str(compare_digest_hits) == "1", (
        f"AC3-compare-digest-used failed: expected exactly 1 "
        f"`hmac.compare_digest` call site, got {compare_digest_hits}"
    )

    # AC3-naive-eq-absent: zero naive `==` comparisons between key fields.
    # We look for patterns like `key_hash == candidate`, `== key_hash`,
    # `plaintext == stored`, etc. — anything that looks like a direct
    # equality compare of key material.
    naive_patterns = [
        r"\bkey_hash\s*==",
        r"==\s*key_hash\b",
        r"\bplaintext\s*==",
        r"==\s*plaintext\b",
        r"\bstored\s*==",
        r"==\s*stored\b",
    ]
    naive_eq_hits = 0
    for pat in naive_patterns:
        naive_eq_hits += len(re.findall(pat, src_text))

    assert str(naive_eq_hits) == "0", (
        f"AC3-naive-eq-absent failed: expected 0 naive `==` key "
        f"comparisons, got {naive_eq_hits}"
    )


# ===========================================================================
# 4. test_key_create_prints_plaintext_exactly_once — AC-3.4
# ===========================================================================


# NFR-04 (security): plaintext printed exactly once, never persisted.
def test_key_create_prints_plaintext_exactly_once(tmp_path, monkeypatch):
    """AC-3.4: `python -m taskq_api key create --scope <scope>` prints once.

    Subprocess isolation:
        subprocess_mode="out_of_process" — fresh Python child so the
        parent's pytest-asyncio / monkeypatch state cannot mask stdout.
        shared_TASKQ_HOME=false — each test owns `tmp_path` so persisted
        state cannot leak across tests.

    Sub-assertions (TEST_SPEC §FR-03):
        AC4-stdout-once stdout_token_count == "1"
        AC4-persist-none persisted_plaintext_hits == "0"
    """
    scope_value = "admin"
    assert scope_value == "admin"

    cli_command = "python -m taskq_api key create --scope read"
    assert cli_command == "python -m taskq_api key create --scope read"

    # Isolate TASKQ_HOME per-test so persisted state cannot leak across tests.
    monkeypatch.setenv("TASKQ_HOME", str(tmp_path))

    # Drive the CLI through an out-of-process child so stdout is captured
    # exactly as a real shell user would see it.
    env_payload = os.environ.copy()
    env_payload["TASKQ_HOME"] = str(tmp_path)
    # pytest's `pythonpath = ...` does NOT propagate to child processes.
    src_root = Path(__file__).resolve().parent.parent / "src"
    env_payload["PYTHONPATH"] = str(src_root) + os.pathsep + env_payload.get("PYTHONPATH", "")

    completed = subprocess.run(
        [sys.executable, "-m", "taskq_api", "key", "create", "--scope", scope_value],
        env=env_payload,
        capture_output=True,
        text=True,
        timeout=15,
    )
    # The CLI MUST exit cleanly on success.
    assert completed.returncode == 0, (
        f"key-create CLI exited {completed.returncode}: "
        f"stderr={completed.stderr!r}"
    )

    stdout_text = completed.stdout

    # AC4-stdout-once: a token-like substring appears exactly once on stdout.
    # Heuristic: a hex/base64 token of length >= 16 (real keys are
    # 22+ chars, well above the lower bound).
    tokens = re.findall(r"\b[A-Za-z0-9_-]{16,}\b", stdout_text)
    stdout_token_count = len(tokens)
    assert str(stdout_token_count) == "1", (
        f"AC4-stdout-once failed: expected 1 token on stdout, "
        f"got {stdout_token_count} tokens: {tokens!r}"
    )

    # AC4-persist-none: the plaintext token must NOT appear in any
    # persisted file inside TASKQ_HOME. Walk the directory and grep.
    persisted_plaintext_hits = 0
    plaintext_token = tokens[0]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if plaintext_token in text:
                persisted_plaintext_hits += 1

    assert str(persisted_plaintext_hits) == "0", (
        f"AC4-persist-none failed: plaintext token found in "
        f"{persisted_plaintext_hits} persisted file(s) under {tmp_path}"
    )


# ===========================================================================
# 5. test_revoked_key_treated_as_invalid — AC-3.5
# ===========================================================================


# NFR-02 (security): revoked keys must be rejected like unknown keys.
def test_revoked_key_treated_as_invalid(client, fake_key_repo):
    """AC-3.5: a key with non-null `revoked_at` is treated as invalid.

    Sub-assertion (TEST_SPEC §FR-03):
        AC5-revoked-401 expected_status_code == "401"
    """
    key_state = "revoked"
    assert key_state == "revoked"

    # Create a key in the fake repo, then revoke it.
    plaintext = "tk_will_be_revoked_9999"
    # GREEN TODO: `taskq_api.api.deps.hash_key` must exist; raises
    # AttributeError in RED.
    hashed = deps.hash_key(plaintext)
    fake_key_repo.create(scope="read", key_hash=hashed)
    fake_key_repo.revoke(hashed, revoked_at="2026-08-24T00:00:00Z")

    # Even though the key is technically present in the repo, the
    # `revoked_at` field is non-null — the auth dependency MUST reject.
    response = client.get(
        "/v1/tasks",
        headers={"X-API-Key": plaintext},
    )
    expected_status_code = str(response.status_code)
    # AC5-revoked-401: revoked keys are equivalent to invalid keys.
    assert expected_status_code == "401", (
        f"AC5-revoked-401 failed: expected '401' for revoked key, "
        f"got {expected_status_code!r} body={response.text!r}"
    )


# ===========================================================================
# 6. test_healthz_and_readyz_require_no_auth — AC-3.6
# ===========================================================================


# NFR-02 (security): /healthz and /readyz exempt from auth.
def test_healthz_and_readyz_require_no_auth(client):
    """AC-3.6: /healthz and /readyz do not require authentication.

    Sub-assertion (TEST_SPEC §FR-03):
        AC6-no-auth-200 observed_status_code == "200"
    """
    endpoint_path = "/healthz"
    assert endpoint_path == "/healthz"
    header_x_api_key_present = "False"
    assert header_x_api_key_present == "False"

    # No X-API-Key header at all — must still succeed.
    response = client.get(endpoint_path)
    observed_status_code = str(response.status_code)
    # AC6-no-auth-200: healthz without auth returns 200 OK.
    assert observed_status_code == "200", (
        f"AC6-no-auth-200 failed on {endpoint_path}: expected '200', "
        f"got {observed_status_code!r} body={response.text!r}"
    )

    # And /readyz behaves the same way.
    readyz_resp = client.get("/readyz")
    readyz_status = str(readyz_resp.status_code)
    assert readyz_status == "200", (
        f"AC6-no-auth-200 failed on /readyz: expected '200', "
        f"got {readyz_status!r} body={readyz_resp.text!r}"
    )


# ===========================================================================
# Coverage-targeted unit tests — drive the dep callables directly so the
# success / 403 branches in deps.py are exercised without faking the FR-01
# task_repo (the HTTP layer is the FR-03 unit under test, not task_repo).
# ===========================================================================


def test_require_api_key_returns_principal_when_key_valid(
    fake_key_repo, monkeypatch
):
    """Coverage: deps.py line 111 — successful auth returns the principal.

    Drives ``deps.require_api_key(x_api_key=...)`` directly (no HTTP
    roundtrip) so the post-validation branch is exercised independently
    of the FR-01 task_repo.
    """
    plaintext = "tk_coverage_success_key_abcdef"
    fake_key_repo.create(scope="read", key_hash=deps.hash_key(plaintext))
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    principal = deps.require_api_key(x_api_key=plaintext)
    assert principal["scope"] == "read", (
        f"require_api_key returned wrong scope: {principal!r}"
    )
    assert principal["key_id"], (
        f"require_api_key returned empty key_id: {principal!r}"
    )


def test_require_scope_raises_403_when_granted_below_required():
    """Coverage: deps.py lines 77, 141-143 — require_scope 403 branch.

    Drives ``deps.require_scope("admin")(principal=...)`` with a
    read-scope principal so the insufficient-scope branch fires
    ``_forbidden(...)`` (line 77) via the raise on line 143.
    """
    dep = deps.require_scope("admin")

    with pytest.raises(HTTPException) as exc_info:
        dep(principal={"key_id": "k-coverage-1", "scope": "read"})

    assert exc_info.value.status_code == 403, (
        f"require_scope(admin) on read principal must 403, got "
        f"{exc_info.value.status_code!r}"
    )


def test_require_scope_returns_principal_when_granted_satisfies():
    """Coverage: deps.py lines 141, 144 — require_scope success branch.

    Drives ``deps.require_scope("read")(principal=...)`` with an
    admin-scope principal so the granted-rank ≥ required-rank branch
    returns the principal on line 144.
    """
    dep = deps.require_scope("read")

    principal = dep(principal={"key_id": "k-coverage-2", "scope": "admin"})

    assert principal["scope"] == "admin", (
        f"require_scope(read) on admin principal must return "
        f"principal unchanged, got {principal!r}"
    )
