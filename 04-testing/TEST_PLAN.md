# TEST_PLAN.md — taskq-api P4 Testing

> Phase 4 (Testing). Authoritative coverage plan executed once before per-FR testing begins.
> Sources: `01-requirements/SRS.md` (FR-01..FR-10 + NFR-01..NFR-12 ACs) and
> `.methodology/quality_manifest.json` (FR list, dimension mapping, gate score overrides).
> Scope: ALL 10 FRs and ALL 12 NFRs.

---

## 0. Conventions

| Token | Meaning |
|-------|---------|
| **TC-XX.Y** | Test case ID where XX = FR/NFR index and Y = ordinal within that requirement. |
| **Category** | `positive` (happy path), `negative` (error / forbidden input), `boundary` (limit / threshold), `edge` (unusual but valid scenario). |
| **Priority** | `P0` (gate-blocking), `P1` (gate-scored), `P2` (smoke / hygiene). P0 maps to NFR gate_score_overrides. |
| **Spec anchor** | The SRS AC / §8 row the case is traceable to (used by TRACEABILITY_MATRIX.md). |
| **Test layer** | `unit` (function / class), `integration` (ASGI transport via `httpx.AsyncClient(transport=ASGITransport(app))`), `e2e` (real SQLite file + Alembic), `architecture` (lint-imports / bandit / radon / greps), `mutation` (mutmut runs). |

**Test runner:** `pytest 03-development/tests -q` (per NFR-09 / SRS §8 #1).
**Integration coverage gate:** line coverage of `03-development/src/` measured while running only `03-development/tests/integration/` ≥ 80% (per NFR-10 / SRS §8 #3).
**Python:** `/Users/johnny/projects/taskq-cc-new/.venv/bin/python`.

---

## 1. FR-01 — Task Resource CRUD API

> Module: `taskq_api.api.tasks` + `taskq_api.service.tasks` + `taskq_api.repository.task_repo.*`
> Endpoints: `POST /v1/tasks` (scope `write`), `GET /v1/tasks/{id}` (scope `read`), `GET /v1/tasks` (scope `read`), `DELETE /v1/tasks/{id}` (scope `admin`).

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-01.1 | positive | P0 | Create task with valid write key and valid body | `POST /v1/tasks` headers=`X-API-Key=<write>`, body=`{"name":"build-1","command":"echo hi"}` | `201 Created`, body contains new `id`, `status="pending"`; persisted row in `tasks` | AC-1.1 / SRS §8 #4 |
| TC-01.2 | negative | P0 | Reject empty body validation rule | `POST /v1/tasks` body=`{}` | `422`, `application/problem+json`, `type=/errors/validation`, `detail` carries field path | AC-1.2 |
| TC-01.3 | negative | P0 | Reject empty `name` (non-empty rule) | `POST /v1/tasks` body=`{"name":"","command":"echo hi"}` | `422`, problem+json | AC-1.2 |
| TC-01.4 | boundary | P0 | Reject `name` length = 1001 chars (canonical max 1000) | `POST /v1/tasks` body=`{"name":"a"*1001,"command":"echo hi"}` | `422`, problem+json | AC-1.2 |
| TC-01.5 | boundary | P0 | Accept `name` length = 1000 chars (boundary inclusive) | `POST /v1/tasks` body=`{"name":"a"*1000,"command":"echo hi"}` | `201 Created` | AC-1.1 |
| TC-01.6 | negative | P0 | Reject injection-blacklist characters in `command` (`;`, `&&`, `\|`, backtick, `$`, etc.) | `POST /v1/tasks` body=`{"name":"x","command":"echo hi; rm -rf /"}` | `422`, problem+json, `type=/errors/validation` | AC-1.2 |
| TC-01.7 | negative | P0 | Reject duplicate `name` | `POST /v1/tasks` same name twice with valid write key | First `201`; second `409` problem+json | AC-1.1 / SRS §8 #8 |
| TC-01.8 | positive | P0 | Get single task by id | `GET /v1/tasks/{id}` with read key, id previously created | `200 OK`, body returns full task fields (id, name, command, status, timestamps) | AC-1.1 |
| TC-01.9 | negative | P0 | Get unknown id returns 404 with problem+json | `GET /v1/tasks/00000000-0000-0000-0000-000000000000` | `404 Not Found`, `application/problem+json`, `type=/errors/not-found` | AC-1.3 / SRS §8 #7 |
| TC-01.10 | positive | P0 | List with default pagination | `GET /v1/tasks` read key, seed ≥ 3 tasks | `200 OK`, body has items + opaque `cursor`; default `limit=50` | AC-1.4 / AC-1.5 |
| TC-01.11 | positive | P0 | Cursor pagination iterates full result set | `GET /v1/tasks?limit=2` → follow `cursor` repeatedly | All seeded items returned exactly once, no `offset` parameter anywhere in URL or response | AC-1.4 |
| TC-01.12 | negative | P0 | Listing rejects offset-style pagination | `GET /v1/tasks?offset=0` | `422` (offset is not a valid query field) | AC-1.4 |
| TC-01.13 | boundary | P0 | `limit=50` accepted as default | `GET /v1/tasks?limit=50` | `200 OK`, no error | AC-1.5 |
| TC-01.14 | boundary | P0 | `limit=200` (canonical upper bound) accepted | `GET /v1/tasks?limit=200` | `200 OK`, no error | AC-1.5 |
| TC-01.15 | negative | P0 | `limit=201` rejected | `GET /v1/tasks?limit=201` | `422`, problem+json | AC-1.5 |
| TC-01.16 | negative | P0 | `limit=0` rejected (lower bound) | `GET /v1/tasks?limit=0` | `422`, problem+json | AC-1.5 |
| TC-01.17 | positive | P0 | Filter by status | `GET /v1/tasks?status=pending` with mix of pending/running tasks | Only pending rows returned | AC-1.1 |
| TC-01.18 | positive | P0 | DELETE removes task + result rows in same transaction | `DELETE /v1/tasks/{id}` admin key, after seeding `task_results` row(s) | `204 No Content`; SQL row count in `tasks` AND `task_results` for that id = 0 within same transaction | AC-1.6 |
| TC-01.19 | negative | P0 | DELETE non-admin returns 403, body does not leak existence | `DELETE /v1/tasks/{id}` with write (non-admin) key | `403`, body does not differentiate "exists but forbidden" vs "does not exist" | AC-4.2 / SRS §8 #6 |
| TC-01.20 | edge | P1 | DELETE then GET returns 404 | After `DELETE /v1/tasks/{id}` admin, follow with `GET /v1/tasks/{id}` | Second call `404` problem+json (cascading cache/db consistency) | AC-1.6 |
| TC-01.21 | edge | P1 | GET after partial rollback yields 404 (atomicity check) | Trigger a failed sibling insert + rollback for the same id | Id still 404 after rollback (no orphan) | AC-1.6 |

---

## 2. FR-02 — Task Execution Endpoint

> Module: `taskq_api.service.runner` (high-risk) + `taskq_api.api.tasks.run_task` + `taskq_api.repository.task_repo.record_result`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-02.1 | positive | P0 | `POST /v1/tasks/{id}/run` returns 202 + run_id | Valid write key, valid task id, command `"echo hello"` | `202 Accepted`, body has `run_id` (UUID-ish) | AC-2.1 |
| TC-02.2 | positive | P0 | Run success populates task_results | After TC-02.1, poll DB | One row in `task_results` with `exit_code=0`, `stdout_tail` containing "hello", `duration_ms > 0`, `finished_at` non-null | AC-2.5 |
| TC-02.3 | positive | P0 | Task state machine `pending → running → done` for success path | Subscribe to status transitions during a run | Observed sequence: `pending → running → done` | AC-2.4 |
| TC-02.4 | negative | P0 | Task state machine reaches `failed` on non-zero exit | command `"exit 7"` | `task_results.exit_code=7`, task.status=`failed` | AC-2.4 |
| TC-02.5 | negative | P0 | Task state machine reaches `timeout` when exceeding TASKQ_TASK_TIMEOUT | command `"sleep 30"` with `TASKQ_TASK_TIMEOUT=1` | `task_results` row written with timeout sentinel, status=`timeout`, child process killed (no orphan — see TC-08.4) | AC-2.3 / AC-2.4 / AC-8.4 |
| TC-02.6 | positive | P0 | `GET /v1/tasks/{id}/runs` returns history newest-to-oldest | Run 3 commands in sequence, then `GET /v1/tasks/{id}/runs` | Items returned in DESC order by `finished_at` (or `started_at` desc fallback); first item is the latest run | AC-2.6 |
| TC-02.7 | negative | P0 | `POST /v1/tasks/{id}/run` with read key (insufficient scope) returns 403 | `read`-only key | `403`, problem+json, `type=/errors/forbidden` | AC-2.1 (auth) + AC-4.2 |
| TC-02.8 | negative | P0 | `POST /v1/tasks/{id}/run` on unknown id returns 404 | Random uuid | `404`, problem+json | AC-1.3 / AC-2.1 |
| TC-02.9 | edge | P1 | stdout_tail / stderr_tail truncated to a configurable max | Command emitting > 1 MB output | Only the tail portion is persisted; `stdout_tail` ≤ configured limit | AC-2.5 |
| TC-02.10 | edge | P1 | Concurrent runs of same task id are serialised (state machine consistency) | Fire `run` twice within 100 ms | Both produce distinct `run_id`s, never overlap `running` state in DB | AC-2.4 |

---

## 3. FR-03 — API Key Authentication

> Module: `taskq_api.api.deps.authenticate` + `taskq_api.service.auth.verify_key` + `taskq_api.repository.key_repo.*`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-03.1 | negative | P0 | Missing `X-API-Key` header → 401 + problem+json | `POST /v1/tasks` no auth header | `401`, `application/problem+json`, `type=/errors/unauthenticated` | AC-3.1 / SRS §8 #5 |
| TC-03.2 | negative | P0 | Invalid `X-API-Key` value → 401 | header=`X-API-Key: not-a-real-key` | `401`, problem+json | AC-3.1 |
| TC-03.3 | positive | P0 | Valid key authenticates the request | Header with valid write key | Request proceeds to next dependency | AC-3.1 |
| TC-03.4 | unit | P0 | `api_keys` table stores SHA-256 hash only (64 hex) | After `key create`, query DB | `key_hash` length=64; no plaintext column or value contains the secret | AC-3.2 / SRS §8 #18 |
| TC-03.5 | unit | P0 | Plaintext API key never written to disk | Grep stdout / file system after `key create` | No plaintext occurrences outside the one-time creation print | AC-3.2 / AC-N4.3 |
| TC-03.6 | unit | P0 | API key comparison uses `hmac.compare_digest` | Static / introspection of `taskq_api.service.auth.verify_key` | Call path contains `hmac.compare_digest`; no `==` direct compare on `key_hash` | AC-3.3 / AC-N2.3 |
| TC-03.7 | positive | P0 | `python -m taskq_api key create --scope write` emits plaintext exactly once | Capture stdout | One full plaintext line printed; nothing else | AC-3.4 |
| TC-03.8 | positive | P0 | `key create --scope read/write/admin` stores the correct scope column | Create three keys, inspect DB | Row scopes match CLI flag exactly | AC-3.4 |
| TC-03.9 | negative | P0 | Revoked key (non-null `revoked_at`) is invalid for every `/v1/*` | Revoke a valid key, then attempt every `/v1/*` endpoint | All return `401` | AC-3.5 |
| TC-03.10 | positive | P0 | `/healthz` does not require auth | `GET /healthz` no header | `200 OK` | AC-3.6 |
| TC-03.11 | positive | P0 | `/readyz` does not require auth | `GET /readyz` no header | `200 OK` or `503` (based on DB / migration) — never `401` | AC-3.6 |
| TC-03.12 | edge | P1 | Two keys whose hashes differ only in last byte both fail distinctly | Two synthetic keys with same prefix, different suffix | Both rejected; rejected-with-collision test confirms no timing oracle | AC-N2.3 |
| TC-03.13 | edge | P1 | Empty `X-API-Key: ` header → 401 | Header value empty | `401`, problem+json | AC-3.1 |

---

## 4. FR-04 — Scope Authorization

> Module: `taskq_api.api.deps.require_scope` (the **single** authn/authz dependency per SPEC §6).

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-04.1 | unit | P0 | Scope hierarchy `read < write < admin` is inclusive | `read` key → `read`-required endpoint; `write` key → `read`/`write`-required; `admin` → all | All pass authz | AC-4.1 |
| TC-04.2 | negative | P0 | `read` key on write-required endpoint → 403 | `GET /v1/tasks/{id}` write-required scope check (e.g. `POST /v1/tasks`) | `403`, problem+json, `type=/errors/forbidden` | AC-4.2 |
| TC-04.3 | negative | P0 | `read` key on admin-required endpoint → 403 | `DELETE /v1/tasks/{id}` with read key | `403`, problem+json, body does NOT reveal whether id exists | AC-4.2 / SRS §8 #6 |
| TC-04.4 | negative | P0 | `write` key on admin-required endpoint → 403 | `DELETE /v1/tasks/{id}` with write key | `403`, problem+json | AC-4.2 |
| TC-04.5 | architecture | P0 | Single authn/authz decision point | Introspect FastAPI `app.router.routes` for `/v1/*` routes | Every `/v1/*` route has the same auth dependency attached (no route bypasses it) | AC-4.3 |
| TC-04.6 | edge | P1 | 403 body identical for "exists but forbidden" vs "does not exist" | Two sequences: `DELETE /v1/tasks/<exists>` with write key vs `DELETE /v1/tasks/<random>` with write key | Both responses have byte-identical `type`, `title`, `status`, `detail` (only `correlation_id` may differ) | AC-4.2 / AC-N2.4 |

---

## 5. FR-05 — Rate Limiting (Per-Token Token Bucket)

> Module: `taskq_api.api.deps.rate_limit` + `taskq_api.service.ratelimit.consume` + `taskq_api.repository.rate_repo.*`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-05.1 | positive | P0 | Bucket capacity = `TASKQ_RATE_BURST`, refill = `TASKQ_RATE_PER_SEC` | Issue `N = TASKQ_RATE_BURST` requests within refill window | All `2xx` | AC-5.1 |
| TC-05.2 | negative | P0 | `(N + 1)`-th request → 429 + `Retry-After` | `N = TASKQ_RATE_BURST`, fire one more | `429`, `application/problem+json`, `type=/errors/rate-limited`, `Retry-After` header present and integer seconds | AC-5.2 / SRS §8 #9 |
| TC-05.3 | positive | P0 | After `Retry-After` seconds elapsed, request succeeds again | Wait the indicated interval | Next call returns `2xx` | AC-5.2 |
| TC-05.4 | architecture | P0 | Bucket state lives in DB (single source across workers) | Two parallel ASGI clients using the same token; burst one then the other | Second client sees the bucket already partially drained | AC-5.3 |
| TC-05.5 | architecture | P0 | Update happens in a single transaction with row-level lock | Inspect SQL emitted during `consume` (SQLAlchemy event listener) | `SELECT ... FOR UPDATE` (or equivalent) and `UPDATE` issued inside one `BEGIN; COMMIT;` boundary | AC-5.3 |
| TC-05.6 | negative | P0 | Rate limit is per-token (token A's burst does not affect token B) | Two valid keys fire concurrently | Both keys get their own `TASKQ_RATE_BURST` capacity before any `429` | AC-5.1 |
| TC-05.7 | positive | P0 | `/healthz` is not rate-limited | 1000 rapid `GET /healthz` with no key | All `200`, never `429` | AC-5.4 |
| TC-05.8 | positive | P0 | `/readyz` is not rate-limited | 1000 rapid `GET /readyz` | All `200` or `503` (never `429`) | AC-5.4 |
| TC-05.9 | boundary | P0 | `Retry-After` is non-zero integer | Trigger `429` | `Retry-After` parses as `int(s) > 0` | AC-5.2 |
| TC-05.10 | edge | P1 | Race condition: 10 concurrent requests at bucket boundary result in exactly `N` accepted | Launch N+K concurrent requests | Exactly N succeed, K fail with 429 (no over-allowance) | AC-5.3 / R12 |

---

## 6. FR-06 — Persistence Layer & Transaction Boundaries

> Module: `taskq_api.repository.session` (high-risk) + `taskq_api.repository.task_repo.*`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-06.1 | architecture | P0 | All data access flows through `repository/`; service layer does not hold `Session` | AST grep: `taskq_api.service.**` | No `from sqlalchemy.orm import Session` (or equivalent) outside `repository/` | AC-6.1 / AC-N6.1 |
| TC-06.2 | architecture | P0 | Forbidden contract: `sqlalchemy` importable only from `repository/` | `from sqlalchemy import ...` in `taskq_api.service.runner` | `ImportError` (lint-imports gate) | AC-6.1 / AC-N6.2 |
| TC-06.3 | unit | P0 | Each request uses exactly one Session (context-managed) | Instrument `Session.__init__` / `session_scope` | Per request: 1 session, 1 commit on success, 1 rollback on exception | AC-6.2 |
| TC-06.4 | negative | P0 | Exception in service triggers rollback | Inject a failing operation in the same request | DB unchanged after the request | AC-6.2 |
| TC-06.5 | architecture | P0 | No string-concatenated SQL | `grep -rnE 'f"[^"]*(SELECT|INSERT|UPDATE|DELETE)' 03-development/src/` | Zero hits | AC-6.3 / SRS §8 #17 / AC-N2.2 |
| TC-06.6 | unit | P0 | No f-string / `%` / `+` SQL composition | Regex scan over `03-development/src/` | Zero hits | AC-6.3 |
| TC-06.7 | architecture | P0 | List endpoint emits constant SQL statement count (no N+1) | SQLAlchemy event listener on a 100-row seed | Statement count == statement count at 10-row seed (same N) | AC-6.4 / SRS §8 #14 / AC-N1.3 |
| TC-06.8 | architecture | P0 | Explicit `selectinload` / `joinedload` for relationships | AST grep on repo modules | All relationships loaded via eager-loading options; no implicit lazy load during list | AC-6.4 |
| TC-06.9 | unit | P0 | Connection pool: `pool_size=TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True` | Inspect `create_engine` kwargs | Both kwargs set to the configured values | AC-6.5 |
| TC-06.10 | positive | P0 | `pool_pre_ping=True` causes dead connection to be recycled | Inject a stale connection | Next query succeeds (no `OperationalError` to the client) | AC-6.5 |
| TC-06.11 | edge | P1 | Session rollback closes underlying connection | Trigger exception, then inspect `Session.is_closed` after the request | Session is closed; underlying connection returned to pool | AC-6.2 |

---

## 7. FR-07 — Schema Migration (Alembic v1 → v2 → v3)

> Module: `migrations.versions.v1_initial`, `migrations.versions.v2_tags`, `migrations.versions.v3_split_results` (high-risk).

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-07.1 | e2e | P0 | `alembic upgrade head` against a real SQLite file succeeds | Fresh SQLite file at `./test_migrations.db` | Exit `0`, all v3 tables present (`tasks`, `api_keys`, `tags`, `task_tags`, `task_results`) | AC-7.1 / SRS §8 #12 |
| TC-07.2 | e2e | P0 | `alembic downgrade base` succeeds and leaves no residual tables | After TC-07.1, run `downgrade base` | Exit `0`; sqlite_master contains zero of the v1+ tables | AC-7.2 / SRS §8 #13 |
| TC-07.3 | e2e | P0 | Round-trip reversibility: `upgrade head` → seed → `downgrade -1` → `upgrade head` is byte-identical for sample data (v3 data move is the focus) | Hand-crafted `result_json` row at v2 state | After round-trip, every column of every row in `task_results` (and `tasks` minus the dropped column) is byte-identical | AC-7.3 / SRS §8 #12 |
| TC-07.4 | architecture | P0 | No `op.execute("DROP TABLE ...")` shortcut | Regex scan of `migrations/versions/` | Zero hits | AC-7.4 |
| TC-07.5 | unit | P0 | Migration files have Alembic offline SQL assertions | Run `alembic upgrade head --sql` per revision | Asserted against expected DDL substrings per revision | AC-7.5 |
| TC-07.6 | e2e | P0 | v2 introduces `tasks.name` unique index | After v2 upgrade, attempt two `tasks` rows with same `name` | Second insert raises `IntegrityError` | FR-07 table row 2 |
| TC-07.7 | e2e | P0 | v2 introduces `tags` / `task_tags` (many-to-many) | Insert tag + task + association | Reads back via ORM, both join rows present | FR-07 table row 2 |
| TC-07.8 | e2e | P0 | v3 migrates existing `tasks.result_json` rows into `task_results` columns | At v2 state insert a task with `result_json={"exit_code":0,"stdout_tail":"hi",...}` | After `upgrade head`, `task_results` row carries those values column-by-column | FR-07 table row 3 / AC-7.3 |
| TC-07.9 | e2e | P0 | `downgrade -1` from v3 reverses the data move (no data loss) | After v3 with seeded rows, `downgrade -1` | `tasks.result_json` reappears with original content; no rows lost | AC-7.3 |
| TC-07.10 | unit | P0 | Each migration has a `downgrade()` body (not `pass`) | AST scan of `migrations/versions/v*` | Every revision has a non-trivial `downgrade()` body | AC-7.4 |

---

## 8. FR-08 — Async Runner

> Module: `taskq_api.service.runner` (high-risk).

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-08.1 | unit | P0 | Background execution managed by `asyncio.TaskGroup` | Inspect module imports / instantiation | `asyncio.TaskGroup` is the primary orchestrator | AC-8.1 |
| TC-08.2 | e2e | P0 | Graceful drain on shutdown waits up to `TASKQ_DRAIN_TIMEOUT`; over-budget tasks marked `interrupted` | Start a long-running task (`sleep 30`), trigger shutdown with `TASKQ_DRAIN_TIMEOUT=2` | Service exits within `TASKQ_DRAIN_TIMEOUT + ε`; `task_results` row for the drained task has sentinel `interrupted` | AC-8.2 / SRS §8 #25 |
| TC-08.3 | architecture | P0 | Concurrency capped at `TASKQ_MAX_CONCURRENT` | Fire `N > TASKQ_MAX_CONCURRENT` tasks | At no point do more than `TASKQ_MAX_CONCURRENT` subprocesses exist; surplus queue rather than spawn | AC-8.3 |
| TC-08.4 | e2e | P0 | Task timeout terminates child process via `process.kill()` + `await process.wait()`; no orphan processes | `sleep 30` with `TASKQ_TASK_TIMEOUT=1`; afterwards `ps -ef \| grep sleep` | No `sleep` process remains; `task_results` row has timeout sentinel | AC-8.4 / AC-N3.5 / SRS §8 #25 |
| TC-08.5 | unit | P0 | `asyncio.CancelledError` is re-raised (not swallowed) | Cancel a task mid-execution; also grep `service/runner` | `CancelledError` propagates up the call stack; no `except Exception:` block around runner code | AC-8.5 / AC-N3.3 |
| TC-08.6 | positive | P0 | Concurrent run submission respects `TASKQ_MAX_CONCURRENT` | Fire 10 runs with cap=3 | DB / runtime inspection shows at most 3 `running` states simultaneously | AC-8.3 |
| TC-08.7 | edge | P1 | Drain cancels further submissions but lets in-flight finish | Submit new task during drain | New submission rejected (or queued); in-flight finishes | AC-8.2 |
| TC-08.8 | edge | P1 | Timeout racing with natural completion | `sleep 1` with `TASKQ_TASK_TIMEOUT=10` | Task finishes normally; no spurious kill | AC-8.4 |

---

## 9. FR-09 — Health, Readiness & Metrics

> Module: `taskq_api.api.health` + `taskq_api.service.metrics`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-09.1 | positive | P0 | `/healthz` returns 200 + `{"status":"ok"}` | `GET /healthz` (no auth) | `200 OK`, body `{"status":"ok"}` | AC-9.1 |
| TC-09.2 | positive | P0 | `/readyz` returns 200 when DB reachable AND `alembic current == head` | `GET /readyz` | `200 OK` | AC-9.2 |
| TC-09.3 | negative | P0 | `/readyz` returns 503 with body naming the failed condition when DB unreachable | Stop DB (or override DB URL to unreachable), call `/readyz` | `503`, problem+json, `type=/errors/not-ready`, `detail` mentions DB | AC-9.2 / SRS §8 #10 |
| TC-09.4 | negative | P0 | `/readyz` returns 503 when `alembic current` is behind head (new code deployed without migration) | `alembic downgrade -1`, then `GET /readyz` | `503`, problem+json, `detail` mentions migration | AC-9.2 / AC-9.3 / SRS §8 #11 |
| TC-09.5 | negative | P0 | `/v1/metrics` requires admin scope | read / write key | `403` | AC-9.4 |
| TC-09.6 | positive | P0 | `/v1/metrics` (admin key) reports task counts by status, execution-latency percentiles, rate-limit rejection counts | Seed runs in mixed statuses, fire some 429s | Body contains: per-status counts, p50/p95 latency fields, 429 counter | AC-9.4 |
| TC-09.7 | positive | P0 | `/healthz` survives process aliveness regardless of DB state | Stop DB; `GET /healthz` | Still `200` (process is alive) | AC-9.1 |
| TC-09.8 | edge | P1 | `/readyz` body for DB-down vs migration-down differs but both are 503 with distinguishable `detail` | Trigger each separately | Two distinct `detail` strings, both under `type=/errors/not-ready` | AC-9.2 |

---

## 10. FR-10 — Error Contract (RFC 7807)

> Module: `taskq_api.errors.problem` + `taskq_api.errors.handlers.*` + `taskq_api.api.deps.correlation_id`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-10.1 | integration | P0 | Every non-2xx response has `Content-Type: application/problem+json` | Walk all error-code integration tests | `Content-Type` header equals `application/problem+json` for 401/403/404/409/422/429/500/503 | AC-10.1 |
| TC-10.2 | integration | P0 | problem+json body has fields `type, title, status, detail, instance, correlation_id` | Trigger 404 | All six fields present and non-empty (except `detail` may be empty) | AC-10.2 |
| TC-10.3 | integration | P0 | `type` field is a URI string starting with `/errors/...` | Trigger each error code | `type` is `/errors/validation`, `/errors/unauthenticated`, `/errors/forbidden`, `/errors/not-found`, `/errors/conflict`, `/errors/rate-limited`, `/errors/not-ready`, `/errors/internal` | AC-10.2 / AC-10.5 |
| TC-10.4 | integration | P0 | `status` field equals HTTP status code | Each error code | `status` field == response status code | AC-10.2 |
| TC-10.5 | integration | P0 | `detail` never contains SQL, stack traces, file paths, or schema descriptions | Force a 500 (raise unhandled exception), inspect body | Body does not match regex `(?:SELECT|INSERT|UPDATE|DELETE|FROM\s+\w+|Traceback|File \"...|CREATE TABLE)` | AC-10.3 / SRS §8 #19 |
| TC-10.6 | integration | P0 | `correlation_id` echoed in `X-Correlation-Id` response header AND server log | Trigger any error | Header value == body field == log line substring | AC-10.4 |
| TC-10.7 | integration | P0 | Error-code mapping matches SPEC.md §7 (422/401/403/404/409/429/503/500) | Trigger each | Response status codes match exactly: 422 validation, 401 unauthenticated, 403 forbidden, 404 not-found, 409 conflict, 429 rate-limited, 503 not-ready, 500 internal | AC-10.5 / SRS §8 mapping |
| TC-10.8 | unit | P0 | Problem envelope builders never leak Python repr / `str(exception)` | AST scan of `taskq_api/errors/` | No `repr(`, no `str(exc`, no `f"{exc" in `detail`-forming code paths | AC-10.3 |
| TC-10.9 | integration | P0 | Two concurrent requests receive distinct `correlation_id` values | Issue two requests, capture both | `correlation_id` differs; logs show two distinct entries | AC-10.4 |
| TC-10.10 | edge | P1 | 500 fallback still emits valid problem+json with sanitised `detail` | Force an unexpected exception | Body has all six fields, `detail` is a generic string, `type=/errors/internal` | AC-10.5 |

---

## 11. NFR-01 — Performance & Query Efficiency

> Target module: `taskq_api.api.tasks`.
> Measurement: `pytest-benchmark` over 10k-row fixtures; SQLAlchemy event listener for statement count.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N1.1 | performance | P0 | `GET /v1/tasks/{id}` p95 < 30ms over 10k rows | `pytest-benchmark` suite, ASGI transport | p95 < 30ms | AC-N1.1 / SRS §8 #15 |
| TC-N1.2 | performance | P0 | `GET /v1/tasks?limit=50` p95 < 80ms over 10k rows | `pytest-benchmark` suite | p95 < 80ms | AC-N1.2 |
| TC-N1.3 | architecture | P0 | List endpoint emits constant SQL statement count | SQLAlchemy event listener; 10, 100, 1000 rows | Same statement count across row counts | AC-N1.3 / SRS §8 #14 / AC-6.4 |
| TC-N1.4 | performance | P1 | Performance regression budget — p95 does not regress > 10% across runs | Re-run `pytest-benchmark` after changes | p95 within 10% of baseline | AC-N1.1 |

---

## 12. NFR-02 — HTTP & Data-Layer Security

> Target module: `taskq_api.service.runner`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N2.1 | architecture | P0 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` → 0 hits | Shell grep | Exit `1` (no matches) | AC-N2.1 / SRS §8 #16 |
| TC-N2.2 | architecture | P0 | No string-concatenated SQL (f-string / `%` / `+` building SQL) | Grep + code review | Zero hits | AC-N2.2 / SRS §8 #17 |
| TC-N2.3 | unit | P0 | API key hashing uses `hmac.compare_digest` | Static inspection | Function path uses `hmac.compare_digest` | AC-N2.3 |
| TC-N2.4 | integration | P0 | 403 body never reveals resource existence | Two paths: existing vs non-existing id, write key on DELETE | Both 403 bodies byte-identical (modulo `correlation_id`) | AC-N2.4 / SRS §8 #6 |
| TC-N2.5 | integration | P0 | Error body never contains stack trace / SQL / file path | Force 500, capture body | None of the leakage patterns appear in body | AC-N2.5 / SRS §8 #19 |
| TC-N2.6 | unit | P0 | CORS deny-by-default; allowlist from `TASKQ_CORS_ORIGINS` | Issue cross-origin request with default config | CORS headers absent / rejection; allow-listed origin gets header | AC-N2.6 |
| TC-N2.7 | architecture | P0 | `bandit -r 03-development/src/` reports 0 HIGH and 0 MEDIUM | Run `bandit` | Exit clean, no HIGH/MEDIUM findings | AC-N2.7 / SRS §8 #23 |
| TC-N2.8 | architecture | P1 | Forbidden subprocess flags | `grep -rnE "os\.system\|subprocess\.Popen\(.*shell"` | Zero hits | AC-N2.1 |

---

## 13. NFR-03 — Error Handling, Transactions, Async Correctness

> Target module: `taskq_api.service.runner`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N3.1 | architecture | P0 | Per-request transactions via context manager | Trigger exception in handler | Rollback issued; DB unchanged | AC-N3.1 |
| TC-N3.2 | architecture | P0 | No bare `except:` or `except Exception: pass` | AST scan of `03-development/src/` | Zero hits | AC-N3.2 |
| TC-N3.3 | unit | P0 | `asyncio.CancelledError` re-raised (never swallowed) | Cancel task inside runner; grep for `except Exception:` around runner code | `CancelledError` propagates; no swallowing | AC-N3.3 / AC-8.5 |
| TC-N3.4 | integration | P0 | DB-connection failure → `/readyz` 503 with clear `detail` | Stop DB; `GET /readyz` | `503`, body identifies DB | AC-N3.4 / SRS §8 #10 |
| TC-N3.5 | integration | P0 | Task timeout kills child process; no orphan | Force timeout; `ps -ef \| grep -c <cmd>` after | Zero orphan subprocesses | AC-N3.5 / AC-8.4 / SRS §8 #25 |
| TC-N3.6 | e2e | P0 | Failed migration rolls back transaction; DB stays at previous revision | Inject failing v4 (or break v3 upgrade), run `alembic upgrade head` | Exit non-zero; `alembic current` unchanged | AC-N3.6 |
| TC-N3.7 | architecture | P1 | No silent infinite-retry on connection failures | AST scan | No `while True:` retry around DB connect without bounded counter | AC-N3.4 |

---

## 14. NFR-04 — Sensitive Data Redaction

> Target module: `taskq_api.service.auth`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N4.1 | unit | P0 | `stdout_tail` redaction: line matching `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` is replaced wholesale with `[REDACTED]` | Feed canonical samples through the redactor | Output contains `[REDACTED]` in place of full line; original secret absent | AC-N4.1 |
| TC-N4.2 | unit | P0 | `stderr_tail` redaction applies the same regex | Feed samples through stderr path | Same behaviour | AC-N4.1 |
| TC-N4.3 | unit | P0 | Log lines containing secret patterns are redacted before persistence | Feed sample secrets to logger | Log file contains `[REDACTED]` only | AC-N4.1 |
| TC-N4.4 | unit | P0 | Error-body redactor applies to problem+json `detail` | Force an exception whose `detail` accidentally includes a DSN | `detail` field redacted before serialization | AC-N4.1 |
| TC-N4.5 | architecture | P0 | DB connection string (with password) absent from logs | Run the service, trigger errors; grep logs | No `TASKQ_DB_URL` password fragment | AC-N4.2 / SRS §8 #20 |
| TC-N4.6 | architecture | P0 | DB connection string (with password) absent from `/v1/metrics` response | Admin hits `/v1/metrics` | Body contains no DSN | AC-N4.2 |
| TC-N4.7 | unit | P0 | API-key plaintext printed exactly once and never persisted | Run `key create`, inspect stdout and DB | Stdout: 1 plaintext line. DB: no plaintext column/value | AC-N4.3 / AC-3.2 |
| TC-N4.8 | edge | P1 | Redactor handles multi-secret line (multiple secrets in one line) | `"Authorization: Bearer abc token=xyz postgres://u:p@h/d"` | Single `[REDACTED]` replacement; no partial leak | AC-N4.1 |

---

## 15. NFR-05 — Documentation Coverage

> Target module: `taskq_api.api.tasks`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N5.1 | architecture | P0 | 100% of public functions/classes have docstrings containing `[FR-XX]` or `[NFR-XX]` reference | AST scanner over `03-development/src/taskq_api/` | Every public symbol has a docstring with at least one reference | AC-N5.1 |
| TC-N5.2 | architecture | P0 | Every API endpoint appears in `/openapi.json` with `summary` and `description` populated | `GET /openapi.json` | Each route's operation has non-empty `summary` and `description` | AC-N5.2 |
| TC-N5.3 | architecture | P1 | Docstring reference uses FR/NFR IDs that actually exist in the spec | Cross-check references | Zero references to non-existent IDs | AC-N5.1 |

---

## 16. NFR-06 — Architecture Layering Contract

> Target module: `taskq_api.repository.session`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N6.1 | architecture | P0 | `.importlinter` exists at project root and declares `api > service > repository > models` | File presence + content scan | All four layers declared; `config` and `errors` marked independent | AC-N6.1 |
| TC-N6.2 | architecture | P0 | Forbidden contract: `sqlalchemy` imports banned outside `repository/` | Inspect `.importlinter` and run `lint-imports` | Forbidden contract present | AC-N6.2 |
| TC-N6.3 | architecture | P0 | `lint-imports` exits 0 | Run `lint-imports` | Exit `0` | AC-N6.3 / SRS §8 #21 |
| TC-N6.4 | architecture | P0 | No `ignore_imports` wildcard or downgrade loophole | Inspect `.importlinter` content | No `ignore_imports = **`; no contract downgrade comments | AC-N6.4 |
| TC-N6.5 | architecture | P0 | `from sqlalchemy ...` in `service/` raises `ImportError` (lint gate enforces) | Try to import sqlalchemy from `taskq_api.service.*` module | Import error or lint-imports failure | AC-N6.2 |
| TC-N6.6 | architecture | P1 | `config.py` and `errors.py` are independence modules (no upward imports) | Inspect lint contract | Independence flags present | AC-N6.1 |

---

## 17. NFR-07 — Dependency & License Compliance

> Target module: `taskq_api.config`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N7.1 | architecture | P0 | Every runtime dep in `requirements.txt` pinned with `==` | File scan | All lines matching `^[^#]*==` (no `>=`, `<`) | AC-N7.1 |
| TC-N7.2 | architecture | P0 | `requirements.lock` exists and pins transitives | File presence + scan | All transitive entries pinned with `==` | AC-N7.1 |
| TC-N7.3 | architecture | P0 | `pip-licenses --format=json --with-system` reports all licenses ∈ allowlist | Run command | Every license ∈ {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF} | AC-N7.2 / SRS §8 #22 |
| TC-N7.4 | architecture | P0 | SBOM at `08-config/SBOM.json` with `name/version/license/direct-or-transitive` per dep | File schema validation | All required fields present for every dep | AC-N7.4 |
| TC-N7.5 | architecture | P1 | Negative test: introducing a GPL-licensed package fails CI | Inject mock dep with `GPL-3.0` into lock | `pip-licenses` gate fails; SBOM schema validator fails | AC-N7.2 |

---

## 18. NFR-08 — Mutation Testing

> Target module: `taskq_api.service.tasks` (scope: `service/`, `repository/`).

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N8.1 | architecture | P0 | `.methodology/harness_config.json` has `features.mutation_testing: true` | File inspection | Field present and `true` | AC-N8.1 |
| TC-N8.2 | mutation | P0 | `mutmut run` then score extraction yields mutation score ≥ 70 | Run mutmut over `service/` + `repository/` | Score ≥ 70 | AC-N8.2 / SRS §8 #24 |
| TC-N8.3 | architecture | P0 | Scope restricted to `service/` + `repository/`; rationale recorded | Inspect `harness_config.json` | Scope-restriction block present with rationale string | AC-N8.3 |
| TC-N8.4 | mutation | P1 | Allowed exclusions (e.g. trivial getters) are documented | Inspect exclusions | List non-empty + documented | AC-N8.2 |

---

## 19. NFR-09 — Verification Authenticity (Zero-Skip Rule)

> Target module: `taskq_api.service.runner`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N9.1 | architecture | P0 | `pytest 03-development/tests -q` reports `skipped == 0` | Run pytest | `0 skipped` in summary | AC-N9.1 / SRS §8 #1 |
| TC-N9.2 | architecture | P0 | Every test function has ≥ 1 `assert` (`zero_assert == 0`) | AST scanner over `03-development/tests/` | Zero tests without `assert` | AC-N9.2 |
| TC-N9.3 | architecture | P0 | No exclusions via `--ignore`, `-k`, `--deselect`, `collect_ignore`, testpaths removal | Scan `pyproject.toml`, `pytest.ini`, `conftest.py` | Zero exclusion mechanisms targeting the test directories under test | AC-N9.3 |
| TC-N9.4 | e2e | P0 | FR-07 migration round-trip tested against real SQLite file (not in-memory mock) | Inspect TC-07.3 + migration tests | Tests use `sqlite:///...` file URL, not `:memory:` | AC-N9.4 / SRS §8 #12 |
| TC-N9.5 | architecture | P0 | `TRACEABILITY_MATRIX.md` `VERIFIED` cells only set after actual test pass | Re-run failing test, inspect matrix | Cell flips to `PENDING`/blank if test fails | AC-N9.5 |
| TC-N9.6 | architecture | P0 | Coverage report shows TOTAL 100% on `pytest --cov=03-development/src --cov-report=term` | Run pytest with coverage | `TOTAL 100%` | SRS §8 #2 |

---

## 20. NFR-10 — Integration Coverage

> Target module: `taskq_api.api.tasks`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N10.1 | architecture | P0 | Integration-suite-only line coverage of `03-development/src/` ≥ 80% | `pytest --cov=03-development/src --cov-report=term --cov-tests=integration` | TOTAL ≥ 80% | AC-N10.1 / SRS §8 #3 |
| TC-N10.2 | architecture | P0 | Integration tests driven via `httpx.AsyncClient(transport=ASGITransport(app))`; no direct handler calls | Static inspection of integration tests | All tests use ASGI transport; no `from taskq_api.api.tasks import create_task` direct-call patterns | AC-N10.2 |
| TC-N10.3 | integration | P0 | Each error code (401/403/404/409/422/429/503) covered at least once | Enumerate error-code tests | At least 1 test per code | AC-N10.3 |
| TC-N10.4 | integration | P0 | Full CRUD chain covered | Integration tests | POST → GET → LIST → DELETE end-to-end passes | AC-N10.3 |
| TC-N10.5 | integration | P0 | Migration round-trip covered in integration | TC-07.3 promoted into integration suite | Passes on real SQLite file | AC-N10.3 |
| TC-N10.6 | integration | P0 | Rate-limit trigger + recovery covered | TC-05.2 + TC-05.3 promoted | Both pass | AC-N10.3 |
| TC-N10.7 | integration | P0 | Graceful drain covered | TC-08.2 promoted | Passes | AC-N10.3 |

---

## 21. NFR-11 — Readability

> Target module: `taskq_api.api.tasks`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N11.1 | architecture | P0 | Project MI (LLOC-weighted) ≥ 80 | Run radon-mi | Score ≥ 80 | AC-N11.1 |
| TC-N11.2 | architecture | P0 | Single-function CC ≤ 10 | Run radon-cc | No function above `C` (CC=10) | AC-N11.2 |
| TC-N11.3 | architecture | P0 | Single file ≤ 400 lines | Line-count scan over `03-development/src/` | Zero files over 400 | AC-N11.3 |
| TC-N11.4 | architecture | P0 | Single directory ≤ 15 files | File-count scan | Zero directories over 15 | AC-N11.3 |
| TC-N11.5 | architecture | P0 | Each API handler ≤ 40 lines | AST scan of `taskq_api.api.*` handlers | Zero handlers over 40 LOC | AC-N11.4 |
| TC-N11.6 | architecture | P1 | Business logic lives in `service/` | AST grep for repository imports in `api/*.py` (excluding `deps.py`) | No repository imports in handler files | AC-N11.4 |

---

## 22. NFR-12 — System Verification Target

> Target module: `taskq_api.app`.

| ID | Cat. | Pri. | Description | Input | Expected Output | Spec anchor |
|----|------|------|-------------|-------|-----------------|-------------|
| TC-N12.1 | architecture | P0 | `Makefile` declares `verify-system` chaining the four canonical steps | File inspection | Target exists with all four commands in order | AC-N12.1 |
| TC-N12.2 | e2e | P0 | `make verify-system` exits 0 and prints `verify-system: PASS` | Run `make verify-system` | Exit `0`; stdout contains `verify-system: PASS` | AC-N12.2 / SRS §8 #27 |
| TC-N12.3 | e2e | P0 | Each step in the chain is observable in stdout | Capture stdout | `alembic upgrade head` output, pytest summary, `/healthz` + `/readyz` smoke results, downgrade/upgrade round-trip logs all present | AC-N12.1 |
| TC-N12.4 | edge | P1 | If any step fails, exit code reflects failure | Inject a failing test | Exit non-zero; `verify-system: PASS` absent | AC-N12.2 |

---

## 23. Coverage Matrix — FR ↔ TC

| FR / NFR | Test Cases | Count |
|----------|------------|-------|
| FR-01 | TC-01.1..TC-01.21 | 21 |
| FR-02 | TC-02.1..TC-02.10 | 10 |
| FR-03 | TC-03.1..TC-03.13 | 13 |
| FR-04 | TC-04.1..TC-04.6 | 6 |
| FR-05 | TC-05.1..TC-05.10 | 10 |
| FR-06 | TC-06.1..TC-06.11 | 11 |
| FR-07 | TC-07.1..TC-07.10 | 10 |
| FR-08 | TC-08.1..TC-08.8 | 8 |
| FR-09 | TC-09.1..TC-09.8 | 8 |
| FR-10 | TC-10.1..TC-10.10 | 10 |
| NFR-01 | TC-N1.1..TC-N1.4 | 4 |
| NFR-02 | TC-N2.1..TC-N2.8 | 8 |
| NFR-03 | TC-N3.1..TC-N3.7 | 7 |
| NFR-04 | TC-N4.1..TC-N4.8 | 8 |
| NFR-05 | TC-N5.1..TC-N5.3 | 3 |
| NFR-06 | TC-N6.1..TC-N6.6 | 6 |
| NFR-07 | TC-N7.1..TC-N7.5 | 5 |
| NFR-08 | TC-N8.1..TC-N8.4 | 4 |
| NFR-09 | TC-N9.1..TC-N9.6 | 6 |
| NFR-10 | TC-N10.1..TC-N10.7 | 7 |
| NFR-11 | TC-N11.1..TC-N11.6 | 6 |
| NFR-12 | TC-N12.1..TC-N12.4 | 4 |
| **TOTAL** | | **175** |

---

## 24. Coverage Matrix — Category Distribution (per FR/NFR)

Every FR and NFR exercises at least one of each category where applicable:

| FR / NFR | positive | negative | boundary | edge |
|----------|----------|----------|----------|------|
| FR-01 | TC-01.1, .8, .10, .11, .17, .18 | TC-01.2, .3, .6, .7, .9, .12, .16, .19 | TC-01.4, .5, .13, .14, .15 | TC-01.20, .21 |
| FR-02 | TC-02.1, .2, .3 | TC-02.4, .5, .7, .8 | TC-02.5 (timeout) | TC-02.9, .10 |
| FR-03 | TC-03.3, .7, .8, .10, .11 | TC-03.1, .2, .9 | — | TC-03.12, .13 |
| FR-04 | TC-04.1 | TC-04.2, .3, .4 | — | TC-04.6 |
| FR-05 | TC-05.1, .3, .7, .8 | TC-05.2 | TC-05.9 | TC-05.4, .5, .6, .10 |
| FR-06 | TC-06.3, .10 | TC-06.4 | TC-06.7 | TC-06.11 |
| FR-07 | TC-07.1, .2, .6, .7, .8 | TC-07.4 | — | TC-07.9 |
| FR-08 | TC-08.6 | TC-08.4 | TC-08.3 (cap) | TC-08.7, .8 |
| FR-09 | TC-09.1, .2, .6, .7 | TC-09.3, .4, .5 | — | TC-09.8 |
| FR-10 | TC-10.1..10.4, .6 | TC-10.5, .7 | — | TC-10.9, .10 |
| NFR-01 | TC-N1.1, .2 | — | TC-N1.3 (constant-N) | TC-N1.4 |
| NFR-02 | TC-N2.6 | TC-N2.1, .2, .4, .5, .7, .8 | — | TC-N2.3 (HMAC), TC-N4.8 |
| NFR-03 | TC-N3.1 | TC-N3.2, .3, .4, .5, .6 | — | TC-N3.7 |
| NFR-04 | TC-N4.7 | TC-N4.5, .6 | — | TC-N4.1, .2, .3, .4, .8 |
| NFR-05 | TC-N5.1, .2 | — | — | TC-N5.3 |
| NFR-06 | TC-N6.1, .2, .3, .5 | TC-N6.4 | — | TC-N6.6 |
| NFR-07 | TC-N7.1, .2, .3, .4 | TC-N7.5 | — | — |
| NFR-08 | TC-N8.1 | TC-N8.2 (threshold gate) | TC-N8.3 (scope) | TC-N8.4 |
| NFR-09 | TC-N9.1, .2, .5, .6 | TC-N9.3 (forbidden exclusions) | — | TC-N9.4 (real SQLite) |
| NFR-10 | TC-N10.1, .2, .4..7 | — | — | TC-N10.3 (per error code) |
| NFR-11 | TC-N11.1, .2 | TC-N11.3, .4, .5, .6 | — | — |
| NFR-12 | TC-N12.1, .2, .3 | TC-N12.4 | — | — |

---

## 25. Execution Order (one-shot pre-flight)

1. Build the 10k-row performance fixture (TC-N1.1, TC-N1.2).
2. Provision real SQLite migration file fixture (TC-07.1).
3. Run architecture gates first (cheapest, broadest): TC-N2.1, .7; TC-N6.3; TC-N9.1, .2, .3; TC-N11.*; TC-N12.1.
4. Run unit tests (FR-03 hashing, FR-06 repository, FR-08 runner internals, NFR-04 redaction).
5. Run integration suite (every TC-XX.* not classified above).
6. Run `pytest-benchmark` for NFR-01.
7. Run `mutmut` for NFR-08.
8. Run `make verify-system` for NFR-12.

---

## 26. Acceptance Gate Mapping

`TEST_PLAN.md` covers every FR in `.methodology/quality_manifest.json` (FR-01..FR-10) and every NFR (NFR-01..NFR-12) with positive, negative, boundary, and edge categories where applicable. Total test cases: **175**. Categories present per FR/NFR as enumerated in §24. All test cases trace to a SPEC.md §8 row or an explicit AC id, which feeds `TRACEABILITY_MATRIX.md`.