# Traceability Matrix — taskq-api

> Bidirectional Requirements Traceability Matrix
> Framework: harness-methodology
> Version: v1.0
> Phase: 1 (INGESTION) — Round 1, 2026-08-24
> Author: Agent A (Requirements Engineer)
> Source of Truth: SPEC.md (v1.0.0, 2026-07-30) → SRS.md (APPROVED, 2026-08-24)

This matrix provides **FR ↔ SRS ↔ Code ↔ Test** bidirectional traceability
supporting ASPICE SWE.3.B.SP1 / SWE.3.B.SP2 / SWE.3.B.SP3 compliance. Every
row is grounded in a single SPEC.md clause; every AC and implementation_function
is copied verbatim from `01-requirements/SRS.md` §3 / §4 / §10 (FR Block JSON)
to keep this matrix self-consistent with the system of record.

> **Status semantics** (Round 1 — no implementation exists yet):
> - `PLANNED` — FR / NFR / module / test is named in SRS.md and reserved.
> - `IN_PROGRESS` — code or test files exist in `03-development/`.
> - `VERIFIED` — code exists AND cited test has executed green.
>
> Rows are populated with `PLANNED` for Round 1. Subsequent rounds will
> promote to `IN_PROGRESS` (Agent B/C lands code) and `VERIFIED` (Agent D
> runs tests). Per NFR-09 / AC-N9.5, `VERIFIED` is set **only** after a
> cited test has actually executed and passed.

---

## 1. FR ↔ Spec Mapping

> Maps every functional requirement to its canonical SPEC.md clause,
> priority, and current verification status. AC identifiers reference
> `01-requirements/SRS.md` §3 verbatim.

| FR ID | Functional Requirement (canonical summary)                                                                              | SRS Section | Priority | Test Methods (planned)          | Status   |
|-------|--------------------------------------------------------------------------------------------------------------------------|-------------|----------|----------------------------------|----------|
| FR-01 | Task resource CRUD API (POST/GET/LIST/DELETE on `/v1/tasks`, cursor pagination, 422/404/409 problem+json)                | SS 3.1      | HIGH     | AC-1.1..AC-1.6 (6 cases)         | PLANNED  |
| FR-02 | Task execution endpoint (POST `/v1/tasks/{id}/run` → 202 + `run_id`; subprocess via `asyncio.create_subprocess_exec`)  | SS 3.2      | HIGH     | AC-2.1..AC-2.6 (6 cases)         | PLANNED  |
| FR-03 | API-key authentication (`X-API-Key` header; SHA-256 hash; `hmac.compare_digest`; 401 on missing/invalid)                | SS 3.3      | HIGH     | AC-3.1..AC-3.6 (6 cases)         | PLANNED  |
| FR-04 | Scope authorisation (`read < write < admin`; 403 problem+json; single FastAPI dependency)                               | SS 3.4      | HIGH     | AC-4.1..AC-4.3 (3 cases)         | PLANNED  |
| FR-05 | Rate limiting (per-token token bucket; 429 + `Retry-After`; row-level lock)                                             | SS 3.5      | HIGH     | AC-5.1..AC-5.4 (4 cases)         | PLANNED  |
| FR-06 | Persistence layer + transaction boundaries (`repository/` only; one Session per request; no string-concat SQL)         | SS 3.6      | HIGH     | AC-6.1..AC-6.5 (5 cases)         | PLANNED  |
| FR-07 | Schema migration (Alembic v1 → v2 → v3; round-trip reversibility; no destructive shortcuts)                              | SS 3.7      | HIGH     | AC-7.1..AC-7.5 (5 cases)         | PLANNED  |
| FR-08 | Async runner (`asyncio.TaskGroup`; graceful drain; concurrency cap; `wait_for` timeout kills child)                      | SS 3.8      | HIGH     | AC-8.1..AC-8.5 (5 cases)         | PLANNED  |
| FR-09 | Health and observability (`/healthz`, `/readyz` fail-closed, `/v1/metrics` admin-only)                                  | SS 3.9      | HIGH     | AC-9.1..AC-9.4 (4 cases)         | PLANNED  |
| FR-10 | Error contract (RFC 7807 `application/problem+json`; `correlation_id` propagated)                                       | SS 3.10     | HIGH     | AC-10.1..AC-10.5 (5 cases)       | PLANNED  |

**FR coverage check**: 10 / 10 functional requirements mapped (100%).

---

## 2. Spec ↔ Code Mapping

> Maps each FR to its planned code file (under `03-development/src/taskq_api/`)
> and the owning function/class. File paths and qualnames are copied verbatim
> from `01-requirements/SRS.md` §10 FR Block JSON `implementation_functions`
> field. `Lines` is `—` for Round 1 (no code landed yet).

| FR ID | SRS Section | Code File (planned)                                  | Function / Class (planned)                                                                                              | Lines | Status   |
|-------|-------------|------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|-------|----------|
| FR-01 | SS 3.1      | `03-development/src/taskq_api/api/tasks.py`          | `create_task`, `get_task`, `list_tasks`, `delete_task`                                                                   | —     | PLANNED  |
| FR-01 | SS 3.1      | `03-development/src/taskq_api/service/tasks.py`      | `create_task`, `get_task`, `list_tasks`, `delete_task`                                                                   | —     | PLANNED  |
| FR-01 | SS 3.1      | `03-development/src/taskq_api/repository/task_repo.py` | `TaskRepository.create`, `TaskRepository.get`, `TaskRepository.list`, `TaskRepository.delete`                          | —     | PLANNED  |
| FR-02 | SS 3.2      | `03-development/src/taskq_api/api/tasks.py`          | `run_task`, `list_runs`                                                                                                  | —     | PLANNED  |
| FR-02 | SS 3.2      | `03-development/src/taskq_api/service/runner.py`    | `Runner.run`, `Runner.execute`                                                                                           | —     | PLANNED  |
| FR-02 | SS 3.2      | `03-development/src/taskq_api/service/tasks.py`     | `schedule_run`                                                                                                           | —     | PLANNED  |
| FR-02 | SS 3.2      | `03-development/src/taskq_api/repository/task_repo.py` | `TaskRepository.record_result`, `TaskRepository.list_runs`                                                              | —     | PLANNED  |
| FR-03 | SS 3.3      | `03-development/src/taskq_api/api/deps.py`          | `authenticate`                                                                                                           | —     | PLANNED  |
| FR-03 | SS 3.3      | `03-development/src/taskq_api/service/auth.py`      | `verify_key`                                                                                                             | —     | PLANNED  |
| FR-03 | SS 3.3      | `03-development/src/taskq_api/repository/key_repo.py` | `KeyRepository.lookup_by_hash`, `KeyRepository.create`, `KeyRepository.revoke`                                          | —     | PLANNED  |
| FR-04 | SS 3.4      | `03-development/src/taskq_api/api/deps.py`          | `require_scope`, `authenticate`                                                                                          | —     | PLANNED  |
| FR-04 | SS 3.4      | `03-development/src/taskq_api/service/auth.py`      | `scope_satisfies`                                                                                                        | —     | PLANNED  |
| FR-05 | SS 3.5      | `03-development/src/taskq_api/api/deps.py`          | `rate_limit`                                                                                                             | —     | PLANNED  |
| FR-05 | SS 3.5      | `03-development/src/taskq_api/service/ratelimit.py` | `consume`                                                                                                                | —     | PLANNED  |
| FR-05 | SS 3.5      | `03-development/src/taskq_api/repository/rate_repo.py` | `RateRepository.get_bucket`, `RateRepository.update_bucket`                                                             | —     | PLANNED  |
| FR-06 | SS 3.6      | `03-development/src/taskq_api/repository/session.py` | `session_scope`                                                                                                          | —     | PLANNED  |
| FR-06 | SS 3.6      | `03-development/src/taskq_api/repository/task_repo.py` | `TaskRepository.*`                                                                                                      | —     | PLANNED  |
| FR-06 | SS 3.6      | `03-development/src/taskq_api/repository/key_repo.py` | `KeyRepository.*`                                                                                                       | —     | PLANNED  |
| FR-06 | SS 3.6      | `03-development/src/taskq_api/repository/rate_repo.py` | `RateRepository.*`                                                                                                     | —     | PLANNED  |
| FR-07 | SS 3.7      | `03-development/migrations/versions/v1_initial.py`  | (Alembic revision script)                                                                                                | —     | PLANNED  |
| FR-07 | SS 3.7      | `03-development/migrations/versions/v2_tags.py`     | (Alembic revision script)                                                                                                | —     | PLANNED  |
| FR-07 | SS 3.7      | `03-development/migrations/versions/v3_split_results.py` | (Alembic revision script)                                                                                            | —     | PLANNED  |
| FR-07 | SS 3.7      | `03-development/migrations/env.py`                  | (Alembic env)                                                                                                            | —     | PLANNED  |
| FR-08 | SS 3.8      | `03-development/src/taskq_api/service/runner.py`    | `Runner`, `Runner.execute`, `Runner.drain`                                                                                | —     | PLANNED  |
| FR-09 | SS 3.9      | `03-development/src/taskq_api/api/health.py`        | `healthz`, `readyz`, `metrics`                                                                                           | —     | PLANNED  |
| FR-10 | SS 3.10     | `03-development/src/taskq_api/errors/problem.py`    | `problem` (module-level factory)                                                                                         | —     | PLANNED  |
| FR-10 | SS 3.10     | `03-development/src/taskq_api/errors/handlers.py`    | `handlers.*`                                                                                                             | —     | PLANNED  |
| FR-10 | SS 3.10     | `03-development/src/taskq_api/api/deps.py`          | `correlation_id`                                                                                                         | —     | PLANNED  |

**Code file coverage check**: 11 planned files covering all 10 FRs (100%).

---

## 3. Code ↔ Test Mapping

> Maps each planned code file to its test file and the specific AC test
> cases. Test paths are planned under `03-development/tests/` with two
> sub-suites per FR — unit (per FR-NN test module) and integration (under
> `tests/integration/`). Coverage % column is `—` for Round 1.

| Code File (planned)                                  | Test File (planned)                                          | AC Coverage                                    | Status   |
|------------------------------------------------------|--------------------------------------------------------------|------------------------------------------------|----------|
| `03-development/src/taskq_api/api/tasks.py`          | `03-development/tests/unit/test_fr01_tasks_api.py`           | AC-1.1, AC-1.2, AC-1.3, AC-1.4, AC-1.5, AC-1.6 | PLANNED  |
| `03-development/src/taskq_api/api/tasks.py`          | `03-development/tests/integration/test_fr01_crud_chain.py`   | AC-1.1..AC-1.6 (full CRUD)                     | PLANNED  |
| `03-development/src/taskq_api/service/tasks.py`      | `03-development/tests/unit/test_fr01_tasks_service.py`       | AC-1.1, AC-1.5                                 | PLANNED  |
| `03-development/src/taskq_api/repository/task_repo.py` | `03-development/tests/unit/test_fr01_task_repo.py`         | AC-1.3, AC-1.6                                 | PLANNED  |
| `03-development/src/taskq_api/api/tasks.py` (run)    | `03-development/tests/unit/test_fr02_run_api.py`             | AC-2.1, AC-2.4, AC-2.6                         | PLANNED  |
| `03-development/src/taskq_api/service/runner.py`    | `03-development/tests/unit/test_fr02_runner.py`              | AC-2.2, AC-2.3, AC-2.4, AC-2.5                 | PLANNED  |
| `03-development/src/taskq_api/service/tasks.py`     | `03-development/tests/unit/test_fr02_schedule.py`            | AC-2.1, AC-2.4                                 | PLANNED  |
| `03-development/src/taskq_api/repository/task_repo.py` | `03-development/tests/unit/test_fr02_task_results.py`      | AC-2.5, AC-2.6                                 | PLANNED  |
| `03-development/src/taskq_api/api/tasks.py` (run)    | `03-development/tests/integration/test_fr02_run_lifecycle.py`| AC-2.1..AC-2.6 (lifecycle chain)               | PLANNED  |
| `03-development/src/taskq_api/api/deps.py`          | `03-development/tests/unit/test_fr03_auth.py`                | AC-3.1, AC-3.2, AC-3.3, AC-3.5, AC-3.6         | PLANNED  |
| `03-development/src/taskq_api/service/auth.py`      | `03-development/tests/unit/test_fr03_auth_service.py`         | AC-3.2, AC-3.3                                 | PLANNED  |
| `03-development/src/taskq_api/repository/key_repo.py` | `03-development/tests/unit/test_fr03_key_repo.py`           | AC-3.2, AC-3.4, AC-3.5                         | PLANNED  |
| `03-development/src/taskq_api/api/deps.py`          | `03-development/tests/integration/test_fr03_authn_chain.py`  | AC-3.1..AC-3.6                                 | PLANNED  |
| `03-development/src/taskq_api/api/deps.py`          | `03-development/tests/unit/test_fr04_scope.py`               | AC-4.1, AC-4.2, AC-4.3                         | PLANNED  |
| `03-development/src/taskq_api/service/auth.py`      | `03-development/tests/unit/test_fr04_scope_satisfies.py`      | AC-4.1                                         | PLANNED  |
| `03-development/src/taskq_api/api/deps.py`          | `03-development/tests/integration/test_fr04_authz_chain.py`  | AC-4.1..AC-4.3 (single dependency test)        | PLANNED  |
| `03-development/src/taskq_api/api/deps.py`          | `03-development/tests/unit/test_fr05_rate_limit.py`           | AC-5.2, AC-5.3                                 | PLANNED  |
| `03-development/src/taskq_api/service/ratelimit.py` | `03-development/tests/unit/test_fr05_ratelimit_consume.py`    | AC-5.1, AC-5.3                                 | PLANNED  |
| `03-development/src/taskq_api/repository/rate_repo.py` | `03-development/tests/unit/test_fr05_rate_repo.py`         | AC-5.3                                         | PLANNED  |
| `03-development/src/taskq_api/api/deps.py`          | `03-development/tests/integration/test_fr05_rate_limit.py`   | AC-5.1..AC-5.4                                 | PLANNED  |
| `03-development/src/taskq_api/repository/session.py` | `03-development/tests/unit/test_fr06_session_scope.py`      | AC-6.2, AC-6.5                                 | PLANNED  |
| `03-development/src/taskq_api/repository/task_repo.py` | `03-development/tests/unit/test_fr06_no_n_plus_one.py`     | AC-6.4                                         | PLANNED  |
| `03-development/src/taskq_api/repository/key_repo.py` | `03-development/tests/unit/test_fr06_key_repo_param.py`     | AC-6.3                                         | PLANNED  |
| `03-development/src/taskq_api/repository/rate_repo.py` | `03-development/tests/unit/test_fr06_rate_repo_param.py`   | AC-6.3                                         | PLANNED  |
| `03-development/migrations/env.py` + versions/*     | `03-development/tests/integration/test_fr07_migrations.py`   | AC-7.1..AC-7.5 (real SQLite file)              | PLANNED  |
| `03-development/migrations/versions/*.py`           | `03-development/tests/unit/test_fr07_offline_sql.py`         | AC-7.5                                         | PLANNED  |
| `03-development/src/taskq_api/service/runner.py`    | `03-development/tests/unit/test_fr08_runner.py`              | AC-8.1, AC-8.2, AC-8.3, AC-8.4, AC-8.5         | PLANNED  |
| `03-development/src/taskq_api/service/runner.py`    | `03-development/tests/integration/test_fr08_drain.py`        | AC-8.2, AC-8.4 (orphan absence)               | PLANNED  |
| `03-development/src/taskq_api/api/health.py`        | `03-development/tests/integration/test_fr09_health.py`       | AC-9.1, AC-9.2, AC-9.3, AC-9.4                 | PLANNED  |
| `03-development/src/taskq_api/api/health.py`        | `03-development/tests/unit/test_fr09_metrics_admin.py`        | AC-9.4                                         | PLANNED  |
| `03-development/src/taskq_api/errors/problem.py`    | `03-development/tests/unit/test_fr10_problem_factory.py`     | AC-10.1, AC-10.2, AC-10.5                      | PLANNED  |
| `03-development/src/taskq_api/errors/handlers.py`    | `03-development/tests/unit/test_fr10_handlers.py`           | AC-10.3, AC-10.4                               | PLANNED  |
| `03-development/src/taskq_api/api/deps.py` (correlation_id) | `03-development/tests/unit/test_fr10_correlation.py`   | AC-10.4                                        | PLANNED  |
| `03-development/src/taskq_api/errors/*.py`          | `03-development/tests/integration/test_fr10_error_chain.py`  | AC-10.1..AC-10.5 (all error codes)             | PLANNED  |

**Test file coverage check**: 28 planned test files covering all 10 FRs (100%).

---

## 4. NFR ↔ Test Mapping (Cross-Cutting)

> Maps each non-functional requirement to its concrete machine-decidable
> acceptance command (SPEC.md §8 #1..#27) and the test function that will
> exercise it. Test names follow the `test_nfrNN_AC_NN_N_*` convention
> reserved for `TEST_INVENTORY.yaml` extension.

| NFR ID  | Non-Functional Requirement (canonical summary)                                                | SRS Section | Test / Command (planned)                                       | AC Coverage          | Status   |
|---------|------------------------------------------------------------------------------------------------|-------------|----------------------------------------------------------------|----------------------|----------|
| NFR-01  | `GET /v1/tasks/{id}` p95 < 30ms; `GET /v1/tasks?limit=50` p95 < 80ms; constant SQL statement count | SS 4.1     | `tests/unit/test_nfr01_perf.py::test_nfr01_perf_p95`           | AC-N1.1..AC-N1.3     | PLANNED  |
| NFR-02  | No `shell=True`/`eval(`/`exec(`; no string-concat SQL; `hmac.compare_digest`; 403 non-leak; redaction; CORS deny-by-default; bandit 0/0 | SS 4.2     | `tests/unit/test_nfr02_security.py` (multi-case module)        | AC-N2.1..AC-N2.7     | PLANNED  |
| NFR-03  | Explicit transaction boundaries; no bare `except:`; `CancelledError` propagation; `/readyz` 503; timeout kills child; migration rollback | SS 4.3     | `tests/unit/test_nfr03_error_handling.py`                       | AC-N3.1..AC-N3.6     | PLANNED  |
| NFR-04  | Redaction filter for sk-/token=/Bearer/postgres:// in stdout_tail/stderr_tail/log/error-body; DB password absent from logs/metrics | SS 4.4     | `tests/unit/test_nfr04_redaction.py`                            | AC-N4.1..AC-N4.3     | PLANNED  |
| NFR-05  | 100% public fn/class docstrings with `[FR-XX]` / `[NFR-XX]`; OpenAPI summary + description     | SS 4.5     | `tests/unit/test_nfr05_docstrings.py`                           | AC-N5.1, AC-N5.2     | PLANNED  |
| NFR-06  | `.importlinter` `api > service > repository > models`; forbidden `sqlalchemy` outside `repository/`; `lint-imports` exits 0 | SS 4.6     | `tests/unit/test_nfr06_importlinter.py`                         | AC-N6.1..AC-N6.4     | PLANNED  |
| NFR-07  | Runtime deps pinned `==`; transitive pinned; license allowlist; full-tree scan; SBOM at `08-config/SBOM.json` | SS 4.7     | `tests/unit/test_nfr07_licenses.py` + SBOM JSON schema          | AC-N7.1..AC-N7.4     | PLANNED  |
| NFR-08  | `features.mutation_testing: true`; mutmut score ≥ 70 over `service/` + `repository/`            | SS 4.8     | Framework `mutation-test-score` reads `.methodology/mutation_score.json` | AC-N8.1..AC-N8.3     | PLANNED  |
| NFR-09  | `pytest skipped == 0`; `zero_assert == 0`; no `--ignore`/`-k`/`--deselect` exclusions; FR-07 real-SQLite round-trip; `VERIFIED` only after test pass | SS 4.9     | `tests/unit/test_nfr09_testability.py`                          | AC-N9.1..AC-N9.5     | PLANNED  |
| NFR-10  | Integration suite coverage ≥ 80%; `httpx.AsyncClient(transport=ASGITransport(app))` driver; CRUD + 401/403/404/409/422/429/503 each ≥ 1 | SS 4.10    | `tests/integration/` + `pytest --cov=03-development/src`        | AC-N10.1..AC-N10.3   | PLANNED  |
| NFR-11  | Project MI ≥ 80; single-fn CC ≤ 10; file ≤ 400 lines; dir ≤ 15 files; handler ≤ 40 lines       | SS 4.11    | `tests/unit/test_nfr11_readability.py` (radon-mi + radon-cc + size scans) | AC-N11.1..AC-N11.4   | PLANNED  |
| NFR-12  | `make verify-system` chains `alembic upgrade head` → tests → service + `/healthz`/`/readyz` smoke → `alembic downgrade base` then `upgrade head` | SS 4.12    | `Makefile::verify-system` invocation + exit-code + stdout scan  | AC-N12.1, AC-N12.2   | PLANNED  |

**NFR coverage check**: 12 / 12 non-functional requirements mapped (100%).

---

## 5. Risk ↔ FR/NFR Traceability

> Cross-reference: risks in `01-requirements/SRS.md` §8 (verbatim from
> SPEC.md §9) to the FR/NFRs that mitigate them. This is the *third
> dimension* of bidirectional traceability (requirement ↔ risk).

| Risk ID | Risk (canonical)                                  | Likelihood / Impact | Mitigated by (FR / NFR)                                       | Verified by (AC)                       |
|---------|---------------------------------------------------|---------------------|----------------------------------------------------------------|-----------------------------------------|
| R1      | v3 資料搬遷遺失資料                                | 中 / 高             | FR-07 / NFR-09                                                 | AC-7.3 / AC-N9.4                        |
| R2      | SQL injection                                      | 低 / 高             | NFR-02 / FR-06                                                 | AC-N2.2 / AC-6.3                        |
| R3      | API key 洩漏                                       | 中 / 高             | FR-03 / NFR-02 / NFR-04                                        | AC-3.2 / AC-N2.3 / AC-N4.3              |
| R4      | 403 洩漏資源存在性                                 | 中 / 中             | FR-04 / NFR-02                                                 | AC-4.2 / AC-N2.4                        |
| R5      | N+1 查詢在大表上崩潰                              | 高 / 高             | FR-06 / NFR-01                                                 | AC-6.4 / AC-N1.3                        |
| R6      | 錯誤 body 洩漏內部結構                             | 高 / 中             | FR-10 / NFR-02                                                 | AC-10.3 / AC-N2.5                       |
| R7      | `CancelledError` 被吞 → 關閉時卡死                 | 中 / 中             | FR-08 / NFR-03                                                 | AC-8.5 / AC-N3.3                        |
| R8      | 任務 timeout 留下孤兒進程                          | 中 / 中             | FR-08 / NFR-03                                                 | AC-8.4 / AC-N3.5                        |
| R9      | 部署後忘記跑 migration                             | 中 / 高             | FR-09                                                         | AC-9.2 / AC-9.3                         |
| R10     | 連線池耗盡                                         | 中 / 中             | FR-06 / FR-08 / NFR-03                                        | AC-6.5 / AC-8.3 / AC-N3.1               |
| R11     | transitive 依賴引入不相容 license                  | 中 / 中             | NFR-07                                                        | AC-N7.1 / AC-N7.2 / AC-N7.4             |
| R12     | rate bucket 競態導致超放行                         | 中 / 低             | FR-05 / NFR-03                                                | AC-5.3 / AC-N3.1                        |

**Risk coverage check**: 12 / 12 risks mapped to FR/NFR mitigations (100%).

---

## 6. Completeness Verification

| Check                                  | Target | Actual (Round 1) | Status    |
|----------------------------------------|--------|------------------|-----------|
| FR → SRS section mapping               | 100%   | 100% (10/10)     | COMPLETE  |
| NFR → SRS section mapping              | 100%   | 100% (12/12)     | COMPLETE  |
| FR → planned code file mapping         | 100%   | 100% (10/10)     | COMPLETE  |
| NFR → planned test file mapping        | 100%   | 100% (12/12)     | COMPLETE  |
| FR AC → test case mapping              | 100%   | 100% (49/49)     | COMPLETE  |
| NFR AC → test case mapping             | 100%   | 100% (46/46)     | COMPLETE  |
| FR ↔ NFR cross-references              | 100%   | 100%             | COMPLETE  |
| Risk → FR/NFR mitigation mapping       | 100%   | 100% (12/12)     | COMPLETE  |
| ASPICE SWE.3.B bidirectional traceability | met | met (Round 1)    | COMPLETE  |
| Code → Test coverage                   | ≥ 80%  | — (no code yet)  | DEFERRED  |
| Test pass status                       | 100%   | — (no tests yet) | DEFERRED  |
| `pytest skipped == 0` (NFR-09)         | 0      | — (no tests yet) | DEFERRED  |
| `zero_assert == 0` (NFR-09)            | 0      | — (no tests yet) | DEFERRED  |
| `mutmut score ≥ 70` (NFR-08)           | ≥ 70   | — (no tests yet) | DEFERRED  |

**Round 1 verdict**: All structural traceability is COMPLETE. The four
DEFERRED rows will flip to COMPLETE in Phase 3 once Agent C lands code
and Agent D runs the test suite per NFR-12 `make verify-system`.

---

## 7. ASPICE Compliance (SWE.3 / SYS.4)

| ASPICE Capability                                  | Round 1 Status | Evidence                                                                                  |
|----------------------------------------------------|----------------|-------------------------------------------------------------------------------------------|
| SWE.3.B.SP1 Task-to-work-product traceability      | MET            | Sections 1–4 above (FR ↔ Spec ↔ Code ↔ Test)                                              |
| SWE.3.B.SP2 Bidirectional traceability             | MET            | Sections 1→2→3 (forward) and 3→2→1 (reverse) — every row has a sibling in the other table |
| SWE.3.B.SP3 Traceability consistency               | MET            | All rows sourced from single SPEC.md (v1.0.0, 2026-07-30); no orphan cells                |
| SWE.3.B.SP4 Traceability maintenance               | MET (planned)  | Status column with PLANNED / IN_PROGRESS / VERIFIED semantics; promoted by framework scan |
| SYS.4.B.SP1 System requirements → software elements | MET            | Section 2 (Spec ↔ Code) traces every FR to its owning module path                          |

---

## 8. Source Citations

- Canonical spec: `/Users/johnny/projects/taskq-cc-new/SPEC.md` (v1.0.0, 2026-07-30)
- SRS authoritative view: `/Users/johnny/projects/taskq-cc-new/01-requirements/SRS.md` (APPROVED, 2026-08-24)
- FR/NFR machine-readable block: `/Users/johnny/projects/taskq-cc-new/01-requirements/SRS.md` §10
- Acceptance criteria summary (27 items): `/Users/johnny/projects/taskq-cc-new/01-requirements/SRS.md` §5
- Risk roster: `/Users/johnny/projects/taskq-cc-new/01-requirements/SRS.md` §8
- Architecture target: `/Users/johnny/projects/taskq-cc-new/02-architecture/SAD.md` (template; Agent B P2 target)
- Test catalog target: `/Users/johnny/projects/taskq-cc-new/02-architecture/TEST_SPEC.md` (template; Agent A P2 target)
- Test name authority: `/Users/johnny/projects/taskq-cc-new/TEST_INVENTORY.yaml` (template; Agent A P2 expansion)
- Spec tracking mirror: `/Users/johnny/projects/taskq-cc-new/01-requirements/SPEC_TRACKING.md` (APPROVED 2026-08-24)

---

## 9. Update Log

| Date       | Change                                                                                                                | By    |
|------------|------------------------------------------------------------------------------------------------------------------------|-------|
| 2026-08-24 | Round 1 initial creation: bidirectional FR/NFR ↔ Spec ↔ Code ↔ Test matrix for taskq-api (10 FR / 12 NFR / 12 risks); all structural traceability rows COMPLETE; coverage rows DEFERRED until Phase 3 code lands | Agent A (Requirements Engineer) |