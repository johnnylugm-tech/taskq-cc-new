# Specification Tracking Matrix — taskq-api

> Phase 1 requirements tracking view over the canonical spec `SPEC.md`
> (v1.0.0, 2026-07-30). This file is a human-readable mirror of the SRS.md
> FR roster; it is NOT the system of record for status. See "Specification
> Status" below.

## Project Info
- Project Name: taskq-api
- Version: v1.0.0
- Created: 2026-08-24
- Canonical Spec: `SPEC.md` (project root)
- Source of Truth (SRS): `01-requirements/SRS.md` (APPROVED, 2026-08-24)
- Round: 1

## Specification Status

> **The Status column is machine-refreshed** — `advance-phase` overwrites each
> FR's Status from `build_traceability`'s live code/test scan (IN_PROGRESS once
> code/module exists, VERIFIED once code+test exist). The authoritative status is
> that scan / `quality_manifest.json`, NOT this hand-filled cell. Fill the
> semantic columns (Spec Description / Intent Class / Decision Framework /
> Owner / Notes); leave Status to refresh itself (a hand-edit is overwritten on
> the next advance).

## Tracking Matrix — Functional Requirements

| FR ID | Spec Description | Intent Class | Decision Framework | Owner | Status | Notes |
|-------|-----------------|--------------|-------------------|-------|--------|-------|
| FR-01 | Task resource CRUD API: POST/GET/LIST/DELETE on `/v1/tasks`, cursor-based pagination, 422/404/409 problem+json | functional | FastAPI + pydantic v2 + SQLAlchemy 2.x declarative; cursor pagination (no offset); RFC 7807 error contract per FR-10 | Agent B (architecture) / Agent C (development) | VERIFIED | Single FR per SRS.md §3 FR-01; AC-1.1..AC-1.6 |
| FR-02 | Task execution endpoint: POST `/v1/tasks/{id}/run` returns 202 + `run_id`; `asyncio.create_subprocess_exec(*shlex.split(command))` with `TASKQ_TASK_TIMEOUT`; writes `task_results` row; GET `/v1/tasks/{id}/runs` history newest-first | functional | `asyncio.create_subprocess_exec` (no `shell=True`); state machine `pending → running → done\|failed\|timeout` | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-02; AC-2.1..AC-2.6 |
| FR-03 | API-key authentication: `X-API-Key` header, SHA-256 hash at rest, `hmac.compare_digest` constant-time compare, 401 on missing/invalid, plaintext printed once at `key create`, revoked keys invalid, `/healthz` and `/readyz` exempt | security | SHA-256 hashing; `hmac.compare_digest`; `python -m taskq_api key create --scope <scope>` admin entry | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-03; AC-3.1..AC-3.6 |
| FR-04 | Scope authorization: `read < write < admin` hierarchical; insufficient scope returns 403 problem+json without leaking resource existence; single FastAPI dependency is the only authn/authz decision point | security | Hierarchical scope; single dependency at `api/deps.py`; 403 body must not leak existence | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-04; AC-4.1..AC-4.3 |
| FR-05 | Rate limiting: per-token token bucket in DB with capacity `TASKQ_RATE_BURST` and refill `TASKQ_RATE_PER_SEC`; 429 + `Retry-After` on overflow; row-level lock in single transaction; `/healthz` and `/readyz` exempt | functional | Per-token token bucket persisted in DB; single transaction with row-level lock; `Retry-After` header (seconds) | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-05; AC-5.1..AC-5.4 |
| FR-06 | Persistence layer and transaction boundaries: `repository/` is the only layer importing `sqlalchemy`; one `Session` per request with explicit commit/rollback via context manager; no string-concatenated SQL; explicit eager loading (`selectinload`/`joinedload`); `pool_size=TASKQ_DB_POOL_SIZE` with `pool_pre_ping=True` | architecture | 4-layer contract `api > service > repository > models`; `sqlalchemy` forbidden outside `repository/` (enforced by `.importlinter`) | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-06; AC-6.1..AC-6.5 |
| FR-07 | Schema migration: Alembic v1 (tasks, api_keys) → v2 (tags, task_tags, `tasks.name` unique index) → v3 (split `tasks.result_json` into `task_results` with data move, reversible downgrade); `upgrade head` / `downgrade base` succeed; round-trip data integrity verified column-by-column against real SQLite file; no destructive shortcuts | functional | Alembic 3-step migration; each `downgrade()` real (no `DROP TABLE` shortcuts); migration files covered by offline-SQL assertion tests | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-07; AC-7.1..AC-7.5 |
| FR-08 | Async runner: `asyncio.TaskGroup` manages background execution; graceful drain on shutdown waits up to `TASKQ_DRAIN_TIMEOUT` (tasks exceeding budget marked `interrupted`); concurrency cap `TASKQ_MAX_CONCURRENT`; `asyncio.wait_for` timeout kills child process via `process.kill()` + `await process.wait()`; `asyncio.CancelledError` must propagate | functional | `asyncio.TaskGroup` + bounded concurrency + `wait_for`; orphan-process absence asserted | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-08; AC-8.1..AC-8.5 |
| FR-09 | Health and observability: `/healthz` returns 200 `{status:ok}` when alive; `/readyz` returns 200 only when DB reachable AND `alembic current` == head (else 503 with body naming the failed condition); `/v1/metrics` requires `admin` and reports task counts by status, execution-latency percentiles, rate-limit rejection counts | functional | Fail-closed `/readyz`; `/v1/metrics` admin-scoped; in-process metrics endpoint only (no Prometheus scrape) | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-09; AC-9.1..AC-9.4 |
| FR-10 | Error contract (RFC 7807): every non-2xx response uses `application/problem+json` with fields `type`/`title`/`status`/`detail`/`instance`/`correlation_id`; `detail` never carries SQL/stack/file-path/schema; `correlation_id` echoed in `X-Correlation-Id` header and log; status mapping per SPEC.md §7 | functional | RFC 7807 envelope; `detail` whitelist (no SQL/stack/path/schema); `correlation_id` propagated to header + log | Agent B / Agent C | VERIFIED | Single FR per SRS.md §3 FR-10; AC-10.1..AC-10.5 |

## Tracking Matrix — Non-Functional Requirements

| NFR ID | Spec Description | Intent Class | Decision Framework | Owner | Status | Notes |
|--------|-----------------|--------------|-------------------|-------|--------|-------|
| NFR-01 | `GET /v1/tasks/{id}` p95 < 30ms and `GET /v1/tasks?limit=50` p95 < 80ms at 10k rows; list endpoint SQL statement count is constant (no N+1); measured via `pytest-benchmark` / ASGI transport | performance | `pytest-benchmark` over 10k-row fixture; SQLAlchemy event listener asserts constant statement count on list endpoint | Agent B / Agent C | DRAFT | SRS.md §4 NFR-01; AC-N1.1..AC-N1.3 |
| NFR-02 | No `shell=True`/`eval(`/`exec(` in codebase (grep 0 hits); no string-concatenated SQL; API keys hashed with `hmac.compare_digest`; 403 leaks no resource existence; error body carries no stack/SQL/path; CORS deny-by-default; `bandit` 0 HIGH / 0 MEDIUM | security | grep gates + `bandit -r 03-development/src/` + CORS allowlist from `TASKQ_CORS_ORIGINS`; 403 body redaction asserted | Agent B / Agent C | DRAFT | SRS.md §4 NFR-02; AC-N2.1..AC-N2.7 |
| NFR-03 | Explicit per-request transaction boundaries via context manager; no bare `except:` / `except Exception: pass`; `asyncio.CancelledError` always re-raised; DB-connection failure => `/readyz` 503 with detail; task timeout kills child process; migration failure rolls back transaction | reliability | `session_scope` context manager; `CancelledError` propagation test; orphan-process absence test; `/readyz` 503 fail-closed | Agent B / Agent C | DRAFT | SRS.md §4 NFR-03; AC-N3.1..AC-N3.6 |
| NFR-04 | Lines matching `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` replaced with `[REDACTED]` before `stdout_tail`/`stderr_tail`/log/error-body emission; DB connection string (with password) absent from logs, errors, `/v1/metrics`; API-key plaintext printed once and not persisted | security | Redaction filter applied at each output channel; unit tests feed sample secrets and assert substitution; log/metric scan asserts no DB-URL password | Agent B / Agent C | DRAFT | SRS.md §4 NFR-04; AC-N4.1..AC-N4.3 |
| NFR-05 | 100% of public functions/classes have docstrings containing `[FR-XX]` or `[NFR-XX]` references; every API endpoint has `summary` + `description` in `/openapi.json` | documentation | AST-docstring scanner with `[FR-XX]`/`[NFR-XX]` pattern check; OpenAPI JSON assertion test | Agent B / Agent C | DRAFT | SRS.md §4 NFR-05; AC-N5.1..AC-N5.2 |
| NFR-06 | `.importlinter` declares layers contract `api > service > repository > models` with `config`/`errors` independence and a forbidden contract banning `sqlalchemy` imports outside `repository/`; `lint-imports` exits 0; no contract weakening | architecture | `lint-imports` CI gate (exit 0); architecture test attempts `sqlalchemy` import from `service/` and `api/` and asserts `ImportError` | Agent B / Agent C | DRAFT | SRS.md §4 NFR-06; AC-N6.1..AC-N6.4 |
| NFR-07 | Runtime deps pinned with `==` in `requirements.txt`; transitives pinned via `requirements.lock`; license allowlist {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF}; full-tree scan via `pip-licenses --with-system`; SBOM at `08-config/SBOM.json` with `name`/`version`/`license`/`direct or transitive` per dep | licensing | `pip-licenses --format=json --with-system` assert; SBOM JSON schema validation; CI gate for non-allowlist license | Agent B / Agent C | DRAFT | SRS.md §4 NFR-07; AC-N7.1..AC-N7.4 |
| NFR-08 | `features.mutation_testing: true` in `.methodology/harness_config.json`; `mutmut` score ≥ 70 over `service/` + `repository/` with scope-rationale recorded | mutation | Framework `mutation-test-score` command reads `.methodology/mutation_score.json` and asserts score ≥ 70 | Agent B / Agent C | DRAFT | SRS.md §4 NFR-08; AC-N8.1..AC-N8.3 |
| NFR-09 | `pytest skipped count == 0`; `zero_assert == 0`; no exclusions via `--ignore`/`-k`/`--deselect`/`collect_ignore`/`testpaths` removal; FR-07 migration tested against real SQLite file with column-by-column round-trip; `TRACEABILITY_MATRIX.md` `VERIFIED` only after actual test pass | testability | `pytest -q` output scan; AST-assertions scanner; integration test for migration round-trip on real SQLite file; traceability verifier | Agent B / Agent C | DRAFT | SRS.md §4 NFR-09; AC-N9.1..AC-N9.5 |
| NFR-10 | Integration suite (`03-development/tests/integration/`) line-coverage of `03-development/src/` ≥ 80%; integration tests driven via `httpx.AsyncClient(transport=ASGITransport(app))`, not direct handler calls; covers full CRUD chain plus 401/403/404/409/422/429/503 each at least once, plus migration round-trip, rate-limit trigger + recovery, graceful drain | integration | `pytest --cov=03-development/src` with coverage threshold ≥ 80%; per-error-code integration test enumeration | Agent B / Agent C | DRAFT | SRS.md §4 NFR-10; AC-N10.1..AC-N10.3 |
| NFR-11 | Project MI (LLOC-weighted) ≥ 80; single-function CC ≤ 10; single file ≤ 400 lines; single directory ≤ 15 files; each API handler ≤ 40 lines (business logic sinks into `service/`) | maintainability | `radon-mi` scanner; `radon-cc` scanner; file/dir line-count + file-count scans; handler LOC scan | Agent B / Agent C | DRAFT | SRS.md §4 NFR-11; AC-N11.1..AC-N11.4 |
| NFR-12 | Makefile `verify-system` target chains `alembic upgrade head` → full test suite → service start + `/healthz` + `/readyz` smoke → `alembic downgrade base` then `upgrade head`; `make verify-system` exits 0 and prints `verify-system: PASS` | verifiability | `make verify-system` invocation and exit-code + stdout scan | Agent B / Agent C | DRAFT | SRS.md §4 NFR-12; AC-N12.1..AC-N12.2 |

## Completeness Verification

| Check | Target | Actual | Status |
|-------|--------|--------|--------|
| FR coverage in SRS.md | 10 | 10 | ✅ Done |
| NFR coverage in SRS.md | 12 | 12 | ✅ Done |
| FR → SRS section mapping | 100% | 100% | ✅ Done |
| Owner assignment | 100% | 100% | ✅ Done |
| Intent Class assigned | 100% | 100% | ✅ Done |
| Decision Framework recorded | 100% | 100% | ✅ Done |

## Source Citations

- Canonical spec source: `SPEC.md` (project root, v1.0.0, 2026-07-30)
- SRS authoritative view: `01-requirements/SRS.md` (APPROVED, 2026-08-24)
- FR block (machine-readable): `01-requirements/SRS.md` §10 (FR Block JSON)
- Acceptance criteria summary: `01-requirements/SRS.md` §5

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-24 | Initial creation: populated template with all 10 FR + 12 NFR rows from SRS.md (APPROVED); assigned Owner/Intent Class/Decision Framework; canonical source citations use bare `SPEC.md` | Agent A |