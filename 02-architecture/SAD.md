# Software Architecture Document (SAD) — taskq-api

> Software Architecture Document for `taskq-api` (v1.0.0). Authored
> from `SPEC.md` v1.0.0 (10 FR / 12 NFR / 12 env). Phase 2 of the
> harness-methodology progressive validation testbed (round 2 of 3).

---

## 1. Architecture Overview

`taskq-api` is a Python 3.11 ASGI service that exposes a REST API for
submitting, querying, and executing tasks. It is a single-process FastAPI
application backed by SQLAlchemy 2.x ORM, an Alembic-driven schema, and
an in-process `asyncio` background executor.

### 1.1 System Verification Target

> **Every exit gate (2, 3 and 4)**: the harness executes
> `make verify-system`. A non-zero exit fails the gate. The target name
> is fixed — the harness always calls `make verify-system`.

**Makefile target**: `verify-system`

**Exercises** (real acceptance criteria against real dependencies — the
rule says the step must be able to fail):

1. `alembic upgrade head` against a real SQLite file (NFR-12, FR-07).
2. Full test suite (NFR-09) including integration tests via
   `httpx.ASGITransport` (NFR-10).
3. Service startup (`uvicorn taskq_api.app:app`) + smoke
   `GET /healthz` and `GET /readyz` (FR-09).
4. `alembic downgrade base` → re-`upgrade head` round-trip (FR-07
   reversible migration, NFR-12 #4).
5. Stdout must contain `verify-system: PASS`; non-zero exit = gate fail.

The target runs the real `taskq_api` entry point (the program a user
would run) and depends on `taskq_api.repository.session`,
`taskq_api.service.runner`, `taskq_api.service.auth`,
`migrations/versions/v3_split_results.py` — the four modules flagged
high-risk in SPEC §10.

### 1.2 Architecture Style

Layered architecture with **strict downward-only dependencies** enforced
by `import-linter` (NFR-06):

```
api  (L4) ── FastAPI routes, deps
  │
service  (L3) ── business logic, async execution
  │
repository  (L2) ── only layer allowed to import sqlalchemy
  │
models  (L1) ── ORM + pydantic schemas

independence: config, errors  (no layer dependency; L0)
```

A separate `migrations/` tree owns Alembic revisions and the
`__main__.py` CLI for `migrate` / `key create` / `healthcheck`.

### 1.3 Key Cross-Cutting Decisions

| Concern | Decision | FR/NFR |
|---|---|---|
| Auth | `X-API-Key` header, SHA-256 hashed at rest, `hmac.compare_digest` | FR-03 / NFR-02 |
| Authz | Per-token scope hierarchy `read < write < admin`, single FastAPI dependency | FR-04 / NFR-02 |
| Rate limit | Per-token token bucket in DB (row-locked single transaction) | FR-05 |
| Errors | RFC 7807 `application/problem+json` on every non-2xx; `detail` whitelist | FR-10 / NFR-02 |
| DB access | Repository layer owns `Session`; business layer never imports `sqlalchemy` | FR-06 / NFR-06 |
| Migration | 3 Alembic revisions, round-trip reversible, real SQLite in tests | FR-07 / NFR-09 |
| Async | `asyncio.TaskGroup` background runner, `CancelledError` re-raised, graceful drain | FR-08 / NFR-03 |
| Subprocess | `asyncio.create_subprocess_exec` with `shell=False`, timeout kills the child | FR-02 / FR-08 / NFR-02 |

---

## 2. Module Design

### 2.1 Directory Structure Design Principles

The SPEC §6 tree is the contract. CRG treats each subdirectory of
`taskq_api/` as one community, producing **5 expected source
communities** plus the `migrations/` community (test directories are
excluded from CRG scoring). This sits within the CRG-recommended 3–6
source communities and keeps each community ≤ 50 nodes.

| Community (dir) | Sibling files | Hub strategy | High-risk? |
|---|---|---|---|
| `taskq_api/` (root) | `__init__.py`, `__main__.py`, `app.py`, `config.py`, `errors.py` | `config` and `errors` are called by `app` and `__main__` from every function body | — |
| `taskq_api/api/` | `__init__.py`, `deps.py`, `tasks.py`, `health.py` | `deps.py` is the cross-cutting hub (auth, scope, rate-limit) called by every handler | — |
| `taskq_api/service/` | `__init__.py`, `tasks.py`, `runner.py`, `auth.py`, `ratelimit.py`, `metrics.py` | ≥2 hub functions: `_enter_service_unit(...)` (transaction wrapper) + `ServiceError` base; ≥5 siblings → both hubs are called from every function body in siblings | `runner.py` |
| `taskq_api/repository/` | `__init__.py`, `session.py`, `task_repo.py`, `key_repo.py`, `rate_repo.py` | `session.transaction()` context manager is the single hub called by every `*_repo` function body | `session.py` |
| `taskq_api/models/` | `__init__.py`, `orm.py`, `schemas.py` | `orm.py` table classes imported by `schemas.py`; `schemas.py` called by every API handler | — |
| `migrations/versions/` | `v1_initial.py`, `v2_tags.py`, `v3_split_results.py` | Each revision upgrades the prior — linear pipeline (A→B→C) chain is the CRG-approved exception | `v3_split_results.py` |

**Edge budget (per SPEC §10 community analysis):**

For `service/` (5 siblings — `tasks`, `runner`, `auth`, `ratelimit`,
`metrics`; `__init__.py` is package init), ~18 function bodies across
non-init siblings, ~6 external edges per file for `asyncio` / `shlex` /
`subprocess` / `hmac` / `datetime`:
- E ≈ 30 external edges → need I ≥ ⌈0.4286 × 30⌉ = 13 internal edges.
- 5 siblings × ~4 function bodies × calls to `service.auth`/
  `service.ratelimit` hubs ≈ 18+ internal edges, safely above threshold.

For `repository/` (4 siblings, all importing `sqlalchemy`): the external
edge count is ~5 per file (sqlalchemy + contextlib + typing). I ≥
⌈0.4286 × 20⌉ = 9 internal edges. 4 repos × 3 function bodies each
calling `session.transaction()` = 12 internal edges, above threshold.

### 2.2 Module Catalogue (FR → Module traceability)

Every FR (SPEC §3) and NFR (SPEC §4) is enumerated below and mapped
to its owning module(s) per SPEC §6.

#### 2.2.1 L0 — Independence (`taskq_api/` root files)

| Attribute | Value |
|---|---|
| **Module(s)** | `taskq_api.config`, `taskq_api.errors` |
| **Responsibility** | `config` reads all 12 `TASKQ_*` env vars (SPEC §5.1); `errors` provides the RFC 7807 `application/problem+json` factory and the `ProblemException` hierarchy. Both are imported by every other layer. |
| **External Interface** | `config.get_settings()`; `errors.problem_response(status, type_uri, detail, correlation_id)`, `errors.ProblemException` |
| **Dependencies** | stdlib only (`os`, `dataclasses`, `logging`, `typing`) — no `sqlalchemy`, no `fastapi` |
| **NFR owners** | NFR-04 (config must redact DB URL from logs), NFR-10 (error contract) |

**Logical constraints**

- `config` and `errors` MUST NOT import from `fastapi` or `sqlalchemy`
  (independence rule; NFR-06).
- `config.get_settings()` must apply NFR-04 redaction to `TASKQ_DB_URL`
  before any `__repr__` / log emission.

#### 2.2.2 L1 — Models

| Attribute | Value |
|---|---|
| **Module(s)** | `taskq_api.models.orm`, `taskq_api.models.schemas` |
| **Responsibility** | `orm.py` declares `tasks`, `api_keys`, `tags`, `task_tags`, `task_results`, `rate_buckets` (SPEC §5.2). `schemas.py` declares pydantic v2 request/response models for every API endpoint. |
| **External Interface** | ORM table classes; pydantic `TaskCreate`, `TaskRead`, `RunCreate`, `RunRead`, `ProblemDetail` |
| **Dependencies** | `sqlalchemy.orm` (declarative base ONLY — `DeclarativeBase`/`Mapped`/`mapped_column`; no `select` / `execute` / query APIs; enforced by `architecture_constraints.models_sqlalchemy_orm_declarative_only` and C-14 carve-out below), `pydantic` v2 |
| **FR owners** | FR-01 (schemas), FR-02 (`TaskRead` includes `run_id`), FR-07 (table definitions), FR-10 (`ProblemDetail` schema) |

**Logical constraints**

- `models/` MUST NOT import from `service/` or `api/` (downward-only).
- `orm.py` is the only authoritative schema; Alembic revisions are
  generated from it (FR-07).

#### 2.2.3 L2 — Repository

| Attribute | Value |
|---|---|
| **Module(s)** | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` |
| **Responsibility** | `session.py` owns the `Session` factory + `transaction()` context manager (FR-06). Each `*_repo.py` exposes a narrow set of operations on its aggregate. **This is the only layer that may import `sqlalchemy`** (NFR-06). |
| **External Interface** | `session.transaction()` context manager; `task_repo.{create,get,list,delete,get_results}`; `key_repo.{create,get_by_hash,revoke,get_by_id}`; `rate_repo.{consume,peek,reset}` |
| **Dependencies** | `sqlalchemy`, `sqlalchemy.orm`, `taskq_api.models.orm` |
| **FR owners** | FR-01 (task_repo), FR-03 (key_repo), FR-05 (rate_repo), FR-06 (session), FR-07 (each repo's first migration target), FR-09 (`session.healthy()` for `/readyz`) |

**Logical constraints**

- All `*_repo` functions accept an injected `Session` and NEVER create
  one (transaction boundary is the caller's job — FR-06).
- `selectinload` / `joinedload` are mandatory on any multi-relation
  read (NFR-01 N+1 guard).
- Repository functions accept validated domain inputs from `service/`;
  they MUST NOT raise HTTP exceptions.

#### 2.2.4 L3 — Service

| Attribute | Value |
|---|---|
| **Module(s)** | `taskq_api.service.tasks`, `taskq_api.service.runner`, `taskq_api.service.auth`, `taskq_api.service.ratelimit`, `taskq_api.service.metrics` |
| **Responsibility** | All business logic. `tasks` implements FR-01 CRUD orchestration; `runner` owns the async subprocess executor + drain (FR-02, FR-08); `auth` owns key validation, scope check, redaction (FR-03, FR-04, NFR-04); `ratelimit` owns token-bucket policy (FR-05); `metrics` owns the metrics aggregation path used by `/v1/metrics` (FR-09) — it is the only service entry point allowed to read aggregate counts, and it MUST NOT expose any field that the repository layer would consider sensitive (DSN fragments, raw key material). |
| **External Interface** | Plain Python functions: `tasks.create_task(cmd, name, actor)`, `tasks.get_task(id, actor)`, `runner.run_task(id, command)`, `auth.resolve_api_key(key)`, `auth.check_scope(actor, required)`, `ratelimit.consume(key_id)`, `metrics.snapshot()` |
| **Dependencies** | `taskq_api.repository.*`, `taskq_api.models.*`, `asyncio`, `shlex`, `hmac`, `hashlib` |
| **FR owners** | FR-01 (tasks), FR-02 (runner), FR-03 (auth), FR-04 (auth), FR-05 (ratelimit), FR-08 (runner), FR-09 (metrics — service-level entry point for `/v1/metrics`), FR-10 (auth wraps problem+json) |

**Logical constraints**

- `service/` MUST NOT import `sqlalchemy` directly (NFR-06 forbidden
  contract — verified by `import-linter`). `metrics.snapshot()` reads
  aggregate counts via the repository layer only; `/v1/metrics` reaches
  the DB through `service.metrics` → `repository.*_repo`, never through
  a direct api→repository edge.
- `runner.run_task` MUST use `asyncio.create_subprocess_exec` with
  `shell=False` (NFR-02). Timeouts MUST call `proc.kill()` then
  `await proc.wait()` to guarantee no orphans (FR-08, R8).
- `auth.resolve_api_key` MUST use `hmac.compare_digest` (NFR-02).
- `auth` MUST redact `(sk-…|token=…|Bearer …|postgres(ql)?://…)` before
  logging or returning (NFR-04).
- `asyncio.CancelledError` MUST NOT be caught by any `except Exception`
  handler (NFR-03, R7).

#### 2.2.5 L4 — API

| Attribute | Value |
|---|---|
| **Module(s)** | `taskq_api.api.deps`, `taskq_api.api.tasks`, `taskq_api.api.health` |
| **Responsibility** | `deps.py` is the **single** auth + scope + rate-limit FastAPI dependency (FR-04 "must be one place"). `tasks.py` declares `/v1/tasks/*` routes. `health.py` declares `/healthz`, `/readyz`, `/v1/metrics`. |
| **External Interface** | FastAPI `APIRouter`s; `Depends(require_scope("read"|"write"|"admin"))` |
| **Dependencies** | `fastapi`, `taskq_api.service.*`, `taskq_api.errors`, `taskq_api.config` |
| **FR owners** | FR-01, FR-02 (tasks), FR-03, FR-04 (deps), FR-05 (deps), FR-09 (health) |

**Logical constraints**

- Each handler ≤ 40 lines; business logic is delegated to `service/`
  (NFR-11).
- Scope check happens **before** any resource lookup, so 403 bodies
  cannot leak resource existence (FR-04, R4).
- All non-2xx responses are produced via `errors.problem_response` →
  guaranteed `application/problem+json` (FR-10).
- `/healthz` and `/readyz` are the only unauthenticated routes
  (FR-09); `/v1/metrics` requires `admin` scope.

#### 2.2.6 Entry Points

| Attribute | Value |
|---|---|
| **Module(s)** | `taskq_api.app`, `taskq_api.__main__` |
| **Responsibility** | `app.py` builds the FastAPI app, wires routers + middleware, registers the lifespan handler that starts/stops the background runner. `__main__.py` provides the `python -m taskq_api` CLI (`migrate`, `key create`, `healthcheck`). |
| **External Interface** | `app.app:ASGIApp` (uvicorn target); CLI subcommands |
| **Dependencies** | `fastapi`, `uvicorn`, `taskq_api.api.*`, `taskq_api.config`, `taskq_api.errors`, `alembic` (CLI only) |

**Logical constraints**

- `app` and `__main__` live inside the `taskq_api/` community with
  `config` + `errors` as siblings so the unavoidable external imports
  (`fastapi`, `uvicorn`, `argparse`, `asyncio`) are offset by hub calls
  to `config.get_settings()` and `errors.problem_response(...)` from
  every function body.

#### 2.2.7 Migrations (separate community)

| Attribute | Value |
|---|---|
| **Module(s)** | `migrations/env.py`, `migrations/versions/v1_initial.py`, `migrations/versions/v2_tags.py`, `migrations/versions/v3_split_results.py` |
| **Responsibility** | Alembic revisions v1 → v2 → v3; v3 includes data migration from `tasks.result_json` to `task_results` (SPEC §5.2, FR-07). |
| **Dependencies** | `alembic`, `sqlalchemy`, `taskq_api.models.orm` |
| **FR owners** | FR-07 (all three revisions), NFR-09 (real-DB round-trip tests) |

**Logical constraints**

- Every revision MUST have a working `downgrade()` (FR-07).
- v3 `downgrade()` MUST round-trip data back into `tasks.result_json`
  before dropping `task_results` (FR-07, R1).
- No `op.execute("DROP TABLE …")` shortcuts (FR-07).

### 2.3 Circular-Dependency Audit

No cycle exists. The dependency DAG is strictly downward:

```
api ──→ service ──→ repository ──→ models
 │         │             │
 └──→ config/errors ←────┴── (independence; no upward edges)
```

`migrations/` references `models/` only. `__main__` and `app` import
only `config` + `errors` + `api` + `alembic` (no upward edges).
`import-linter` (NFR-06) mechanically enforces this and will fail the
build if a cycle is introduced.

---

## 3. Interfaces & Data Flows

### 3.1 Public HTTP API

All `/v1/*` routes require `X-API-Key`. All non-2xx bodies are RFC 7807
`application/problem+json` (FR-10).

| Method | Path | Scope | Handler module | Service module |
|---|---|---|---|---|
| `POST` | `/v1/tasks` | `write` | `api.tasks` | `service.tasks` |
| `GET` | `/v1/tasks/{id}` | `read` | `api.tasks` | `service.tasks` |
| `GET` | `/v1/tasks` | `read` | `api.tasks` | `service.tasks` |
| `DELETE` | `/v1/tasks/{id}` | `admin` | `api.tasks` | `service.tasks` |
| `POST` | `/v1/tasks/{id}/run` | `write` | `api.tasks` | `service.runner` |
| `GET` | `/v1/tasks/{id}/runs` | `read` | `api.tasks` | `service.tasks` |
| `GET` | `/v1/metrics` | `admin` | `api.health` | `service.metrics` |
| `GET` | `/healthz` | (none) | `api.health` | — |
| `GET` | `/readyz` | (none) | `api.health` | `repository.session` |

### 3.2 Task Creation Flow (FR-01)

```
client ──HTTP POST /v1/tasks──▶ api.deps.require_scope("write")
                                  │ 401/403 problem+json on fail
                                  ▼
                              api.tasks.create_task
                                  │ validates body via models.schemas.TaskCreate
                                  ▼
                              service.tasks.create_task
                                  │ (1) calls service.auth.redact(cmd)        [hub call → internal edge]
                                  │ (2) calls service.ratelimit.consume(actor)  [hub call → internal edge]
                                  │ (3) calls repository.session.transaction() [hub call → internal edge]
                                  ▼
                              repository.task_repo.create(...)
                                  │ SELECT nextval + INSERT
                                  ▼
                              commit → return TaskRead
                                  ▼
                              201 Created + problem+json on failure
```

### 3.3 Task Execution Flow (FR-02 / FR-08)

```
client ──HTTP POST /v1/tasks/{id}/run──▶ api.tasks.run_task
                                          │ 202 Accepted, body: {"run_id": "..."}
                                          ▼
                                      service.runner.run_task
                                          │ queue in asyncio.TaskGroup
                                          ▼
                                      (background) service.runner._execute
                                          │ subprocess_exec(shell=False)  [NFR-02]
                                          │ wait_for(TASKQ_TASK_TIMEOUT)   [FR-08]
                                          │ on timeout: proc.kill(); await proc.wait()  [R8]
                                          │ on success: repository.task_repo.write_result(...)
                                          ▼
                                      task_results table updated; status transitions
```

### 3.4 Auth / Authz Flow (FR-03 / FR-04)

```
every /v1/* request
    │
    ▼
api.deps.require_scope("read"|"write"|"admin")
    │ 1. extract X-API-Key header
    │ 2. SHA-256 hash; constant-time compare via hmac.compare_digest
    │ 3. service.auth.resolve_api_key(hash) → ApiKeyRecord (or None → 401)
    │ 4. check revoked_at is null (or 401)
    │ 5. service.auth.check_scope(record, required) (or 403 — body never reveals resource existence)
    │ 6. service.ratelimit.consume(key_id)  (or 429 + Retry-After)
    ▼
inject ApiKeyRecord as the handler's `actor` argument
```

### 3.5 Migration Flow (FR-07)

```
alembic upgrade head    → v1 → v2 → v3 (with data copy)
alembic downgrade -1    → v3 reverses data back to tasks.result_json, drops task_results
alembic upgrade head    → v3 re-runs forward
                          ↑ SPEC §8 #12: row-by-row equality of migrated sample data
```

### 3.6 Data Flow Summary

| Origin | Sink | Carrier | NFR |
|---|---|---|---|
| HTTP request body | `service.tasks` | pydantic `TaskCreate` (validation) | NFR-05 |
| `service.tasks` | `repository.task_repo` | dataclass + `Session` | FR-06 |
| `repository.task_repo` | `tasks` / `task_results` tables | SQLAlchemy Core/ORM | NFR-01, NFR-02 |
| `service.runner` | subprocess `stdout/stderr` | `task_results.stdout_tail` (NFR-04-redacted) | FR-02, NFR-04 |
| API key header | `api_keys.key_hash` | SHA-256 digest | FR-03, NFR-02 |
| `service.ratelimit` | `rate_buckets` row | row-level lock within single transaction | FR-05, R12 |

---

## 4. NFR Handling

Every NFR (SPEC §4) is enumerated and mapped to its handling
mechanism. The "Verification" column names the gate tool the harness
runs.

| ID | NFR | Dimension | Handling mechanism | Verification |
|---|---|---|---|---|
| NFR-01 | Performance + N+1 guard | `performance` | `selectinload`/`joinedload` in every list query; SQLAlchemy event-listener counts statements per request and fails the test on non-constant count; p95 measured by `pytest-benchmark` against 10k seeded rows | `pytest-benchmark`; SQL-count assertion in `tests/integration/test_nfr01_n_plus_one.py` |
| NFR-02 | HTTP/DB security | `security` | grep gate (`shell=True\|eval(\|exec(` 0 hits; SQL string-concat 0 hits); `bandit -r 03-development/src/` 0 HIGH/0 MEDIUM; CORS allowlist from `TASKQ_CORS_ORIGINS` (empty = reject all); API key hashed; `hmac.compare_digest`; 403 bodies do not leak existence | `bandit`; `tests/security/test_nfr02_grep.py`; CORS unit test |
| NFR-03 | Error handling / async correctness | `error_handling` | `session.transaction()` context manager enforces commit/rollback; `ast-error-handling` scanner refuses bare `except:` / `except Exception: pass`; explicit `except asyncio.CancelledError: raise` documented in `service/runner.py`; `/readyz` returns 503 with explicit `detail` if DB unreachable; subprocess killed + awaited on timeout | `tests/unit/test_nfr03_error_handling.py`; `tests/integration/test_nfr03_readyz_db_down.py`; `tests/integration/test_nfr08_no_orphan.py` |
| NFR-04 | Sensitive data redaction | `security` | `auth.redact()` regex `(sk-[A-Za-z0-9_-]{8,}\|token=\S+\|Bearer\s+\S+\|postgres(ql)?://[^\s]+)` applied to every stdout/stderr/log line; `config.__repr__` masks `TASKQ_DB_URL` password fragment; `/v1/metrics` body checked in test for absence of DSN | `tests/unit/test_nfr04_redaction.py`; `tests/integration/test_nfr04_metrics_no_dsn.py` |
| NFR-05 | Docstring coverage | `documentation` | `ast-docstrings` reports 100% coverage of public functions/classes; every docstring includes `[FR-XX]` or `[NFR-XX]`; OpenAPI `summary`+`description` asserted on every route via `app.openapi()` | `ast-docstrings`; `tests/integration/test_nfr05_openapi.py` |
| NFR-06 | Layering contract | `architecture_constraints` | `.importlinter` declares `api > service > repository > models` plus a `forbidden_imports` contract: `sqlalchemy` may only be imported from `repository/` | `lint-imports` exit 0; `tests/architecture/test_nfr06_no_sqlalchemy_above_repository.py` |
| NFR-07 | License compliance | `license_compliance` | `requirements.txt` pins `==`; `requirements.lock` (transitive) generated by `pip-compile`; allowlist: MIT/BSD-2-Clause/BSD-3-Clause/Apache-2.0/PSF; SBOM at `08-config/SBOM.json` | `pip-licenses --format=json --with-system`; SBOM diff check |
| NFR-08 | Mutation testing | `mutation_testing` | `mutmut` scoped to `service/` and `repository/`; score ≥ 70; scope recorded in `.methodology/harness_config.json` with justification | `mutmut results` |
| NFR-09 | Zero-skip testability | `test_assertion_quality` | `pytest -q` reports `0 skipped`; every test function contains ≥ 1 `assert`; FR-07 round-trip runs against a real SQLite file, not in-memory mock | `ast-assertions`; `pytest -q`; `tests/integration/test_fr07_migration_round_trip.py` |
| NFR-10 | Integration coverage | `integration_coverage` | `httpx.AsyncClient(transport=ASGITransport(app))`; every error code (401/403/404/409/422/429/503) covered by one integration test; ≥ 80% line coverage in `tests/integration/` | `pytest --cov=03-development/src --cov-report=term` on `tests/integration/` |
| NFR-11 | Readability | `readability` | MI (LLOC-weighted) ≥ 80; CC ≤ 10 per function; ≤ 400 LOC per file; ≤ 15 files per dir; ≤ 40 LOC per API handler | `readability-v2` |
| NFR-12 | `verify-system` target | `execute_verification_target` | `Makefile` target chains: `alembic upgrade head` → full pytest → uvicorn boot + `/healthz`+`/readyz` smoke → `alembic downgrade base` → `alembic upgrade head`; stdout MUST contain `verify-system: PASS`; non-zero exit fails the gate | `make verify-system` |

**NFR cross-walk summary**: 12 NFRs map to 11 distinct gate tools
(pytest-benchmark, bandit, ast-error-handling, ast-docstrings,
import-linter, pip-licenses, mutmut, ast-assertions,
pytest-cov-integration, readability-v2, system-verification).
NFR-02 and NFR-04 share no tool but NFR-04 is additionally covered by
the `ast-error-handling`-adjacent redaction regex assertion path
(NFR-04 metrics-DSN test counts as a distinct verification entry in
the same pytest run).

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int
> must match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`.
> Do NOT hand-write the YAML — pasted from the canonical template and
> EXAMPLE values replaced with this project's real values. SAB Generation
> phase will fill in module lists.

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-08-24"
  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: "2"'
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq_api.api.deps"
        - name: "taskq_api.api.tasks"
        - name: "taskq_api.api.health"
        - name: "taskq_api.errors"  # FR-10 RFC 7807 problem+json factory + correlation_id (T-09 owner_module)
      allowed_dependencies: ["service"]
    - name: service
      modules:
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.ratelimit"
        - name: "taskq_api.service.metrics"
      allowed_dependencies: ["repository"]
    - name: repository
      modules:
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.task_repo"
        - name: "taskq_api.repository.key_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models"]
    - name: models
      modules:
        - name: "taskq_api.models.orm"
        - name: "taskq_api.models.schemas"
      allowed_dependencies: []

  allowed_dependencies:
    - from: api
      to: service
    - from: service
      to: repository
    - from: repository
      to: models

  quality_targets:
    max_complexity: 10         # SPEC NFR-11: CC ≤ 10
    min_coverage: 100          # SPEC §11: TOTAL 100%
    max_coupling: 0.3          # CRG cohesion floor

  nfr_dimension_mapping: {}  # auto-derived from nfr_traceability below

  nfr_traceability:
    NFR-01:
      type: performance
      dimension: performance
      target: "p95 < 30ms"
      module: taskq_api.api.tasks
    NFR-02:
      type: security
      dimension: security
      target: "0 bandit HIGH/MEDIUM; 0 grep hits for shell=True|eval(|exec("
      module: taskq_api.service.runner
    NFR-03:
      type: reliability
      dimension: error_handling
      target: "0 bare except; CancelledError re-raised"
      module: taskq_api.service.runner
    NFR-04:
      type: security
      dimension: security
      target: "0 DSN fragments in logs/metrics; redaction regex applied"
      module: taskq_api.service.auth
    NFR-05:
      type: documentation
      dimension: documentation
      target: "100% public docstring coverage with [FR-XX] or [NFR-XX] reference"
      module: taskq_api.api.tasks
    NFR-06:
      type: layering
      dimension: architecture_constraints
      target: "lint-imports exit 0; 0 sqlalchemy imports above repository"
      module: taskq_api.repository.session
    NFR-07:
      type: licensing
      dimension: license_compliance
      target: "all licenses ∈ {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF}"
      module: taskq_api.config
    NFR-08:
      type: mutation
      dimension: mutation_testing
      target: "mutation score >= 70"
      module: taskq_api.service.tasks
      scope_layers: ["service", "repository"]
    NFR-09:
      type: testability
      dimension: test_assertion_quality
      target: "pytest -q reports 0 skipped; 0 zero-assertion tests"
      module: taskq_api.service.runner
    NFR-10:
      type: integration
      dimension: integration_coverage
      target: "integration coverage >= 80%"
      module: taskq_api.api.tasks
    NFR-11:
      type: maintainability
      dimension: readability
      target: "MI >= 80; CC <= 10; <= 400 LOC/file; <= 15 files/dir"
      module: taskq_api.api.tasks
    NFR-12:
      type: verifiability
      dimension: execute_verification_target
      target: "make verify-system exit 0 and prints 'verify-system: PASS'"
      module: taskq_api.app

  advisory_only: []  # AUTO-FILLED by parser — omit or leave []

  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}

  fr_module_traceability:
    FR-01: "taskq_api.api.tasks"               # CRUD routes
    FR-02:
      - "taskq_api.api.tasks"                  # /v1/tasks/{id}/run route
      - "taskq_api.service.runner"             # async executor
    FR-03: "taskq_api.api.deps"                # X-API-Key auth dependency
    FR-04: "taskq_api.api.deps"                # scope authorization
    FR-05: "taskq_api.api.deps"                # rate-limit dependency
    FR-06: "taskq_api.repository.session"      # transaction boundary
    FR-07: "migrations.versions.v3_split_results"  # data-migration owner
    FR-08:
      - "taskq_api.service.runner"             # async executor + drain
      - "taskq_api.api.tasks"                  # /v1/tasks/{id}/run route
    FR-09:
      - "taskq_api.api.health"                 # /healthz, /readyz, /v1/metrics routes
      - "taskq_api.service.metrics"            # metrics aggregation entry point
    FR-10: "taskq_api.errors"                  # RFC 7807 problem+json factory

  architecture_constraints:
    - "no_circular_dependencies"
    - "no_sqlalchemy_above_repository"
    - "models_sqlalchemy_orm_declarative_only"
    - "downward_only_layer_dependencies"

  high_risk_modules:
    - "taskq_api.service.runner"
    - "taskq_api.service.auth"
    - "taskq_api.repository.session"
    - "migrations.versions.v3_split_results"

  required_artifacts:
    - ".importlinter"
    - ".env.example"
    - "requirements.txt"
    - "requirements.lock"
    - "alembic.ini"
    - "Makefile"
    - ".methodology/harness_config.json"
    - "08-config/SBOM.json"
```
<!-- SAB:END -->

Note: SAB Generation phase (Phase 2.5) will expand each `layers[].modules`
list into file-level entries (`implemented_in`) and may add new
advisory-only NFRs discovered during architecture review.

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and the `security_design:` root key are
> parsed by `core/quality_gate/security_design.py:extract_security_block()`.
> Pasted from canonical template; EXAMPLE values replaced with this
> project's real values. `applicability: full` because `taskq-api` is a
> network-facing HTTP service that executes subprocesses and stores
> API credentials.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""  # not required when applicability: full
  trust_boundaries:
    - id: TB-01
      name: "unauthenticated client → API edge"
      description: "Internet-reachable HTTP clients entering the FastAPI app; X-API-Key may be absent, forged, or stale."
    - id: TB-02
      name: "API → Service (authenticated scope)"
      description: "After auth/authz, the API layer dispatches to service functions with a verified ApiKeyRecord; trust now rests on scope correctness."
    - id: TB-03
      name: "Service → Repository (DB)"
      description: "Service code crosses into the only layer allowed to import sqlalchemy; all ORM access must use repository functions to keep session lifecycle correct."
    - id: TB-04
      name: "Service → OS subprocess (task execution)"
      description: "Service code spawns child processes with create_subprocess_exec(shell=False); the OS process boundary is a trust crossing for argv contents, working directory, and env vars."
  threats:
    - id: T-01
      boundary: TB-01
      category: spoofing
      description: "Attacker presents a forged X-API-Key header to impersonate a legitimate client."
      mitigation: "SHA-256 hash at rest, hmac.compare_digest for verification, revoked_at checked on every request."
      owner_module: "taskq_api.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t01_forged_api_key_rejected"
    - id: T-02
      boundary: TB-01
      category: tampering
      description: "Malformed task body (oversize name, control chars, SQL fragments) corrupts persisted state or is logged unsafely."
      mitigation: "pydantic TaskCreate schema validation; 422 problem+json on any rule violation; injected character blacklist (FR-01)."
      owner_module: "taskq_api.api.tasks"
      nfr: NFR-02
      verified_by: "test_sec_t02_malformed_body_rejected"
    - id: T-03
      boundary: TB-02
      category: elevation_of_privilege
      description: "Token with 'read' scope successfully invokes 'admin'-only DELETE /v1/tasks/{id}."
      mitigation: "Single FastAPI dependency require_scope() enforced before any handler body runs; lint test asserts every /v1 route uses it."
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t03_read_scope_cannot_delete"
    - id: T-04
      boundary: TB-02
      category: information_disclosure
      description: "403 response body reveals whether a task id exists, enabling enumeration of internal state."
      mitigation: "Scope check runs before any resource lookup; 403 body never contains id-specific fields."
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t04_403_does_not_leak_existence"
    - id: T-05
      boundary: TB-03
      category: tampering
      description: "SQL injection via string concatenation reaches the database."
      mitigation: "import-linter forbids sqlalchemy above repository; grep CI gate fails on f-string/% / + SQL composition; ORM/parameterized queries only."
      owner_module: "taskq_api.repository.task_repo"
      nfr: NFR-02
      verified_by: "test_sec_t05_no_sql_string_concat"
    - id: T-06
      boundary: TB-04
      category: elevation_of_privilege
      description: "Attacker injects shell metacharacters via the task command field, escaping the intended command."
      mitigation: "create_subprocess_exec with shell=False; shlex.split on validated input; FR-02 blacklist; bandit gate."
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t06_shell_metachars_neutralized"
    - id: T-07
      boundary: TB-04
      category: denial_of_service
      description: "Long-running task is killed by SIGKILL but the child leaves descendants running (orphan process)."
      mitigation: "runner.run_task uses proc.kill() followed by await proc.wait() before returning; integration test asserts no descendant pid remains after timeout."
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t07_no_orphan_after_timeout"
    - id: T-08
      boundary: TB-04
      category: information_disclosure
      description: "Subprocess stdout/stderr contains an API key, bearer token, or DSN password that is then returned via /v1/tasks/{id}/runs or logged."
      mitigation: "service.auth.redact() regex applied before write to task_results and before any log emission; metrics endpoint tested for DSN absence."
      owner_module: "taskq_api.service.auth"
      nfr: NFR-04
      verified_by: "test_sec_t08_secrets_redacted_in_results"
    - id: T-09
      boundary: TB-01
      category: repudiation
      description: "Operator denies issuing a destructive action because no correlation_id links the request to server logs."
      mitigation: "Every problem+json response carries correlation_id mirrored as X-Correlation-Id header and in the structured log line."
      owner_module: "taskq_api.errors"
      nfr: NFR-02
      verified_by: "test_sec_t09_correlation_id_present"
    - id: T-10
      boundary: TB-03
      category: denial_of_service
      description: "Service holds a row-level lock on rate_buckets longer than the request budget, starving other workers."
      mitigation: "Single short transaction per consume() call; pool_pre_ping on; TASKQ_MAX_CONCURRENT caps the request fan-in."
      owner_module: "taskq_api.repository.rate_repo"
      nfr: NFR-01
      verified_by: "test_sec_t10_rate_consume_under_contention"
```
<!-- SEC:END -->

**STRIDE-lite coverage check**: 6 STRIDE categories appear across
10 threats — Spoofing (T-01), Tampering (T-02, T-05), Repudiation (T-09),
Information Disclosure (T-04, T-08), Denial of Service (T-07, T-10),
Elevation of Privilege (T-03, T-06). All four trust boundaries (TB-01 …
TB-04) have at least one associated threat. `owner_module` for every
threat names a module declared in §5's SAB block; `nfr` (when present)
names an NFR in SPEC §4. `verified_by` names a single test function
that Phase 5 must author.
