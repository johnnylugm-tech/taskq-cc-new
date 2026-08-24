# Architecture Decision Records (ADR) — taskq-api

> Decision records for `taskq-api` v1.0.0, extracted from the SAD. Each
> ADR records the context, decision, rationale, consequences, and
> alternatives considered. The Phase 2 orchestrator reloads this file by
> matching the H1 prefix `# Architecture Decision Records`.

---

## Specification, SRS, and Traceability Matrix

This ADR index is one of the Phase 2 architecture artifacts for
`taskq-api` v1.0.0. The canonical specification for every functional
and non-functional requirement is `01-requirements/SRS.md`, transcribed
verbatim from `SPEC.md` v1.0.0 (2026-07-30); the bidirectional FR↔Code↔Test
traceability matrix is maintained at
`01-requirements/TRACEABILITY_MATRIX.md`.

The traceability matrix below is the ADR-side projection of that full
matrix: it anchors each architecture decision (ADR-NNN) to the FR and
NFR identifiers in the SRS that the decision realises, so a reader can
walk from any decision back to the specification clause that motivated
it without leaving this file. Where an ADR has no direct FR anchor
(e.g. ADR-015 on the `verify-system` Makefile target, which is a
methodology exit gate rather than a feature), the matrix records `—` in
the FR column and the single owning NFR instead.

| ADR   | Decision title                                                  | Satisfies (FR) | Satisfies (NFR)        |
|-------|------------------------------------------------------------------|----------------|------------------------|
| ADR-001 | Python 3.11 runtime                                            | —            | NFR-09                 |
| ADR-002 | FastAPI + Uvicorn ASGI                                         | FR-01..FR-10 | NFR-05                 |
| ADR-003 | SQLAlchemy 2.x + Alembic ORM and migrations                    | FR-07        | —                      |
| ADR-004 | Layered architecture (`api > service > repository > models`)   | FR-06        | NFR-06                 |
| ADR-005 | `asyncio.create_subprocess_exec`, `shell=False`                 | FR-02        | NFR-02                 |
| ADR-006 | `asyncio.TaskGroup` background runner                           | FR-08        | NFR-03                 |
| ADR-007 | RFC 7807 `application/problem+json` error contract              | FR-10        | NFR-02                 |
| ADR-008 | `X-API-Key` header auth, SHA-256 at rest, `hmac.compare_digest` | FR-03        | NFR-02                 |
| ADR-009 | Per-token scope hierarchy `read < write < admin`               | FR-04        | NFR-02                 |
| ADR-010 | Per-token token-bucket rate limiting (row-level lock)           | FR-05        | NFR-01                 |
| ADR-011 | Alembic 3-revision migration pipeline (reversible data move)    | FR-07        | NFR-12                 |
| ADR-012 | `session.transaction()` as single repository boundary          | FR-06        | —                      |
| ADR-013 | `selectinload` / `joinedload` mandatory (N+1 guard)             | FR-06        | NFR-01                 |
| ADR-014 | `service.auth.redact()` regex on every sensitive output path   | FR-10        | NFR-02, NFR-04         |
| ADR-015 | `verify-system` Makefile target as Phase 2–4 exit gate         | —            | NFR-12                 |
| ADR-016 | Directory structure mirrors the SPEC §6 tree                   | —            | NFR-06                 |
| ADR-017 | Pydantic v2 schemas for all request/response bodies            | FR-01        | NFR-05                 |
| ADR-018 | Dependency license allowlist (NFR-07 compliance)               | —            | NFR-07                 |
| ADR-019 | Mutation testing scoped to `service/` + `repository/`          | —            | NFR-08                 |
| ADR-020 | `readability-v2` budgets (NFR-11 compliance)                    | —            | NFR-11                 |
| ADR-021 | `__main__` CLI (`migrate` / `key create` / `healthcheck`)       | FR-09        | —                      |
| ADR-022 | `httpx.AsyncClient` integration tests (NFR-10 compliance)      | —            | NFR-10                 |
| ADR-023 | `metrics` lives in `service/`, not `repository/`                | FR-09        | NFR-02                 |
| ADR-024 | `asyncio` + `concurrent.futures` stdlib only                   | FR-08        | NFR-03                 |

This ADR-side traceability matrix is a projection of the full matrix in
`01-requirements/TRACEABILITY_MATRIX.md`; that file owns the FR↔Code and
FR↔Test mappings and is the canonical bidirectional reference per
NFR-09. Each ADR's own **Context** subsection cites the FR / NFR IDs
inline, and the **Rationale** subsection restates which specification
clause the decision satisfies.

---

## ADR-001: Python 3.11 as the implementation language and runtime

### Status
Accepted

### Context
`taskq-api` is a network-facing HTTP service that exposes REST endpoints
for task submission, execution, and querying (SPEC FR-01 … FR-10).
The runtime must support an async HTTP stack, a modern type system,
structured concurrency primitives, and an ecosystem of mature libraries
for SQLAlchemy 2.x ORM, Alembic migrations, and FastAPI routing.

### Decision
Adopt **Python 3.11** (project venv reports `Python 3.11.15`) as the
single implementation language. All source code targets the 3.11 syntax
and stdlib surface. The venv at `.venv/` is the canonical interpreter
referenced by the `Makefile` `verify-system` target.

### Rationale
- Python 3.11 ships `asyncio.TaskGroup` (PEP 654), which the SAD §1.3
  mandates for the background runner with structured cancellation.
- 3.11 `tomllib` removes the need for a third-party TOML parser.
- SQLAlchemy 2.x requires 3.7+; FastAPI 0.100+ recommends 3.10+; 3.11
  is the lowest version where the full stack (FastAPI + SQLAlchemy 2.x
  + Pydantic v2 + Alembic) is verified together.
- Staying on one language minimises hiring surface and ops tooling cost
  for a single-process service.

### Consequences
- Positive: full async/await stack; structured concurrency via
  `TaskGroup`; typing features (`Self`, `TypeVarTuple`) usable.
- Negative: GIL caps CPU-bound throughput; mitigated by `uvicorn`
  worker count and the fact that all heavy work is subprocess I/O.
- Negative: 3.11 EOL is October 2027 — an upgrade plan must exist by
  mid-2027.

### Alternatives considered
- **Go (net/http + sqlx)**: rejected — no SQLAlchemy 2.x equivalent;
  ecosystem for Alembic-equivalent migrations is weaker; the team has
  no Go experience.
- **Node.js + TypeScript**: rejected — Alembic has no equivalent;
  pydantic-style validation requires a third-party library.
- **Python 3.12**: rejected as the floor — only marginal benefit
  (per-interpreter GIL) and 3.11 already covers every stdlib feature
  the SAD needs.

---

## ADR-002: FastAPI + Uvicorn ASGI as the HTTP framework

### Status
Accepted

### Context
`taskq-api` must expose a REST API with `/v1/tasks*`, `/healthz`,
`/readyz`, and `/v1/metrics`. The framework must integrate cleanly with
pydantic v2 for body validation and RFC 7807 error responses, and must
support FastAPI's `Depends()` mechanism for the single auth+scope+rate
dependency mandated by FR-04.

### Decision
Use **FastAPI** on top of **Uvicorn** (ASGI). Routers live under
`taskq_api/api/` and are wired in `taskq_api.app:app`. The uvicorn
target is `taskq_api.app:app`.

### Rationale
- FastAPI's `Depends(require_scope(...))` is the natural realisation of
  the SAD §1.3 "single FastAPI dependency" constraint.
- Pydantic v2 integration gives us FR-01 body validation with no
  extra glue.
- Uvicorn supports HTTP/1.1 + WebSockets; the harness-methodology
  verification target relies on `uvicorn taskq_api.app:app` boot.
- OpenAPI schema is generated automatically and is asserted by
  NFR-05 / `test_nfr05_openapi.py`.

### Consequences
- Positive: declarative routing, dependency injection, automatic
  OpenAPI generation.
- Negative: FastAPI ties the project to pydantic v2; any future
  migration to msgspec or attrs would touch every handler.
- Negative: middleware ordering must be set up in `app.py` and is
  not exposed via decorators.

### Alternatives considered
- **Starlette (bare)**: rejected — would require re-implementing the
  `Depends()` mechanism and the OpenAPI generation.
- **Flask**: rejected — lacks first-class async; would force a sync
  worker model incompatible with `asyncio.create_subprocess_exec`.
- **Litestar**: rejected — smaller ecosystem; team has zero experience.

---

## ADR-003: SQLAlchemy 2.x ORM with Alembic schema migrations

### Status
Accepted

### Context
The data model spans six tables (`tasks`, `api_keys`, `tags`,
`task_tags`, `task_results`, `rate_buckets` per SPEC §5.2). Schema
evolves via FR-07 in three Alembic revisions (`v1_initial`,
`v2_tags`, `v3_split_results`). The data model must be reversible
(round-trippable via `alembic downgrade base` → `upgrade head`,
NFR-12 #4).

### Decision
Use **SQLAlchemy 2.x** declarative ORM (`DeclarativeBase`,
`Mapped`, `mapped_column`) for the model layer (`taskq_api/models/orm.py`)
and **Alembic** for schema migrations (`migrations/versions/`).

### Rationale
- SQLAlchemy 2.x's typed `Mapped[...]` columns give compile-time
  schema awareness.
- Alembic's `upgrade` / `downgrade` semantics give us FR-07's
  reversible-migration contract for free.
- The 2.x `select()` / `Session.execute()` API is what the repository
  layer standardises on (SAD §2.2.3).

### Consequences
- Positive: portable across SQLite (tests) and PostgreSQL (production);
  Alembic provides repeatable, reversible migrations.
- Negative: a C-14 carve-out lets `models/orm.py` import
  `sqlalchemy.orm` for `DeclarativeBase`/`Mapped`/`mapped_column`,
  but forbids `select`/`insert`/`update`/`delete`/`engine` imports
  in `models/`. This must remain explicit in the import-linter contract.
- Negative: Alembic `autogenerate` cannot detect all schema changes
  (e.g. enum value renames); manual revisions are still required for
  v3 data migration.

### Alternatives considered
- **Tortoise ORM + aerich**: rejected — no equivalent of
  `selectinload` maturity; weaker Alembic-equivalent story.
- **Raw `sqlite3` / `psycopg`**: rejected — would lose Alembic
  migration tooling and would require hand-rolled SQL for every
  N+1 guard.
- **Django ORM**: rejected — couples the project to Django's request
  stack, conflicting with FastAPI.

---

## ADR-004: Layered architecture with strict downward dependencies

### Status
Accepted

### Context
SAD §1.2 defines five source communities (`taskq_api/`,
`taskq_api/api/`, `taskq_api/service/`, `taskq_api/repository/`,
`taskq_api/models/`) plus a `migrations/` community. The dependency
graph must be acyclic and downward-only: `api → service → repository →
models`, with `config` and `errors` as L0 independence modules
importable from any layer but importing nothing from layers.

### Decision
Enforce the layering with **`import-linter`** (NFR-06). The contract
declares:
- `api > service > repository > models` (downward chain).
- `forbidden_imports`: `sqlalchemy` may only be imported from
  `repository/` (with the C-14 carve-out allowing `sqlalchemy.orm`
  in `models/orm.py` for declarative base only).
- `migrations/` references `models/` only.

### Rationale
- Downward-only dependencies make the repository layer the single
  source of truth for SQL access, satisfying FR-06 and NFR-06.
- import-linter is a CI gate (exit 0 on success) — the contract is
  machine-enforced, not a code-review convention.
- Hub functions (`session.transaction()`, `service.auth.resolve_api_key`,
  `errors.problem_response`) keep the CRG cohesion score above the
  0.4286 floor required for community survival.

### Consequences
- Positive: impossible to import `sqlalchemy.select` from `service/`
  or `api/` by accident.
- Negative: every new cross-layer helper must either be placed in a
  lower layer or be passed as a parameter — minor friction.
- Negative: `metrics` (a service module) reading aggregate counts
  must go through `repository/*_repo`; the temptation to write
  raw SQL in the service layer must be policed by import-linter.

### Alternatives considered
- **Hexagonal / ports-and-adapters**: rejected — over-architected
  for a single-process service with one DB backend and one HTTP edge.
- **No enforcement, rely on code review**: rejected — NFR-06 demands
  a machine gate.
- **dependency-cruiser as the linter**: rejected — equivalent
  capability, but import-linter is already adopted and is the harness
  standard.

---

## ADR-005: `asyncio.create_subprocess_exec` with `shell=False` for task execution

### Status
Accepted

### Context
FR-02 requires that submitted tasks be executed as OS subprocesses.
FR-08 requires that timeouts kill the child and leave no orphans (R8).
NFR-02 forbids `shell=True`, `eval(`, and `exec(` anywhere in the
codebase (bandit + grep gate).

### Decision
`taskq_api/service/runner.py` uses `asyncio.create_subprocess_exec`
with `shell=False`, passing argv as a list. On timeout, the runner
calls `proc.kill()` and `await proc.wait()` to guarantee the child
is reaped before the coroutine returns.

### Rationale
- `shell=False` neutralises shell-metacharacter injection (T-06).
- The list-form argv prevents the OS from interpreting any single
  argument as a shell directive.
- `proc.kill()` + `await proc.wait()` is the documented asyncio
  pattern for guaranteeing no orphans; the integration test
  `test_sec_t07_no_orphan_after_timeout` asserts no descendant
  pid remains.

### Consequences
- Positive: shell injection is impossible at the OS layer.
- Positive: timeout kills are deterministic; no zombie subprocesses.
- Negative: argv splitting is the caller's responsibility; if the
  client sends a single string with spaces, pydantic schema
  validation must reject it (FR-01 input rules).
- Negative: no shell globbing / pipes — clients that want pipelines
  must submit them as multi-step tasks.

### Alternatives considered
- **`subprocess.run` with `shell=True`**: rejected — direct NFR-02
  violation; `bandit` would fail the build.
- **`os.system`**: rejected — synchronous, blocks the event loop,
  no `shell=False` opt-out.
- **`pexpect` / `pty`**: rejected — adds an interactive-shell attack
  surface with no benefit for non-interactive tasks.

---

## ADR-006: `asyncio.TaskGroup` for the background runner

### Status
Accepted

### Context
FR-08 requires a single-process async executor with graceful drain
on shutdown. The runner must accept new tasks during normal operation
and complete in-flight tasks on SIGTERM. `asyncio.CancelledError`
must propagate, not be swallowed by `except Exception` (NFR-03 R7).

### Decision
Use **`asyncio.TaskGroup`** (PEP 654, Python 3.11+) as the
structured-concurrency primitive in `service/runner.py`. Tasks are
spawned as children of the runner's group; cancellation re-raises
`CancelledError` so the lifespan handler can drain cleanly.

### Rationale
- `TaskGroup` gives automatic propagation of cancellation and
  exception aggregation, eliminating the manual "track each task
  and cancel on shutdown" boilerplate.
- PEP 654 guarantees that an exception in one child cancels the
  remaining siblings — the right semantics for a runner pool.
- 3.11 ships `TaskGroup` natively, so no third-party dependency
  (e.g. `trio` or `anyio` task groups) is needed.

### Consequences
- Positive: deterministic shutdown semantics; one `async with
  TaskGroup()` block replaces manual task bookkeeping.
- Positive: `CancelledError` propagates by design — NFR-03 R7 is
  satisfied without any special handling.
- Negative: any `except Exception:` in a runner callback that
  swallows `CancelledError` would defeat the design; the
  `ast-error-handling` scanner enforces this.
- Negative: `TaskGroup` cannot host a forever-loop child; the runner
  uses a queue + worker tasks pattern instead of a bare `while True`.

### Alternatives considered
- **`asyncio.gather`**: rejected — no cancellation-on-first-failure
  semantics; manual cleanup required.
- **`trio` nursery**: rejected — would force the entire async stack
  onto trio; FastAPI is asyncio-native.
- **Manual task list + cancel loop**: rejected — re-implements
  `TaskGroup` poorly and is error-prone under cancellation.

---

## ADR-007: RFC 7807 `application/problem+json` as the unified error contract

### Status
Accepted

### Context
FR-10 requires every non-2xx response to be `application/problem+json`
with a whitelisted `detail` field. NFR-02 forbids error bodies that
leak resource existence (T-04).

### Decision
A single factory `errors.problem_response(status, type_uri, detail,
correlation_id)` produces every error body. A `ProblemException`
hierarchy lets the service layer raise typed errors that the API
layer converts to problem responses without leaking the exception
type to the wire. Every problem body carries a `correlation_id`
mirrored as `X-Correlation-Id` header (T-09).

### Rationale
- RFC 7807 is the de-facto standard for HTTP API errors; clients
  can parse the shape uniformly.
- A single factory prevents drift: if any handler returns a plain
  `{"error": "..."}`, the AST-driven tests under NFR-10 will fail.
- The `correlation_id` + header mirror satisfies T-09 (repudiation).

### Consequences
- Positive: one error shape across 401/403/404/409/422/429/503;
  client SDKs need only one error parser.
- Positive: correlation_id propagates from request log to response
  body, simplifying incident triage.
- Negative: legacy clients expecting `{"detail": "..."}` FastAPI
  default must update; the OpenAPI doc advertises the new shape.

### Alternatives considered
- **FastAPI's default `HTTPException` JSON**: rejected — non-standard
  shape; cannot carry `type` URI.
- **Plain `{"message": "..."}`**: rejected — FR-10 mandates RFC 7807.

---

## ADR-008: `X-API-Key` header auth with SHA-256 at rest and `hmac.compare_digest`

### Status
Accepted

### Context
FR-03 requires every `/v1/*` route to require an API key. NFR-02
requires that keys never be stored in plaintext, that comparisons
be constant-time, and that revoked keys be rejected on every
request.

### Decision
- Client supplies `X-API-Key: <key>`.
- Server stores `sha256(key)` in `api_keys.key_hash`.
- Verification uses `hmac.compare_digest(sha256(supplied_key), key_hash)`.
- `revoked_at IS NULL` is checked on every request.
- Key creation CLI: `python -m taskq_api key create --scope ...`
  generates a fresh key, returns it once, persists only the hash.

### Rationale
- `hmac.compare_digest` is constant-time and is stdlib — no third
  party needed.
- SHA-256 is collision-resistant enough for an API-key secret
  (the threat model is theft, not collision).
- The CLI returns the plaintext key exactly once; there is no
  recovery path — the operator must regenerate.

### Consequences
- Positive: timing-attack resistant; no plaintext storage.
- Negative: if an operator loses the key, they must `revoke` and
  re-`create` — by design, no re-issuance of existing secrets.
- Negative: rotation requires a brief overlap window where two
  keys are simultaneously valid.

### Alternatives considered
- **Bearer JWT**: rejected — adds a token-format dependency and
  signing-key rotation story; the service does not need third-party
  identity federation.
- **HTTP Basic Auth**: rejected — username/password semantics do
  not map to per-token scopes.
- **mTLS**: rejected — requires PKI in front of the service, which
  is out of scope for v1.0.0.

---

## ADR-009: Per-token scope hierarchy `read < write < admin` enforced by one FastAPI dependency

### Status
Accepted

### Context
FR-04 requires every `/v1/*` route to be guarded by a scope check.
NFR-02 demands that the scope check happen *before* any resource
lookup, so 403 bodies cannot leak whether an id exists (T-04).

### Decision
- Single dependency `require_scope("read" | "write" | "admin")` in
  `taskq_api/api/deps.py`.
- Scope hierarchy: `read < write < admin`. A token with `admin`
  satisfies `write` and `read`.
- The dependency performs auth + scope + rate-limit in that order;
  the handler receives an `ApiKeyRecord` as the `actor` parameter.

### Rationale
- "One place" satisfies FR-04 verbatim and prevents drift between
  handlers.
- Scope check before resource lookup closes the existence-leak (T-04).
- Hierarchical scopes let a single token substitute for three
  separate ones, simplifying token issuance.

### Consequences
- Positive: lint test asserts every `/v1/*` route is decorated
  with `Depends(require_scope(...))` — drift is impossible.
- Negative: a route that needs fine-grained "either read OR admin"
  must be split into two routes; we have no such case in v1.0.0.

### Alternatives considered
- **Per-handler scope strings**: rejected — duplicates the check
  and risks drift.
- **RBAC with explicit role→permission tables**: rejected —
  over-architected for three scopes.

---

## ADR-010: Per-token token-bucket rate limiting with row-level locks in a single transaction

### Status
Accepted

### Context
FR-05 requires per-key rate limiting. NFR-01 demands bounded DB
contention so rate-limiting cannot starve other workers (T-10).

### Decision
- `rate_buckets` table stores `(key_id, tokens, last_refill)`.
- `service.ratelimit.consume(key_id)` runs `SELECT ... FOR UPDATE`
  then `UPDATE` then `COMMIT` in one short transaction.
- A 429 response carries a `Retry-After` header computed from the
  remaining time-to-refill.

### Rationale
- Row-level lock keeps the bucket atomic across concurrent workers.
- A single short transaction caps lock duration; the integration
  test `test_sec_t10_rate_consume_under_contention` asserts p99
  under load.
- Token bucket gives smooth refill semantics — better than a fixed
  window which would allow burst-at-boundary behaviour.

### Consequences
- Positive: deterministic under contention; no thundering herd.
- Negative: every API request takes one row lock on `rate_buckets`.
  Mitigated by SQLite's WAL mode and PostgreSQL's row-level locks.

### Alternatives considered
- **In-memory `collections.deque`**: rejected — state is lost on
  restart and is not shared across processes.
- **Sliding-window log table**: rejected — unbounded row growth.
- **Fixed window**: rejected — vulnerable to burst-at-boundary.

---

## ADR-011: Alembic 3-revision migration pipeline with reversible data migration

### Status
Accepted

### Context
FR-07 requires a reversible schema migration. SPEC §5.2 specifies
that v3 (`v3_split_results`) splits `tasks.result_json` into a
`task_results` table, including a **data** migration (not just a
DDL change). NFR-12 #4 requires the round-trip
`alembic downgrade base` → `upgrade head` to succeed against a real
SQLite file.

### Decision
Three revisions:
1. `v1_initial.py` — base tables (`tasks`, `api_keys`, `tags`,
   `task_tags`, `rate_buckets`).
2. `v2_tags.py` — tag enrichment (any seed data + indexes).
3. `v3_split_results.py` — adds `task_results`; row-by-row copy from
   `tasks.result_json`; drops `tasks.result_json` on upgrade. On
   downgrade, row-by-row copy back into a re-added `result_json`
   column before dropping `task_results`.

Every revision has a working `downgrade()`. No `op.execute("DROP
TABLE ...")` shortcuts.

### Rationale
- Linear `v1 → v2 → v3` pipeline is the CRG-approved exception for
  a chain-style community.
- Per-row data copy keeps the migration safe under partial failure
  (transaction wraps the loop).
- The round-trip test (`test_fr07_migration_round_trip.py`) uses
  a real SQLite file (not `:memory:`) and asserts row-by-row
  equality of a seeded sample (SPEC §8 #12).

### Consequences
- Positive: schema changes are auditable and reversible.
- Negative: data migrations on large tables are slow; v1.0.0's
  expected scale keeps this within budget.
- Negative: every revision must be tested for both directions,
  doubling the migration test surface.

### Alternatives considered
- **`CREATE TABLE IF NOT EXISTS` + raw DDL in app code**: rejected —
  no downgrade path, no audit trail.
- **Single mega-revision**: rejected — cannot be partially reverted
  if v3's data copy fails halfway.

---

## ADR-012: `session.transaction()` context manager as the single repository transaction boundary

### Status
Accepted

### Context
FR-06 requires a single transaction boundary that guarantees
commit-or-rollback for every repository call. The repository layer
must own `Session` lifecycle; the service layer must never
import `sqlalchemy`.

### Decision
`taskq_api/repository/session.py` exposes a `@contextmanager`
`transaction()` function that yields a `Session`, commits on clean
exit, and rolls back on exception. Every `*_repo` function accepts
a `Session` parameter and never creates one.

### Rationale
- A single context manager removes the temptation to scatter
  `session.commit()` calls and risk partial writes.
- Accepting `Session` as a parameter (not constructing it) lets
  the service layer compose multi-repo operations in one
  transaction without importing `sqlalchemy`.
- NFR-03 tests assert that any exception inside the `with` block
  triggers rollback and that `Session.close()` is always called.

### Consequences
- Positive: one place to look for transaction semantics.
- Positive: integration tests can pass a pre-configured `Session`
  to a repo function without going through the context manager.
- Negative: a service function that needs two operations in one
  transaction must `with session.transaction() as s: ...` and
  pass `s` to both repo calls — verbose but explicit.

### Alternatives considered
- **Unit-of-work pattern (per-request session)**: rejected —
  couples session lifetime to FastAPI request lifecycle, breaking
  background tasks in `service/runner.py`.
- **`session.begin()` nested calls**: rejected — fails noisily when
  a caller forgets the outermost `with`.

---

## ADR-013: `selectinload` / `joinedload` mandatory on every multi-relation read (N+1 guard)

### Status
Accepted

### Context
NFR-01 requires that no endpoint triggers the N+1 query problem. A
`GET /v1/tasks` that lazy-loads each row's tags would issue N+1 SQL
statements on a 10k-row table.

### Decision
Every repository list / get function that traverses a relationship
uses `selectinload(...)` (for one-to-many) or `joinedload(...)`
(for many-to-one). The integration test
`test_nfr01_n_plus_one.py` registers a SQLAlchemy event listener
that counts `before_cursor_execute` events per request and fails on
non-constant count. `pytest-benchmark` asserts p95 < 30 ms on a
10k-row seed.

### Rationale
- Eager loading is the canonical SQLAlchemy mitigation.
- The event-listener counter makes regressions fail loudly, not
  silently.

### Consequences
- Positive: N+1 is caught at test time, not at production scale.
- Negative: every new list query must explicitly list its eager
  loads — a checklist item in code review.

### Alternatives considered
- **Always use `lazy='selectin'` at the relationship level**: rejected
  — hides intent and may over-eager-load on single-row reads.
- **Async-only lazy load**: rejected — SQLAlchemy 2.x async still
  issues one query per relationship access.

---

## ADR-014: `service.auth.redact()` regex applied to all sensitive output paths

### Status
Accepted

### Context
NFR-04 requires that API keys (`sk-...`), bearer tokens (`Bearer
...`), URL-embedded secrets (`token=...`), and DSNs
(`postgres://...`) never appear in logs, in `task_results.stdout`,
or in `/v1/metrics`. T-08 covers subprocess output leakage; T-09
covers correlation-id propagation.

### Decision
A single regex `r"(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://\S+)"`
defined in `service/auth.py` is applied via `redact(s: str) -> str`
to:
- every line written to `task_results.stdout_tail` /
  `task_results.stderr_tail`,
- every structured-log message in `service/`,
- the repr of `config.Settings` (so DSN passwords never appear in
  `print(settings)`).

### Rationale
- Centralising the regex prevents drift between handlers.
- Tests assert both presence (a known DSN appears in subprocess
  output) and absence (after redaction, the body has no DSN).

### Consequences
- Positive: one place to extend the redaction list as new secret
  formats appear.
- Negative: aggressive redaction may hide legitimate tokens in
  test fixtures; tests must use non-matching strings (`sk-fake`
  is too short, must be ≥ 8 chars).

### Alternatives considered
- **Logging filter that scrubs `record.msg`**: rejected — does not
  cover `task_results` writes.
- **Vault-based dynamic secret injection**: rejected — out of
  scope for v1.0.0.

---

## ADR-015: `verify-system` Makefile target as the binding Phase 2–4 exit gate

### Status
Accepted

### Context
NFR-12 mandates a single executable verification target that the
harness invokes at exit gates 2, 3, and 4. The target must be able
to fail — i.e. it must exercise real dependencies, not mocks. The
target name is fixed: `make verify-system`.

### Decision
`Makefile` target `verify-system` chains, in order:
1. `alembic upgrade head` (against a real SQLite file).
2. Full pytest (NFR-09: zero skipped).
3. `uvicorn taskq_api.app:app` boot + smoke `GET /healthz`,
   `GET /readyz` (FR-09).
4. `alembic downgrade base`.
5. `alembic upgrade head` (round-trip, NFR-12 #4).

Stdout must contain `verify-system: PASS`. Non-zero exit = gate
fail.

### Rationale
- A single target name (`verify-system`) is a stable contract
  between the project and the harness; the harness never has to
  guess which target to run.
- Real-SQLite (not `:memory:`) catches path-handling bugs the in-
  memory mock would hide.
- The downgrade→upgrade round-trip proves FR-07 reversibility
  end-to-end, not just per-revision unit tests.

### Consequences
- Positive: one command exercises migrations, tests, runtime, and
  migration reversibility in sequence.
- Negative: a flake in any step fails the gate; CI must keep all
  five steps stable.
- Negative: target name is fixed — renaming the Makefile target
  silently breaks the harness.

### Alternatives considered
- **Multiple targets (`verify-migrations`, `verify-tests`,
  `verify-runtime`)**: rejected — the harness mandates one name.
- **`tox` instead of Makefile**: rejected — adds a tool dependency
  for a one-liner orchestration.

---

## ADR-016: Directory structure mirrors the SPEC §6 tree (5 source communities + migrations)

### Status
Accepted

### Context
CRG (the code-review graph) treats each `taskq_api/` subdirectory as
one community. The SPEC §6 directory tree is the binding contract.
The SAD §2.1 community analysis must produce exactly 5 source
communities + `migrations/`, each ≤ 50 nodes, with internal-edge
density above the 0.4286 cohesion floor.

### Decision
Adopt the SPEC §6 tree verbatim:
- `taskq_api/` (root): `__init__.py`, `__main__.py`, `app.py`,
  `config.py`, `errors.py`.
- `taskq_api/api/`: `__init__.py`, `deps.py`, `tasks.py`, `health.py`.
- `taskq_api/service/`: `__init__.py`, `tasks.py`, `runner.py`,
  `auth.py`, `ratelimit.py`, `metrics.py`.
- `taskq_api/repository/`: `__init__.py`, `session.py`,
  `task_repo.py`, `key_repo.py`, `rate_repo.py`.
- `taskq_api/models/`: `__init__.py`, `orm.py`, `schemas.py`.
- `migrations/`: `env.py`, `versions/v1_initial.py`,
  `versions/v2_tags.py`, `versions/v3_split_results.py`.

`config` and `errors` sit at the root community so the unavoidable
external imports (`fastapi`, `uvicorn`, `argparse`) are offset by
hub calls (`config.get_settings()`, `errors.problem_response()`)
from every function body.

### Rationale
- Matches SPEC §6 contractually.
- Each community keeps ≤ 50 nodes and clear hub functions, so
  CRG cohesion scoring stays healthy.
- `service/` having 5 siblings is the maximum the edge-budget math
  can sustain; a 6th sibling would dilute hub density.

### Consequences
- Positive: CRG scoring is deterministic; community counts match
  SAD §2.1's "5 expected source communities" claim.
- Negative: any new module that wants a new top-level package
  must justify expanding beyond 5 communities (we do not have
  such a need in v1.0.0).

### Alternatives considered
- **Flat layout (no sub-packages)**: rejected — would produce one
  monolithic community with > 100 nodes, failing the 50-node
  ceiling.
- **Deeper nesting (per-feature packages)**: rejected — over-
  fragmented; would produce > 8 communities, exceeding the
  CRG-recommended 3–6 range.

---

## ADR-017: Pydantic v2 schemas for all request/response bodies

### Status
Accepted

### Context
FR-01 requires validated task creation. NFR-05 requires 100%
docstring coverage with `[FR-XX]` / `[NFR-XX]` tags and asserts
OpenAPI `summary`/`description` on every route.

### Decision
`taskq_api/models/schemas.py` declares pydantic v2 models:
`TaskCreate`, `TaskRead`, `RunCreate`, `RunRead`, `ProblemDetail`.
Every API handler declares `response_model=...` and `summary=...`
explicitly. The integration test
`tests/integration/test_nfr05_openapi.py` asserts `app.openapi()`
contains every route with `summary` and `description`.

### Rationale
- Pydantic v2 is Rust-backed and 5–50× faster than v1.
- `response_model=` produces OpenAPI documentation automatically.
- A separate `ProblemDetail` schema gives clients a typed parser
  for error bodies (RFC 7807).

### Consequences
- Positive: validation errors surface as 422 problem+json at the
  FastAPI layer, no handler code needed.
- Negative: pydantic v2's migration from v1 has subtle behaviour
  changes; we lock to a specific minor version in `requirements.txt`.

### Alternatives considered
- **msgspec**: rejected — pydantic v2's ecosystem (FastAPI, OpenAPI)
  is deeper.
- **Hand-rolled validators**: rejected — duplicates FastAPI's
  validation machinery.

---

## ADR-018: Dependency allowlist for license compliance (NFR-07)

### Status
Accepted

### Context
NFR-07 requires that every runtime dependency's license be in the
allowlist: `{MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF}`.
The SBOM at `08-config/SBOM.json` must match `requirements.lock`.

### Decision
- `requirements.txt` pins every dep with `==`.
- `requirements.lock` (transitive) generated by `pip-compile`.
- The Makefile target `lint-licenses` runs
  `pip-licenses --format=json --with-system` and asserts every
  license ∈ allowlist.
- An SBOM diff check fails the gate if a dep's license changes.

### Rationale
- Pinning prevents supply-chain surprises; the lock file catches
  transitive regressions.
- Limiting the allowlist to known-permissive licenses is a
  corporate-compliance baseline.

### Consequences
- Positive: legal review is automated.
- Negative: adding a new dep requires verifying its license first;
  e.g. GPL is forbidden.

### Alternatives considered
- **`pip-audit` only**: rejected — covers vulnerabilities, not
  licenses.
- **CycloneDX**: rejected — heavier tooling; the
  `pip-licenses`-based allowlist is sufficient for v1.0.0.

---

## ADR-019: Mutation testing scoped to `service/` and `repository/` with score ≥ 70

### Status
Accepted

### Context
NFR-08 requires a `mutmut` mutation score ≥ 70. The harness must
record the scope in `.methodology/harness_config.json` with a
justification.

### Decision
`mutmut` runs against `taskq_api/service/` and
`taskq_api/repository/` only. The `api/`, `models/`, `config`,
and `errors` modules are excluded because:
- `api/` is plumbing, exercised by integration tests directly.
- `models/` is declarative ORM; mutating a `Mapped[str]` column
  yields low-value mutants.
- `config` and `errors` are pure utilities covered by unit tests.

Score ≥ 70 on the scoped subset is the gate threshold.

### Rationale
- Scoping the mutation test keeps the run time within CI budget.
- The two business-logic-heavy layers are where mutant survival
  reveals real test gaps.

### Consequences
- Positive: mutation runs in < 5 minutes in CI.
- Negative: bugs in `api/` wiring (e.g. wrong `Depends()`) would
  not be caught by mutation; integration tests cover those.

### Alternatives considered
- **Project-wide mutation**: rejected — runs > 30 minutes on the
  expected codebase size and includes low-value mutants in
  declarative ORM definitions.

---

## ADR-020: Readability budgets enforced by `readability-v2` (NFR-11)

### Status
Accepted

### Context
NFR-11 mandates: Maintainability Index (LLOC-weighted) ≥ 80;
cyclomatic complexity ≤ 10 per function; ≤ 400 LOC per file;
≤ 15 files per dir; ≤ 40 LOC per API handler.

### Decision
`readability-v2` runs as a CI gate. The tool computes the LLOC-
weighted MI per module and per function, and fails the build if
any threshold is violated. Handler-length is asserted by an
AST scan that counts lines between the route decorator and the
end of the function body.

### Rationale
- Quantified limits are easier to police than "use your judgement".
- 40 LOC per handler keeps the routing layer thin — anything longer
  belongs in `service/`.

### Consequences
- Positive: consistent code shape across modules.
- Negative: large service functions (> 40 LOC) are acceptable here
  because the handler budget does not apply; we still target CC ≤ 10.

### Alternatives considered
- **Per-line formatter (black)**: rejected — covers style, not
  complexity.

---

## ADR-021: `__main__` CLI for operational subcommands (`migrate`, `key create`, `healthcheck`)

### Status
Accepted

### Context
Operators need a CLI to run migrations, create API keys, and probe
service health without booting the full HTTP server.

### Decision
`python -m taskq_api` exposes subcommands:
- `migrate` — runs `alembic upgrade head`.
- `key create --scope <read|write|admin>` — generates a key,
  returns it once, persists only the hash.
- `healthcheck` — connects to the DB, prints "OK" or exits 1.

### Rationale
- A single `python -m <package>` entry point is stdlib and does
  not require `click` or `typer`.
- The CLI must read `TASKQ_*` env vars via `config.get_settings()`
  — never re-implement env parsing.

### Consequences
- Positive: zero extra dependency; familiar operator UX.
- Negative: argparse boilerplate is verbose; acceptable for three
  subcommands.

### Alternatives considered
- **`click` / `typer`**: rejected — adds a dep for three
  subcommands.
- **Shell scripts**: rejected — bypasses the `config` redaction
  layer.

---

## ADR-022: Integration tests via `httpx.AsyncClient(transport=ASGITransport(app))`

### Status
Accepted

### Context
NFR-10 requires integration tests that exercise the FastAPI app
end-to-end. Coverage must reach ≥ 80% in `tests/integration/`.

### Decision
Every integration test uses
`httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` —
no network socket, no separate process. Each error code (401,
403, 404, 409, 422, 429, 503) has at least one integration test.

### Rationale
- ASGITransport runs the app in-process, so tests are fast and
  deterministic.
- `pytest-asyncio` lets us `await` the client calls.

### Consequences
- Positive: < 100 ms per integration test; full suite < 10 s.
- Negative: middleware that depends on real socket behavior
  (e.g. remote-addr allowlist) needs an explicit fake.

### Alternatives considered
- **`TestClient` (synchronous)**: rejected — would block the
  event loop during subprocess-style tests.
- **Docker-compose with a real uvicorn**: rejected — slow and
  flaky on CI.

---

## ADR-023: `metrics` lives in `service/`, not in `repository/`

### Status
Accepted

### Context
`/v1/metrics` (admin scope, FR-09) reads aggregate counts from the
DB. The naive placement would put it in `repository/` since it
reads, but the SAD §2.2.4 logical-constraints block mandates that
the read path go through `service.metrics → repository.*_repo`,
not `api → repository` directly. NFR-02 also forbids
sensitive-field leakage in the metrics body (T-08).

### Decision
`service/metrics.py` exposes `snapshot() -> MetricsSnapshot`. The
handler `GET /v1/metrics` calls `service.metrics.snapshot()`. The
metrics module MUST NOT return any field the repository layer
considers sensitive (DSN fragments, raw key material).

### Rationale
- Putting `metrics` in `service/` keeps the layering invariant
  (`api → service → repository`) — no shortcut edge.
- The `service.metrics` placement lets us apply `auth.redact()`
  to the metrics payload before serialisation.

### Consequences
- Positive: layering invariant holds; redaction is applied
  uniformly.
- Negative: a future "raw metrics" endpoint would have to be a
  new module — by design, no bypass of the service layer.

### Alternatives considered
- **`api.metrics` calling `repository.*_repo` directly**: rejected
  — violates the layering invariant; would also require redaction
  logic in the API layer.

---

## ADR-024: No third-party concurrency libraries; `asyncio` and `concurrent.futures` stdlib only

### Status
Accepted

### Context
The async executor (FR-02, FR-08) and the structured-concurrency
runner (ADR-006) are built on stdlib `asyncio`. No `trio`, `anyio`,
or `uvloop` is added in v1.0.0.

### Decision
Use only stdlib `asyncio` features: `TaskGroup`, `create_subprocess_exec`,
`Lock`, `Event`, `Queue`. If CPU-bound parallelism is ever needed,
`concurrent.futures.ThreadPoolExecutor` (also stdlib) is the
preferred escape hatch — already mentioned as the prompt's
expected pattern for file I/O or other blocking work.

### Rationale
- Every stdlib feature used is documented and stable across 3.11+.
- Adding `trio` would conflict with FastAPI's asyncio stack.
- `uvloop` brings a meaningful performance boost but introduces a
  platform-specific dependency (no Windows); defer until profiled
  need.

### Consequences
- Positive: zero extra concurrency deps; portable to any Python
  3.11 host.
- Negative: GIL-bound CPU work is single-threaded. If a future
  feature is CPU-heavy, we revisit with `ProcessPoolExecutor`.

### Alternatives considered
- **`uvloop`**: deferred — not needed for v1.0.0's throughput
  targets (p95 < 30 ms).
- **`trio`**: rejected — incompatible with FastAPI.
- **`anyio`**: rejected — adds an abstraction layer over asyncio
  with no v1.0.0 benefit.
