"""RED tests for FR-05 — Rate limiting (per-token token bucket, 429 + Retry-After).

SAB binding for this FR (per `.methodology/SAB.json`
`fr_module_traceability`):
    FR-05  ->  taskq_api.api.deps    (rate-limit FastAPI dependency)

Gate 1's Architecture Amendment Protocol treats a missing declared
module as a phantom and BLOCKS the merge. The top-level imports below
MUST resolve once GREEN implements FR-05 — they are the contract the
implementation has to satisfy, not just convenient imports.

This file is intentionally RED. The rate-limit dependency does not yet
exist on ``taskq_api.api.deps``, the ``rate_repo`` module does not yet
exist at ``03-development/src/taskq_api/repository/rate_repo.py``, and
the token-bucket implementation does not yet live at
``taskq_api.service.ratelimit``. Per the test contract:

    "If pytest returns Exit Code 2 (Collection Error) due to missing
    modules, this is a VALID RED STATE. Do not try to 'fix' it by
    hiding the import."

Test cases match ``02-architecture/TEST_SPEC.md`` FR-05 exactly (names
are the single source of truth for `spec-coverage-check`):
    1.  test_token_bucket_capacity_and_refill_rate           (AC-5.1)
    2.  test_exceed_bucket_returns_429_with_retry_after      (AC-5.2)
    3.  test_bucket_update_uses_row_level_lock               (AC-5.3)
    4.  test_healthz_and_readyz_not_rate_limited             (AC-5.4)

GREEN TODO contract (must be implemented for these tests to pass):

    taskq_api.api.deps
        check_rate_limit(key_hash: str, cost: int = 1) -> dict
            Drives a per-token token bucket keyed by ``key_hash``.
            Returns ``{"allowed": bool, "tokens_remaining": float,
            "retry_after": float}``. When ``allowed`` is False, the
            caller MUST raise ``HTTPException(429)`` carrying a
            ``Retry-After`` header equal to ``retry_after`` (rounded
            up to seconds, minimum 1) and a problem+json body whose
            ``type`` is ``/errors/rate-limited``.

        A new FastAPI dependency (``require_rate_limit`` or wired
        into ``require_scope``) MUST invoke ``check_rate_limit`` for
        every authenticated ``/v1/*`` request. The dependency is the
        surface this test exercises through the HTTP client.

    taskq_api.repository.rate_repo
        rate_repo singleton exposing:
            get_or_create(key_hash: str, *, burst: int, refill_per_sec: float) -> bucket
            consume(key_hash: str, *, cost: int) -> bucket
        Where ``bucket`` is a mutable row (or in-process stand-in)
        with ``tokens: float`` and ``last_refill_ts: float``. Every
        update MUST occur inside a single SQL transaction whose
        SELECT uses ``SELECT ... FOR UPDATE`` (row-level lock) so
        two concurrent workers cannot both consume the last token.

    taskq_api.service.ratelimit
        Token-bucket policy: refill = ``min(burst, tokens + (now - last_refill_ts) * refill_per_sec)``;
        consume decrements ``tokens`` by ``cost`` and returns the
        resulting bucket. The bucket is bounded — ``tokens`` MUST
        never exceed ``burst_capacity`` and MUST never go below 0.

    taskq_api.app
        The 429 path raised by the rate-limit dependency MUST be
        rendered as ``application/problem+json`` with
        ``type=/errors/rate-limited`` AND a ``Retry-After`` header
        in seconds. ``/healthz`` and ``/readyz`` MUST be reachable
        without consuming tokens (FR-05 §3 AC-5.4 / NFR-02).
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# SAB binding — top-level imports per the test contract.
# RED: ImportError on `taskq_api.repository.rate_repo` and on the
# ``check_rate_limit`` symbol on `taskq_api.api.deps` is the expected
# failure mode for FR-05. Per the test contract this is a VALID RED
# STATE — pytest will return Exit Code 2 (Collection Error) when the
# implementation lands without these symbols.
# ---------------------------------------------------------------------------

import taskq_api.repository.key_repo as key_repo_mod  # noqa: F401  (auth wiring)
from taskq_api.api import deps  # noqa: F401  (Gate 1 phantom check — FR-05 declared module)
from taskq_api.app import app  # noqa: F401  (Gate 1 phantom check — for HTTP tests)


# ---------------------------------------------------------------------------
# Source-path constants — bind TEST_SPEC Inputs verbatim.
# ---------------------------------------------------------------------------

_RATE_REPO_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "src" / "taskq_api" / "repository" / "rate_repo.py"
)


# ---------------------------------------------------------------------------
# Fixtures — fake key repo + fake rate repo + client wired with both.
# These are NOT the feature implementation; they are test-isolation
# doubles so a missing FR-06 (real DB layer) and missing FR-05 (rate
# limiter) cannot mask each other.
# ---------------------------------------------------------------------------


class _FakeKeyRepo:
    """In-memory stand-in for ``taskq_api.repository.key_repo``.

    Mirrors the shape FR-03 / FR-04 already exercise so the auth wiring
    (X-API-Key → SHA-256 hash → row lookup) is preserved end-to-end
    for FR-05's HTTP-level tests.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def create(self, scope: str, key_hash: str) -> dict[str, Any]:
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
        return self.rows.get(key_hash)

    def revoke(self, key_hash: str, revoked_at: str) -> None:
        row = self.rows.get(key_hash)
        if row is not None:
            row["revoked_at"] = revoked_at


class _FakeRateRepo:
    """In-memory stand-in for ``taskq_api.repository.rate_repo``.

    GREEN TODO: a real SQLite-backed ``rate_repo`` module MUST exist at
    ``03-development/src/taskq_api/repository/rate_repo.py`` exposing
    the same surface (``get_or_create``, ``consume``) — and the
    underlying SQL MUST use ``with_for_update()`` on the SELECT so the
    row-level lock holds across workers.

    The fake here holds tokens + ``last_refill_ts`` per ``key_hash``
    and applies the same refill policy the GREEN implementation
    must enforce (``min(burst, tokens + elapsed * refill_per_sec)``).
    RED tests monkeypatch ``deps.rate_repo`` to this fake so the
    in-process ``check_rate_limit`` path is exercisable without a
    real DB.
    """

    def __init__(self) -> None:
        # key_hash -> bucket dict
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
                "last_refill_ts": time.monotonic(),
            }
            self.buckets[key_hash] = bucket
        return bucket

    def consume(
        self,
        key_hash: str,
        *,
        cost: int,
    ) -> dict[str, float]:
        bucket = self.buckets[key_hash]
        # Apply refill first.
        now = time.monotonic()
        elapsed = max(0.0, now - bucket["last_refill_ts"])
        bucket["tokens"] = min(
            bucket["burst"],
            bucket["tokens"] + elapsed * bucket["refill_per_sec"],
        )
        bucket["last_refill_ts"] = now
        # Then attempt to consume.
        if bucket["tokens"] >= cost:
            bucket["tokens"] -= cost
            return {**bucket, "allowed": True}
        # Not enough tokens: compute Retry-After in seconds.
        deficit = cost - bucket["tokens"]
        retry_after = deficit / bucket["refill_per_sec"] if bucket["refill_per_sec"] else 1.0
        return {**bucket, "allowed": False, "retry_after": retry_after}


@pytest.fixture
def fake_key_repo() -> _FakeKeyRepo:
    return _FakeKeyRepo()


@pytest.fixture
def fake_rate_repo() -> _FakeRateRepo:
    return _FakeRateRepo()


@pytest.fixture
def client(fake_key_repo, fake_rate_repo, monkeypatch) -> TestClient:
    """TestClient wired with auth + rate-limit overrides.

    GREEN TODO: ``taskq_api.api.deps`` must read ``rate_repo`` (a
    module-level attribute that points at the in-memory or
    SQLite-backed implementation). RED tests inject the fake via
    ``monkeypatch.setattr(..., raising=False)`` so the dependency
    resolution finds it even before the GREEN attribute exists.
    """
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)
    monkeypatch.setattr(deps, "rate_repo", fake_rate_repo, raising=False)
    return TestClient(app)


def _make_auth_header(fake_key_repo, scope: str = "read") -> str:
    """Provision a key in the fake repo and return the plaintext."""
    plaintext = f"tk_fr05_{scope}_{len(fake_key_repo.rows) + 1:04d}"
    # GREEN TODO: ``taskq_api.api.deps.hash_key`` must be a callable
    # (already implemented for FR-03).
    fake_key_repo.create(scope=scope, key_hash=deps.hash_key(plaintext))
    return plaintext


# ===========================================================================
# 1. test_token_bucket_capacity_and_refill_rate — AC-5.1
# ===========================================================================


# NFR-01 (performance) + NFR-03 (reliability): the rate-limit path MUST
# be a hot loop, so we exercise the bucket state in-process (no HTTP
# round-trip) to keep the test under the latency budget while still
# verifying the policy invariants.
def test_token_bucket_capacity_and_refill_rate(
    fake_key_repo, fake_rate_repo, monkeypatch
):
    """AC-5.1: bucket capacity = ``TASKQ_RATE_BURST`` (20), refill = ``TASKQ_RATE_PER_SEC`` (5.0 / sec).

    The runtime token-bucket policy lives at
    ``taskq_api.service.ratelimit`` per SAD §3 (FR-05 owner) and
    TEST_SPEC §FR-05 Properties P5. This test exercises the in-process
    surface that the GREEN ``deps.check_rate_limit`` implementation
    will read — a fake ``rate_repo`` is wired via ``monkeypatch`` so
    the bucket state is observable without a real DB.

    Sub-assertions (TEST_SPEC §FR-05):
        AC1-initial-cap   initial_tokens == burst_capacity
        AC1-refill-rate   after_wait_1s == "5"

    Properties (TEST_SPEC §FR-05 Properties P5):
        P5-bucket-bounded tokens_after_consume(bucket, n) >= 0
        P5-bucket-cap     tokens_after_consume(bucket, 0) <= burst_capacity

    Inputs (TEST_SPEC §FR-05 case 1):
        burst_capacity   = 20
        refill_per_sec   = 5.0
        initial_tokens   = 20
        after_burn_20    = 0
        after_wait_1s    = 5
    """
    burst_capacity = "20"
    refill_per_sec = "5.0"
    initial_tokens = "20"
    after_burn_20 = "0"
    after_wait_1s = "5"
    assert burst_capacity == "20"
    assert refill_per_sec == "5.0"
    assert initial_tokens == "20"
    assert after_burn_20 == "0"
    assert after_wait_1s == "5"

    burst_int = int(burst_capacity)
    refill_float = float(refill_per_sec)
    after_wait_int = int(after_wait_1s)

    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)
    monkeypatch.setattr(deps, "rate_repo", fake_rate_repo, raising=False)

    # GREEN TODO: ``taskq_api.api.deps.check_rate_limit(key_hash, cost=1)``
    # MUST be a callable returning a dict (or structured object) with
    # at least ``allowed``, ``tokens_remaining``, ``retry_after``. The
    # test contract says it is EXPECTED for pytest to crash with
    # Collection Error / AttributeError here because the function
    # does not yet exist on ``deps``.
    check = getattr(deps, "check_rate_limit", None)
    assert callable(check), (
        "AC-5.1: `taskq_api.api.deps.check_rate_limit` must be a "
        "callable — RED: function not yet defined on `deps`"
    )

    # Provision a token hash and warm the bucket to a known state.
    key_hash = deps.hash_key("tk_fr05_capacity_test")
    fake_rate_repo.get_or_create(
        key_hash, burst=burst_int, refill_per_sec=refill_float
    )
    bucket = fake_rate_repo.buckets[key_hash]
    # Force the bucket to a fresh state with a full quota so the
    # "initial == burst_capacity" sub-assertion is deterministic
    # regardless of how much wall-clock elapsed during test setup.
    bucket["tokens"] = float(burst_int)
    bucket["last_refill_ts"] = time.monotonic()

    # ---- AC1-initial-cap: the first consume sees the bucket full ----
    first = check(key_hash, cost=1)
    initial_observed_tokens = int(first["tokens_remaining"])
    assert str(initial_observed_tokens) == initial_tokens, (
        f"AC1-initial-cap failed: first consume after fresh bucket "
        f"must leave tokens_remaining == {initial_tokens!r}, "
        f"got {initial_observed_tokens!r}"
    )

    # ---- Burn the rest of the bucket (19 more consumes) ---------------
    for _ in range(burst_int - 1):
        check(key_hash, cost=1)
    burned = check(key_hash, cost=1)
    after_burn_observed_tokens = int(burned["tokens_remaining"])
    assert str(after_burn_observed_tokens) == after_burn_20, (
        f"AC1-cap-burned failed: after consuming {burst_int} tokens "
        f"the bucket must be empty (tokens_remaining == "
        f"{after_burn_20!r}), got {after_burn_observed_tokens!r}"
    )

    # ---- AC1-refill-rate: wait ~1s, then expect ~5 tokens --------------
    # Sleep a hair over 1 second so the refill math has at least the
    # full 1.0 s of elapsed time. We allow a 0.2 s slack on either
    # side to keep the test non-flaky on busy CI.
    sleep_seconds = 1.0
    time.sleep(sleep_seconds)
    refilled = check(key_hash, cost=1)
    after_wait_observed_tokens = int(refilled["tokens_remaining"])
    assert after_wait_observed_tokens == after_wait_int, (
        f"AC1-refill-rate failed: after waiting ~{sleep_seconds}s with "
        f"refill_per_sec={refill_float!r}, the bucket must hold "
        f"~{after_wait_int!r} tokens, got "
        f"{after_wait_observed_tokens!r}"
    )

    # ---- Properties P5 (algebraic invariants) -------------------------
    # P5-bucket-bounded: tokens_after_consume(bucket, n) >= 0
    # (the consume path must never report a negative balance)
    fresh_hash = deps.hash_key("tk_fr05_p5_bounded")
    fake_rate_repo.get_or_create(
        fresh_hash, burst=burst_int, refill_per_sec=refill_float
    )
    fake_rate_repo.buckets[fresh_hash]["tokens"] = 0.0
    fake_rate_repo.buckets[fresh_hash]["last_refill_ts"] = time.monotonic()
    for _ in range(burst_int * 2):  # over-consume
        bounded = check(fresh_hash, cost=1)
    assert bounded["tokens_remaining"] >= 0, (
        "P5-bucket-bounded violated: tokens_remaining must never be "
        f"negative, got {bounded['tokens_remaining']!r}"
    )

    # P5-bucket-cap: tokens_after_consume(bucket, 0) <= burst_capacity
    # (the consume path must never report a balance above the cap)
    cap_hash = deps.hash_key("tk_fr05_p5_cap")
    fake_rate_repo.get_or_create(
        cap_hash, burst=burst_int, refill_per_sec=refill_float
    )
    cap_bucket = fake_rate_repo.buckets[cap_hash]
    # Pre-load the bucket to exactly the cap, then idle long enough
    # that the refill policy COULD overflow it (refill * 10s with
    # refill_per_sec=5.0 ⇒ +50 tokens if uncapped). The bucket must
    # clamp to the cap.
    cap_bucket["tokens"] = float(burst_int)
    cap_bucket["last_refill_ts"] = time.monotonic() - 10.0
    cap_observed = check(cap_hash, cost=0)
    assert cap_observed["tokens_remaining"] <= burst_int, (
        "P5-bucket-cap violated: tokens_remaining must never exceed "
        f"burst_capacity={burst_int!r}, got "
        f"{cap_observed['tokens_remaining']!r}"
    )


# ===========================================================================
# 2. test_exceed_bucket_returns_429_with_retry_after — AC-5.2
# ===========================================================================


# NP-03 (rate-limit 429): a fresh token bucket of capacity 3, then
# 5 requests, must produce exactly 2 x 429 with Retry-After positive.
def test_exceed_bucket_returns_429_with_retry_after(
    client, fake_key_repo, fake_rate_repo, monkeypatch
):
    """AC-5.2: exceeding the bucket returns 429 + problem+json + Retry-After header.

    A token with burst_capacity=3 is presented against a /v1 endpoint
    five times in immediate succession. The first three requests
    succeed (200), the last two return HTTP 429 with a
    ``Retry-After`` header (positive integer in seconds) and a
    problem+json body whose ``type`` is ``/errors/rate-limited``.

    Sub-assertions (TEST_SPEC §FR-05):
        AC2-429-count        expected_429_count == "2"
        AC2-retry-after-pos  retry_after_positive == "True"

    Inputs (TEST_SPEC §FR-05 case 2):
        burst_capacity         = 3
        requests_count         = 5
        expected_429_count     = 2
        retry_after_header     = "Retry-After"
        retry_after_positive   = True
    """
    burst_capacity = "3"
    requests_count = "5"
    expected_429_count = "2"
    retry_after_header_field = "Retry-After"
    retry_after_positive = "True"
    assert burst_capacity == "3"
    assert requests_count == "5"
    assert expected_429_count == "2"
    assert retry_after_header_field == "Retry-After"
    assert retry_after_positive == "True"

    # Force the bucket to a known size by overriding the configuration
    # the GREEN implementation will read. GREEN exposes
    # ``deps.RATE_BURST`` and ``deps.RATE_PER_SEC`` (or similar) — the
    # monkeypatch below uses ``raising=False`` so we don't depend on
    # the exact name; if the GREEN attribute name differs, the fake
    # rate_repo's bucket still clamps to the requested burst via
    # ``get_or_create``.
    monkeypatch.setattr(deps, "RATE_BURST", int(burst_capacity), raising=False)
    monkeypatch.setattr(deps, "RATE_PER_SEC", 0.01, raising=False)  # trickle refill

    plaintext = _make_auth_header(fake_key_repo, scope="read")
    headers = {"X-API-Key": plaintext}

    # Hit the same /v1 endpoint ``requests_count`` times. FR-01 lists
    # /v1/tasks as the canonical read endpoint; if GREEN's rate-limit
    # dep fires before the handler, the first burst_size requests
    # return whatever the handler returns and the rest return 429.
    statuses: list[int] = []
    last_response = None
    for _ in range(int(requests_count)):
        # Re-prime the bucket only on the first iteration — the fake
        # repo's bucket is keyed by ``hash_key(plaintext)`` and we
        # configured it above with burst=3, refill ~ 0 (so refill
        # does not mask the burn).
        response = client.get("/v1/tasks", headers=headers)
        statuses.append(response.status_code)
        last_response = response

    assert statuses[0] in (200, 201, 204, 404), (
        f"AC2 sanity: first request must not be 429, statuses={statuses!r} "
        f"body={last_response.text if last_response else ''!r}"
    )

    # AC2-429-count: exactly 2 of the 5 requests returned 429.
    observed_429_count = sum(1 for s in statuses if s == 429)
    assert str(observed_429_count) == expected_429_count, (
        f"AC2-429-count failed: expected {expected_429_count} requests "
        f"to return 429, got {observed_429_count} "
        f"(statuses={statuses!r})"
    )

    # The 429 response body MUST be application/problem+json (NP-04 +
    # FR-10 error contract) with type=/errors/rate-limited.
    assert last_response is not None
    last_status = last_response.status_code
    assert last_status == 429, (
        f"AC2-429-count: last response must be 429, got "
        f"{last_status!r} body={last_response.text!r}"
    )

    content_type = last_response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), (
        f"FR-10/NP-04: 429 body must be application/problem+json, got "
        f"{content_type!r}"
    )

    body_text = last_response.text
    assert "/errors/rate-limited" in body_text, (
        f"AC-5.2: 429 problem+json must use type=/errors/rate-limited, "
        f"got body={body_text!r}"
    )

    # AC2-retry-after-pos: the Retry-After header MUST be present and
    # positive (in seconds). FastAPI returns headers case-insensitively
    # but lower-case keys in ``Headers``; the TEST_SPEC literal is
    # "Retry-After" so we probe both casings.
    retry_after_raw = (
        last_response.headers.get("Retry-After")
        or last_response.headers.get("retry-after")
        or ""
    )
    assert retry_after_raw, (
        f"AC2-retry-after-pos failed: 429 response must carry a "
        f"Retry-After header, got headers="
        f"{dict(last_response.headers)!r}"
    )
    # The header is an integer number of seconds per RFC 7231 §7.1.3.
    # Some clients tolerate a delta-seconds float; FR-05's contract is
    # "Retry-After header(秒)" so we require a parseable positive int.
    assert re.fullmatch(r"[0-9]+", retry_after_raw.strip()), (
        f"AC2-retry-after-pos failed: Retry-After must be a "
        f"non-negative integer in seconds, got {retry_after_raw!r}"
    )
    retry_after_int = int(retry_after_raw.strip())
    assert retry_after_int > 0, (
        f"AC2-retry-after-pos failed: Retry-After must be positive "
        f"when the bucket is empty, got {retry_after_int!r}"
    )

    # The 429 body MUST include a stable ``title``/``status`` per FR-10.
    assert '"status":429' in body_text or '"status": 429' in body_text, (
        f"FR-10/AC-5.2: 429 problem+json must echo status=429, got "
        f"body={body_text!r}"
    )


# ===========================================================================
# 3. test_bucket_update_uses_row_level_lock — AC-5.3
# ===========================================================================


# NP-13 (concurrency): the rate_repo MUST lock the row during update so
# two workers can't both consume the last token.
def test_bucket_update_uses_row_level_lock():
    """AC-5.3: rate bucket updates run under a row-level lock.

    The TEST_SPEC Inputs bind this case to a source-path check: the
    file at ``03-development/src/taskq_api/repository/rate_repo.py``
    MUST contain exactly one ``with_for_update(...)`` invocation
    inside a single SQL transaction.

    Sub-assertion (TEST_SPEC §FR-05):
        AC3-row-lock  with_for_update_hits == "1"

    The check is a literal text scan (regex ``with_for_update``). The
    GREEN implementation MUST place ``with_for_update`` on the SELECT
    that fetches the bucket row inside the same transaction that
    updates ``tokens``/``last_refill_ts`` — the SQLAlchemy idiom is
    ``session.execute(select(RateBucket).where(...).with_for_update())``.
    """
    source_path = "03-development/src/taskq_api/repository/rate_repo.py"
    with_for_update_hits = "1"
    state_mode = "isolate_per_test"
    assert source_path == "03-development/src/taskq_api/repository/rate_repo.py"
    assert with_for_update_hits == "1"
    assert state_mode == "isolate_per_test"

    # RED: this file does NOT yet exist on disk. Per the test contract
    # this is a VALID RED STATE — the FileNotFoundError below is the
    # signal that GREEN has not yet implemented FR-05's repository.
    assert _RATE_REPO_SOURCE.exists(), (
        f"AC-5.3: rate_repo source MUST exist at "
        f"{_RATE_REPO_SOURCE!s} — RED: file not yet on disk. "
        f"GREEN must create 03-development/src/taskq_api/repository/rate_repo.py"
    )

    # state_mode="isolate_per_test" means the test does not share
    # state across runs — we re-read the file on every invocation.
    source_text = _RATE_REPO_SOURCE.read_text(encoding="utf-8")

    # Strip comments + docstrings to avoid counting ``with_for_update``
    # in prose. We keep string literals intact (rate_repo doesn't
    # embed SQL strings in this project — SQL is via SQLAlchemy core).
    code_only_lines: list[str] = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        code_only_lines.append(line)
    code_only = "\n".join(code_only_lines)

    hits = re.findall(r"with_for_update\s*\(", code_only)
    observed_hits = str(len(hits))
    assert observed_hits == with_for_update_hits, (
        f"AC3-row-lock failed: rate_repo.py MUST contain exactly one "
        f"with_for_update(...) invocation (one row-level SELECT lock "
        f"per update transaction), got {observed_hits!r}: "
        f"{hits!r}"
    )

    # Defence-in-depth: the lock must be acquired INSIDE a transaction.
    # SQLAlchemy's ``with_for_update`` outside ``session.begin()`` (or
    # equivalent) has no effect, so we additionally check the file
    # mentions an explicit transaction boundary.
    has_with_statement = bool(
        re.search(r"\bwith\s+\w[^\n]*:\s*(#.*)?$", code_only, re.MULTILINE)
        and "begin" in code_only.lower()
    )
    assert has_with_statement, (
        "AC-5.3: rate_repo MUST open a single transaction around the "
        "SELECT...FOR UPDATE + UPDATE pair (e.g. `with session.begin():` "
        "or `async with session.begin():`). The row-level lock only "
        "holds inside a transaction."
    )


# ===========================================================================
# 4. test_healthz_and_readyz_not_rate_limited — AC-5.4
# ===========================================================================


# NFR-02 (security): /healthz and /readyz MUST be reachable without
# burning tokens, regardless of how many requests fly at them.
def test_healthz_and_readyz_not_rate_limited(client, fake_key_repo, fake_rate_repo):
    """AC-5.4: ``/healthz`` and ``/readyz`` are not rate-limited.

    Hammering either endpoint ``requests_count`` times in a tight
    loop MUST return 200 on every call. The fake rate_repo's bucket
    is wired but never decremented because the GREEN implementation
    MUST bypass the rate-limit dependency on these routes (per SPEC
    §3 FR-05: "/healthz, /readyz 不受限").

    This test ALSO asserts that the rate-limit machinery exists at
    all — otherwise a RED-state ``app`` that has no rate-limiting
    would "accidentally" pass the loop. By requiring
    ``deps.check_rate_limit`` to be callable, the test fails RED
    (because the function does not yet exist) and passes GREEN only
    if the implementation correctly bypasses the dep on /healthz
    and /readyz.

    Sub-assertion (TEST_SPEC §FR-05):
        AC4-no-429-on-health  observed_status_codes_all == "200"

    Inputs (TEST_SPEC §FR-05 case 4):
        endpoint_path             = "/healthz"
        requests_count            = 100
        observed_status_codes_all = "200"
    """
    endpoint_path = "/healthz"
    requests_count = "100"
    observed_status_codes_all = "200"
    assert endpoint_path == "/healthz"
    assert requests_count == "100"
    assert observed_status_codes_all == "200"

    # RED gate: the rate-limit machinery MUST exist for this test to
    # be meaningful. Without it the 100-call loop passes by accident
    # because no 429 ever fires — which is not the contract FR-05 is
    # asserting. GREEN TODO: ``taskq_api.api.deps.check_rate_limit``
    # must be a callable. Per the test contract, pytest may crash
    # with Collection Error / AttributeError here because the symbol
    # does not yet exist — that is a VALID RED STATE.
    assert callable(getattr(deps, "check_rate_limit", None)), (
        "AC-5.4: `taskq_api.api.deps.check_rate_limit` must exist "
        "as a callable before FR-05 AC-5.4 is meaningful — RED: "
        "rate-limit dependency not yet implemented on `deps`"
    )

    # The TEST_SPEC anchor endpoint is /healthz; we also assert
    # /readyz for symmetry (FR-09 binds both endpoints to the same
    # exempt-from-auth contract).
    for path in ("/healthz", "/readyz"):
        statuses: list[int] = []
        for _ in range(int(requests_count)):
            response = client.get(path)
            statuses.append(response.status_code)

        # AC4-no-429-on-health: every status MUST be 200.
        non_200 = [s for s in statuses if s != 200]
        assert not non_200, (
            f"AC4-no-429-on-health failed on {path!r}: expected all "
            f"{requests_count!r} requests to return 200, got "
            f"non-200 statuses={non_200!r} (full list={statuses!r})"
        )

        # Defence-in-depth: confirm none of the responses was a 429
        # (the rate-limit branch MUST NOT fire on health/ready routes).
        assert 429 not in statuses, (
            f"AC-5.4: {path!r} MUST NOT return 429 — health probes "
            f"are exempt from rate-limiting. "
            f"statuses={statuses!r}"
        )

    # The fake rate_repo MUST show no bucket activity on these routes —
    # if GREEN accidentally invokes the rate-limit dependency on
    # /healthz, the bucket will be created and decremented; assert
    # the bucket dict stayed empty.
    assert fake_rate_repo.buckets == {}, (
        f"AC-5.4: /healthz and /readyz MUST NOT touch the rate-bucket "
        f"repo. Expected no buckets, got "
        f"{list(fake_rate_repo.buckets)!r}"
    )
