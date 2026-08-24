# Software Requirements Specification (SRS) — taskq-api

> Phase 1 INGESTION MODE. Canonical spec: `SPEC.md` (v1.0.0, 2026-07-30).
> 10 FR / 12 NFR / 12 env vars — transcribed verbatim from SPEC.md §§3–5.
> Per `harness/ssi/prompts/canonical_diff.py` over-spec guard:
> when canonical uses ambiguous phrasing, the verbatim canonical phrase
> is transcribed into the AC; measurement / interpretation boundary is
> owned by the test harness per the same canonical line.

---

## 1. Introduction

### 1.1 Purpose
This SRS captures the complete, machine-checkable requirements for the
`taskq-api` project — a HTTP task-queue service that submits, queries and
executes shell-command tasks over a REST API; persists state to a relational
database through SQLAlchemy; evolves the schema via Alembic; and authenticates
with hashed API keys, authorises by per-token scope, and rate-limits per token.

It is the **single source of truth** for downstream artefacts: `02-architecture/SAD.md`,
`TEST_SPEC.md`, `SAB.md`, the implementation in `03-development/src/taskq_api/`,
and the test suites under `03-development/tests/`.

### 1.2 Scope
In scope:
- 10 functional requirements (FR-01..FR-10) covering REST CRUD, async
  execution, API-key auth, scope authorisation, token-bucket rate limiting,
  repository / transaction layer, Alembic schema migrations, async runner
  with graceful drain, health/readiness/observability, RFC 7807 error contract.
- 12 non-functional requirements (NFR-01..NFR-12) covering performance,
  security, error handling, sensitive-data redaction, documentation,
  architecture constraints, license compliance, mutation testing,
  test-assertion quality, integration coverage, readability, and the
  end-to-end system verification target.
- 12 environment variables (TASKQ_*) and the full database schema (`tasks`,
  `api_keys`, `rate_buckets`, `tags`, `task_tags`, `task_results`).
- 4-layer module structure (`api > service > repository > models`) enforced
  by `.importlinter` plus a `sqlalchemy` forbidden contract.

### 1.3 Definitions, Acronyms, Abbreviations
See §9 Glossary.

### 1.4 References
- `SPEC.md` v1.0.0 (canonical, 2026-07-30) — all clauses FR-01..FR-10,
  NFR-01..NFR-12, §5 env vars, §6 module layout, §7 error status map,
  §8 27 acceptance items.
- `PROJECT_BRIEF.md` — Round-2 brief, canonical_spec field.
- `harness/harness/ssi/prompts/evaluate_dimension.md` — current
  framework dimension roster (used to validate NFR `dimension:` fields).
- RFC 7807 — Problem Details for HTTP APIs.
- RFC 6585 — Additional HTTP Status Codes (429).
- PEP 8 / PEP 257 — Python style / docstring conventions.

### 1.5 Document Overview
§2 Constraints · §3 Functional Requirements · §4 Non-Functional
Requirements · §5 Acceptance Criteria Summary · §6 Out-of-Scope ·
§7 Open Issues · §8 Risks · §9 Glossary · §10 FR Block (machine-readable).

---

## 2. Constraints

| ID | Constraint | Source |
|----|-----------|--------|
| C-01 | Python 3.11 runtime | SPEC.md §1 |
| C-02 | ASGI service — `uvicorn taskq_api.app:app`; also `python -m taskq_api` admin entry | SPEC.md §1 |
| C-03 | HTTP framework: FastAPI (ASGI) | SPEC.md §2 |
| C-04 | Data validation: pydantic v2 request/response models | SPEC.md §2 |
| C-05 | ORM: SQLAlchemy 2.x (declarative + explicit `Session` transaction boundaries) | SPEC.md §2 |
| C-06 | Database: SQLite (dev/test), PostgreSQL (prod) — same ORM models | SPEC.md §2 |
| C-07 | Migration: Alembic — v1 → v2 → v3, every step has `downgrade` | SPEC.md §2 / FR-07 |
| C-08 | Async: `async def` endpoints + `asyncio.TaskGroup` background runner | SPEC.md §2 |
| C-09 | Authentication: `X-API-Key` header, keys hashed at rest, never plaintext | SPEC.md §2 / FR-03 |
| C-10 | Authorisation: per-token scope `read` / `write` / `admin` (hierarchical) | SPEC.md §2 / FR-04 |
| C-11 | Rate limit: per-token token bucket | SPEC.md §2 / FR-05 |
| C-12 | Error contract: RFC 7807 `application/problem+json` | SPEC.md §2 / FR-10 |
| C-13 | Task execution: `asyncio.create_subprocess_exec` — `shell=True` forbidden everywhere | SPEC.md §2 / FR-02 / NFR-02 |
| C-14 | Layering: `api > service > repository > models`; `config` / `errors` independent; `sqlalchemy` importable only from `repository/` (enforced by `.importlinter`) | SPEC.md §6 / NFR-06 |
| C-15 | High-risk modules requiring per-module TDD: `taskq_api.service.runner`, `taskq_api.service.auth`, `taskq_api.repository.session`, `migrations/versions/v3_split_results.py` | PROJECT_BRIEF.md §"Source of Truth" |
| C-16 | Project-side config files are non-optional: `.importlinter`, `requirements.txt` + `requirements.lock`, `requirements-dev.txt`, `alembic.ini` + `migrations/versions/`, `.env.example`, `.methodology/harness_config.json`, `Makefile` | SPEC.md §5.3 |
| C-17 | `crg_cohesion_healthy` retains its default value — may not be lowered to pass a gate | SPEC.md §10 |

---

## 3. Functional Requirements

> Each FR is transcribed from SPEC.md §3. Verbatim canonical phrasing is kept
> in each AC; measurement / interpretation boundary is owned by the test
> harness per the same canonical line.

### FR-01: 任務資源 CRUD API

> Source: SPEC.md §3 FR-01.

| Method | Path | scope | 行為 |
|--------|------|-------|------|
| `POST` | `/v1/tasks` | `write` | 建立任務;body 由 `TaskCreate` pydantic 模型驗證 |
| `GET` | `/v1/tasks/{id}` | `read` | 取得單一任務全欄位 |
| `GET` | `/v1/tasks` | `read` | 分頁列表,支援 `?status=`、`?limit=`、`?cursor=` |
| `DELETE` | `/v1/tasks/{id}` | `admin` | 刪除任務(連同結果列,同一交易) |

Canonical phrasing: 驗證規則同第 1 輪 FR-01(非空 / ≤1000 字元 / 注入字元黑名單 / 名稱唯一);
違反 → HTTP 422 + problem+json。 未知 id → HTTP 404 + problem+json。 分頁為
cursor-based(不得用 offset)。 列表端點的預設 limit 為 50,上限 200;超過上限 → 422。

**Acceptance criteria**
- **AC-1.1** `POST /v1/tasks` with a valid `write`-scope API key and a body
  satisfying the canonical validation rules — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-01.
- **AC-1.2** `POST /v1/tasks` violating any of the canonical validation
  rules returns `HTTP 422` and a `application/problem+json` body
  (`type=/errors/validation`) — measurement / interpretation boundary is
  owned by the test harness per SPEC.md §3 FR-01 / §7.
- **AC-1.3** `GET /v1/tasks/{id}` for an unknown id returns `HTTP 404`
  and a `application/problem+json` body (`type=/errors/not-found`) —
  measurement / interpretation boundary is owned by the test harness per
  SPEC.md §3 FR-01 / §7.
- **AC-1.4** `GET /v1/tasks` paginates with **cursor-based** pagination
  (offset pagination is forbidden) — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-01.
- **AC-1.5** `GET /v1/tasks?limit=N` enforces default `limit=50`,
  upper bound `200`, and returns `HTTP 422` when the limit exceeds the
  upper bound — measurement / interpretation boundary is owned by the
  test harness per SPEC.md §3 FR-01.
- **AC-1.6** `DELETE /v1/tasks/{id}` removes the task and its result
  rows in the same transaction — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-01.

### FR-02: 任務執行端點

> Source: SPEC.md §3 FR-02.

Canonical phrasing: `POST /v1/tasks/{id}/run`(scope `write`)→ HTTP 202 Accepted,
body 含 `run_id`. 實際執行以 `asyncio.create_subprocess_exec(*shlex.split(command))`
進行,禁 `shell=True`,timeout 為 `TASKQ_TASK_TIMEOUT`. 狀態機:
`pending → running → done | failed | timeout`. 執行結果寫入 `task_results`
表,欄位: `exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` /
`finished_at`. `GET /v1/tasks/{id}/runs`(scope `read`)→ 該任務的歷史執行紀錄,
新到舊排序.

**Acceptance criteria**
- **AC-2.1** `POST /v1/tasks/{id}/run` returns `HTTP 202 Accepted` and
  the body contains a `run_id` — measurement / interpretation boundary
  is owned by the test harness per SPEC.md §3 FR-02.
- **AC-2.2** Task execution uses
  `asyncio.create_subprocess_exec(*shlex.split(command))` with
  `shell=True` forbidden — measurement / interpretation boundary is
  owned by the test harness per SPEC.md §3 FR-02 / NFR-02.
- **AC-2.3** Per-task subprocess timeout equals `TASKQ_TASK_TIMEOUT`
  (seconds) — measurement / interpretation boundary is owned by the
  test harness per SPEC.md §3 FR-02 / §5.1.
- **AC-2.4** Task state machine transitions through
  `pending → running → done | failed | timeout` — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-02.
- **AC-2.5** Execution results are written to the `task_results` table
  with columns `exit_code` / `stdout_tail` / `stderr_tail` /
  `duration_ms` / `finished_at` — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-02 / §5.2.
- **AC-2.6** `GET /v1/tasks/{id}/runs` returns that task's execution
  history ordered newest-to-oldest — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-02.

### FR-03: API Key 認證

> Source: SPEC.md §3 FR-03.

Canonical phrasing: 全部 `/v1/*` 端點要求 `X-API-Key` header;缺少或無效 →
HTTP 401 + problem+json. 金鑰以 SHA-256 雜湊儲存於 `api_keys` 表,不得存
明文;比對用 `hmac.compare_digest`(常數時間). 金�由
`python -m taskq_api key create --scope <scope>` 產生,明文只在建立當下
印出一次. 停用金鑰:`revoked_at` 非空的金鑰一律視為無效. `/healthz`、
`/readyz` 不要求認證.

**Acceptance criteria**
- **AC-3.1** Every `/v1/*` endpoint requires the `X-API-Key` header;
  a missing or invalid key returns `HTTP 401` and a
  `application/problem+json` body (`type=/errors/unauthenticated`) —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §3 FR-03 / §7.
- **AC-3.2** API keys are stored as SHA-256 hashes in the `api_keys`
  table; plaintext keys are never persisted — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-03 / NFR-02.
- **AC-3.3** API key comparison uses `hmac.compare_digest`
  (constant-time) — measurement / interpretation boundary is owned
  by the test harness per SPEC.md §3 FR-03 / NFR-02.
- **AC-3.4** `python -m taskq_api key create --scope <scope>` produces
  a key whose plaintext is printed exactly once at creation time —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §3 FR-03.
- **AC-3.5** A key whose `revoked_at` is non-null is treated as invalid
  for every `/v1/*` endpoint — measurement / interpretation boundary
  is owned by the test harness per SPEC.md §3 FR-03.
- **AC-3.6** `/healthz` and `/readyz` do not require authentication —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §3 FR-03 / FR-09.

### FR-04: Scope 授權

> Source: SPEC.md §3 FR-04.

Canonical phrasing: 每把金鑰帶一個 scope:`read` < `write` < `admin`(階層
包含). 端點所需 scope 見 FR-01/02 表;不足 → HTTP 403 + problem+json,且
body 不得洩漏該資源是否存在. 授權判定必須在單一中介層(dependency)完成,
不得散落於各 handler —— 以測試斷言「每個 `/v1` 路由都經過同一個
dependency」.

**Acceptance criteria**
- **AC-4.1** Scope hierarchy is `read` < `write` < `admin` with `admin`
  inclusive of `write` and `write` inclusive of `read` — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-04.
- **AC-4.2** Insufficient scope for any `/v1/*` endpoint returns
  `HTTP 403` and a `application/problem+json` body
  (`type=/errors/forbidden`) whose body does not leak whether the
  target resource exists — measurement / interpretation boundary is
  owned by the test harness per SPEC.md §3 FR-04 / §7 / NFR-02.
- **AC-4.3** The authorisation decision is made in a single FastAPI
  dependency (the "single authn/authz decision point" per SPEC.md §6);
  every `/v1` route traverses the same dependency — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-04 / §6.

### FR-05: 流量控制

> Source: SPEC.md §3 FR-05.

Canonical phrasing: per-token 令牌桶:容量 `TASKQ_RATE_BURST`,補充速率
`TASKQ_RATE_PER_SEC`. 超限 → HTTP 429 + problem+json + `Retry-After`
header(秒). 令牌桶狀態存於資料庫(跨 worker 一致),更新必須在單一交易內
以 row-level lock 進行. `/healthz`、`/readyz` 不受限.

**Acceptance criteria**
- **AC-5.1** Per-token token bucket has capacity `TASKQ_RATE_BURST`
  and refill rate `TASKQ_RATE_PER_SEC` (tokens/second) — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-05 / §5.1.
- **AC-5.2** Exceeding the bucket returns `HTTP 429` with a
  `application/problem+json` body (`type=/errors/rate-limited`) and a
  `Retry-After` header in seconds — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-05 / §7.
- **AC-5.3** Bucket state lives in the database (consistent across
  workers) and is updated within a single transaction holding a
  row-level lock — measurement / interpretation boundary is owned by
  the test harness per SPEC.md §3 FR-05.
- **AC-5.4** `/healthz` and `/readyz` are not rate-limited —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §3 FR-05.

### FR-06: 持久化層與交易邊界

> Source: SPEC.md §3 FR-06.

Canonical phrasing: 全部資料存取經由 `repository/` 層,業務層不得直接持有
`Session`. 每個 API 請求一個 `Session`,交易邊界明確:成功 commit、例外
rollback(以 context manager 保證). 禁止字串拼接 SQL;一律使用 ORM 或
參數化查詢. 關聯查詢必須用 `selectinload` / `joinedload` 顯式預載 ——
N+1 為驗收失敗條件. 連線池:`pool_size=TASKQ_DB_POOL_SIZE`,
`pool_pre_ping=True`.

**Acceptance criteria**
- **AC-6.1** All data access flows through the `repository/` layer;
  the business layer does not hold a `Session` directly — measurement
  / interpretation boundary is owned by the test harness per SPEC.md
  §3 FR-06 / §6 / NFR-06.
- **AC-6.2** Each API request uses exactly one `Session`; transaction
  boundaries are explicit — commit on success, rollback on exception,
  enforced by a context manager — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-06.
- **AC-6.3** No string-concatenated SQL exists; all queries use the
  ORM or parameterised statements — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-06 / NFR-02.
- **AC-6.4** Relationship loads are explicit (`selectinload` /
  `joinedload`); the list endpoint SQL statement count is constant
  with respect to result row count (N+1 is a failure condition) —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §3 FR-06 / NFR-01.
- **AC-6.5** Connection pool uses
  `pool_size=TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True` —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §3 FR-06 / §5.1.

### FR-07: Schema Migration(Alembic 三步演進)

> Source: SPEC.md §3 FR-07.

| revision | upgrade 內容 | downgrade 要求 |
|----------|-------------|----------------|
| **v1** | 建立 `tasks`、`api_keys` 兩表 | drop 兩表 |
| **v2** | 新增 `tags`、`task_tags`(多對多)+ `tasks.name` 唯一索引 | drop 新表與索引,不影響 v1 資料 |
| **v3** | 含資料搬遷:把 `tasks.result_json` 拆為獨立的 `task_results` 表,搬遷既有資料後移除原欄位 | 反向搬遷回 `tasks.result_json` 後 drop `task_results`,資料不得遺失 |

Canonical phrasing: `alembic upgrade head` 與 `alembic downgrade base` 必須
都成功. 往返可逆性驗收:`upgrade head` → 寫入樣本資料 → `downgrade -1` →
`upgrade head`,樣本資料的欄位值必須逐欄相同. 禁止以 `op.execute("DROP
TABLE ...")` 之類的破�性捷徑取代真正的 downgrade. migration 檔本身納入
測試覆蓋(以 `alembic` 的 offline SQL 產生 + 斷言).

**Acceptance criteria**
- **AC-7.1** `alembic upgrade head` succeeds against a real SQLite
  database file (not an in-memory mock) — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-07 / NFR-09.
- **AC-7.2** `alembic downgrade base` succeeds and leaves no
  residual tables — measurement / interpretation boundary is owned
  by the test harness per SPEC.md §3 FR-07.
- **AC-7.3** Round-trip reversibility: `upgrade head` → write sample
  data → `downgrade -1` → `upgrade head` leaves every sample-data
  column byte-identical (the v3 data move is the focus of this
  acceptance) — measurement / interpretation boundary is owned by the
  test harness per SPEC.md §3 FR-07.
- **AC-7.4** No destructive shortcuts such as `op.execute("DROP TABLE
  ...")` substitute for a real `downgrade()` — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-07.
- **AC-7.5** Each migration file is covered by tests that produce
  Alembic offline SQL and assert against it — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-07.

### FR-08: 非同步執行器

> Source: SPEC.md §3 FR-08.

Canonical phrasing: 背景執行以 `asyncio.TaskGroup` 管理;服務關閉時必須
graceful drain(等待進行中的任務至 `TASKQ_DRAIN_TIMEOUT`,逾時則標記
`interrupted`). 併發上限 `TASKQ_MAX_CONCURRENT`;超過時新任務排隊,不得
無限制生成 coroutine. 任務 timeout 以 `asyncio.wait_for` 實作;逾時必須
確實終止子進程(`process.kill()` 後 `await process.wait()`),不得留下孤兒
進程. 取消語意:`asyncio.CancelledError` 必須向上傳播,不得被 `except
Exception` 吞掉.

**Acceptance criteria**
- **AC-8.1** Background execution is managed with `asyncio.TaskGroup`
  — measurement / interpretation boundary is owned by the test harness
  per SPEC.md §3 FR-08.
- **AC-8.2** On shutdown the service performs a graceful drain waiting
  for in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`; tasks exceeding the
  budget are marked `interrupted` — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-08 / §5.1.
- **AC-8.3** Concurrency is capped at `TASKQ_MAX_CONCURRENT`; surplus
  tasks queue rather than spawning unbounded coroutines — measurement
  / interpretation boundary is owned by the test harness per SPEC.md
  §3 FR-08 / §5.1.
- **AC-8.4** Task timeout uses `asyncio.wait_for` and on timeout
  terminates the child process via `process.kill()` followed by
  `await process.wait()`, leaving no orphan processes — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-08.
- **AC-8.5** `asyncio.CancelledError` propagates upward; it must not
  be swallowed by `except Exception` — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-08 / NFR-03.

### FR-09: 健康檢查與可觀測性

> Source: SPEC.md §3 FR-09.

| 端點 | 認證 | 行為 |
|------|------|------|
| `GET /healthz` | 無 | 進程存活 → 200 `{"status":"ok"}` |
| `GET /readyz` | 無 | DB 連線可用 **且** `alembic current` == head → 200;否則 **503** 並在 body 說明哪一項失敗 |
| `GET /v1/metrics` | `admin` | 任務計數(按狀態)、執行延遲分位數、rate-limit 拒絕數 |

Canonical phrasing: `/readyz` 的「migration 未到 head」判定是關鍵:部署了
新程式碼但忘記跑 migration 時必須 fail closed.

**Acceptance criteria**
- **AC-9.1** `GET /healthz` returns `HTTP 200` with body
  `{"status":"ok"}` while the process is alive — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-09.
- **AC-9.2** `GET /readyz` returns `HTTP 200` only when the DB is
  reachable AND `alembic current` equals head; otherwise it returns
  `HTTP 503` (`type=/errors/not-ready`) with the body identifying
  which condition failed — measurement / interpretation boundary is
  owned by the test harness per SPEC.md §3 FR-09 / §7.
- **AC-9.3** Deploying new code without running migrations causes
  `/readyz` to fail closed (`HTTP 503`) — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §3 FR-09.
- **AC-9.4** `GET /v1/metrics` requires `admin` scope and reports task
  counts by status, execution-latency percentiles, and rate-limit
  rejection counts — measurement / interpretation boundary is owned
  by the test harness per SPEC.md §3 FR-09.

### FR-10: 錯誤契約(RFC 7807)

> Source: SPEC.md §3 FR-10.

Canonical phrasing: 全部非 2xx 回應的 `Content-Type` 為
`application/problem+json`. body 欄位:`type`(URI)、`title`、`status`、
`detail`、`instance`、`correlation_id`. `detail` 不得洩漏內部細節:不得
含 SQL 陳述、堆疊追蹤、檔案路徑、資料庫結構描述. `correlation_id` 同時
出現在回應 header `X-Correlation-Id` 與伺服器日誌,可用於串接. 錯誤碼對照:
422 驗證 / 401 未認證 / 403 scope 不足 / 404 未知資源 / 409 名稱衝突 /
429 超限 / 503 未就緒 / 500 其他.

**Acceptance criteria**
- **AC-10.1** Every non-2xx response has `Content-Type:
  application/problem+json` — measurement / interpretation boundary
  is owned by the test harness per SPEC.md §3 FR-10.
- **AC-10.2** The problem+json body has fields `type` (URI), `title`,
  `status`, `detail`, `instance`, `correlation_id` — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-10.
- **AC-10.3** The `detail` field never contains SQL statements, stack
  traces, file paths, or database schema descriptions — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-10 / NFR-02.
- **AC-10.4** The `correlation_id` value appears both in the response
  header `X-Correlation-Id` and in the server log, enabling end-to-end
  stitching — measurement / interpretation boundary is owned by the
  test harness per SPEC.md §3 FR-10.
- **AC-10.5** Error-code mapping matches SPEC.md §7: 422 validation,
  401 unauthenticated, 403 forbidden, 404 not-found, 409 conflict, 429
  rate-limited, 503 not-ready, 500 internal — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §3
  FR-10 / §7.

---

## 4. Non-Functional Requirements

> Each NFR is transcribed from SPEC.md §4. Each `dimension:` is one of the
> dimensions currently listed in
> `harness/harness/ssi/prompts/evaluate_dimension.md` (verified by
> grepping that file at authoring time). All 12 dimensions used here
> (performance / security / error_handling / documentation /
> architecture_constraints / license_compliance / mutation_testing /
> test_assertion_quality / integration_coverage / readability /
> execute_verification_target) are present in that current roster.

### NFR-01: 效能與查詢效率

> Source: SPEC.md §4 NFR-01. Dimension: `performance`.

Canonical phrasing: `GET /v1/tasks/{id}` 在 10,000 筆資料下 p95 < 30ms(不含
網路,以 ASGI transport 量測). `GET /v1/tasks?limit=50` 在 10,000 筆資料下
p95 < 80ms. N+1 為失敗條件:列表端點回應一次請求所發出的 SQL 陳述數必須是
常數(與回傳筆數無關),以 SQLAlchemy event listener 計數斷言. 量測方式:
`pytest-benchmark`.

**Acceptance criteria**
- **AC-N1.1** `GET /v1/tasks/{id}` at 10,000 rows has p95 < 30ms
  measured through the ASGI transport (excluding network) — measurement
  / interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-01.
- **AC-N1.2** `GET /v1/tasks?limit=50` at 10,000 rows has p95 < 80ms
  measured through the ASGI transport (excluding network) — measurement
  / interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-01.
- **AC-N1.3** The list endpoint emits a constant number of SQL
  statements regardless of result row count (N+1 guard); the count is
  asserted via a SQLAlchemy event listener — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-01.

### NFR-02: HTTP 與資料層安全

> Source: SPEC.md §4 NFR-02. Dimension: `security`.

Canonical phrasing: 全 codebase 禁用 `shell=True`、`eval(`、`exec(`(grep 0
命中). 禁止字串拼接 SQL:不得出現 f-string / `%` / `+` 組成的 SQL;一律
ORM 或參數化(以 grep + code review 雙重驗證). API key 雜湊儲存,比對用
`hmac.compare_digest`. 403 回應不得洩漏資源存在性. 錯誤 body 不得含堆疊
/SQL/路徑. CORS 預設拒絕所有來源;允許清單由 `TASKQ_CORS_ORIGINS` 明示.
`bandit -r 03-development/src/`:0 HIGH、0 MEDIUM.

**Acceptance criteria**
- **AC-N2.1** `grep -rn "shell=True\|eval(\|exec(" 03-development/src/`
  yields zero matches — measurement / interpretation boundary is owned
  by the test harness per SPEC.md §4 NFR-02.
- **AC-N2.2** No string-concatenated SQL exists; no f-string / `%` /
  `+` is used to build SQL; verification is grep + code review —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-02.
- **AC-N2.3** API keys are hashed at rest and compared with
  `hmac.compare_digest` (constant-time) — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §4 NFR-02 / FR-03.
- **AC-N2.4** A 403 response body never reveals whether a target
  resource exists — measurement / interpretation boundary is owned by
  the test harness per SPEC.md §4 NFR-02 / FR-04.
- **AC-N2.5** An error body never contains a stack trace, SQL, or
  file path — measurement / interpretation boundary is owned by the
  test harness per SPEC.md §4 NFR-02 / FR-10.
- **AC-N2.6** CORS denies all origins by default; the allowlist comes
  from `TASKQ_CORS_ORIGINS` — measurement / interpretation boundary
  is owned by the test harness per SPEC.md §4 NFR-02 / §5.1.
- **AC-N2.7** `bandit -r 03-development/src/` reports 0 HIGH and 0
  MEDIUM findings — measurement / interpretation boundary is owned by
  the test harness per SPEC.md §4 NFR-02.

### NFR-03: 錯誤處理、交易與非同步正確性

> Source: SPEC.md §4 NFR-03. Dimension: `error_handling`.

Canonical phrasing: 每個請求的交易邊界明確:成功 commit、例外 rollback,以
context manager 保證. 不得出現裸 `except:`、`except Exception: pass`.
`asyncio.CancelledError` 不得被吞掉 —— 必須重新拋出. 資料庫連線失敗 →
`/readyz` 503 + 明確 detail;不得靜默重試至無限. 任務 timeout 必須確實
終止子進程,不留孤兒. migration 失敗 → 交易 rollback,資料庫維持在前一個
revision.

**Acceptance criteria**
- **AC-N3.1** Per-request transaction boundaries are explicit — commit
  on success, rollback on exception — enforced by a context manager —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-03 / FR-06.
- **AC-N3.2** No bare `except:` or `except Exception: pass` exists in
  the codebase — measurement / interpretation boundary is owned by
  the test harness per SPEC.md §4 NFR-03.
- **AC-N3.3** `asyncio.CancelledError` is re-raised (never swallowed
  by a broad `except Exception`) — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §4 NFR-03 / FR-08.
- **AC-N3.4** A database-connection failure produces `/readyz` 503
  with a clear `detail`; the service does not silently retry forever
  — measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-03 / FR-09.
- **AC-N3.5** A task timeout actually terminates the child process and
  leaves no orphan — measurement / interpretation boundary is owned
  by the test harness per SPEC.md §4 NFR-03 / FR-08.
- **AC-N3.6** A failed migration rolls back its transaction and the
  database remains at the previous revision — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-03 / FR-07.

### NFR-04: 敏感資料遮蔽

> Source: SPEC.md §4 NFR-04. Dimension: `security`.

Canonical phrasing: `stdout_tail` / `stderr_tail` / 日誌 / 錯誤 body 落盤
或送出前,匹配 `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
的行整行以 `[REDACTED]` 取代. 資料庫連線字串(含密碼)不得出現在任何日誌、
錯誤訊息或 `/v1/metrics` 回應中. API key 明文只在 `key create` 當下輸出
一次,不得寫入任何持久化位置.

**Acceptance criteria**
- **AC-N4.1** Lines matching
  `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`
  are replaced wholesale with `[REDACTED]` before being written to or
  emitted from `stdout_tail` / `stderr_tail` / logs / error bodies —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-04.
- **AC-N4.2** The database connection string (including its password)
  never appears in any log, error message, or `/v1/metrics` response —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-04.
- **AC-N4.3** API-key plaintext is printed exactly once at `key
  create` time and is not written to any persistent location —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-04 / FR-03.

### NFR-05: 文件覆蓋

> Source: SPEC.md §4 NFR-05. Dimension: `documentation`.

Canonical phrasing: 全部公開函式/類別有 docstring 且含 `[FR-XX]` 或
`[NFR-XX]` 引用,覆蓋率 100%. 每個 API 端點在 OpenAPI schema 中有
`summary` 與 `description`(FastAPI 自動產生的 `/openapi.json` 以測試斷言).

**Acceptance criteria**
- **AC-N5.1** Every public function/class has a docstring that
  contains a `[FR-XX]` or `[NFR-XX]` reference (coverage 100%) —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-05.
- **AC-N5.2** Every API endpoint appears in `/openapi.json` with both
  `summary` and `description` populated — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §4 NFR-05.

### NFR-06: 架構分層契約

> Source: SPEC.md §4 NFR-06. Dimension: `architecture_constraints`.

Canonical phrasing: 專案根目錄必須存在 `.importlinter`,宣告 layers
contract:`api > service > repository > models`. 上層可 import 下層,下層
不得 import 上層;`config` 與 `errors` 為 independence 模組. 額外禁令
(forbidden contract):`repository` 以外的任何層不得 import `sqlalchemy` ——
ORM 洩漏到業務層是本輪要防的具體反模式. `lint-imports` 必須 exit 0.
禁止以刪除 `.importlinter`、萬用字元 `ignore_imports`、或降級 contract 的
方式取得通過.

**Acceptance criteria**
- **AC-N6.1** A `.importlinter` file exists at the project root and
  declares the layers contract `api > service > repository > models`
  with `config` and `errors` marked as independence modules —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-06.
- **AC-N6.2** The contract includes a forbidden contract banning
  `sqlalchemy` imports outside `repository/` — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-06.
- **AC-N6.3** `lint-imports` exits 0 — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §4 NFR-06.
- **AC-N6.4** The contract is not weakened by removing `.importlinter`,
  by adding `ignore_imports` wildcards, or by downgrading the
  contract itself — measurement / interpretation boundary is owned
  by the test harness per SPEC.md §4 NFR-06.

### NFR-07: 依賴與授權合規

> Source: SPEC.md §4 NFR-07. Dimension: `license_compliance`.

Canonical phrasing: 全部 runtime 依賴在 `requirements.txt` 以 `==` 釘版;
transitive 依賴以 lock 檔(`requirements.lock`)完整鎖定. 允許的 license:
MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF;出現其他 → 該依賴
不得使用. 掃描範圍必須包含完整依賴樹(直接 + transitive),證據命令:
`pip-licenses --format=json --with-system`. 產出 SBOM 於
`08-config/SBOM.json`,含每個依賴的 `name` / `version` / `license` /
`direct|transitive`.

**Acceptance criteria**
- **AC-N7.1** Every runtime dependency in `requirements.txt` is pinned
  with `==`; transitive dependencies are fully pinned via
  `requirements.lock` — measurement / interpretation boundary is
  owned by the test harness per SPEC.md §4 NFR-07.
- **AC-N7.2** Every dependency (direct + transitive) carries a license
  in {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF}; any other
  license forbids use of that dependency — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-07.
- **AC-N7.3** `pip-licenses --format=json --with-system` is run and
  the scan covers the full dependency tree — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-07.
- **AC-N7.4** An SBOM is produced at `08-config/SBOM.json` with every
  dependency carrying `name` / `version` / `license` / `direct or
  transitive` — measurement / interpretation boundary is owned by
  the test harness per SPEC.md §4 NFR-07.

### NFR-08: 變異測試

> Source: SPEC.md §4 NFR-08. Dimension: `mutation_testing`.

Canonical phrasing: `.methodology/harness_config.json` 設
`features.mutation_testing: true`. mutation score ≥ 70. 範圍限定於
`service/` 與 `repository/` 兩層,並在 `harness_config.json` 註記限定理由
(執行時間預算).

**Acceptance criteria**
- **AC-N8.1** `.methodology/harness_config.json` contains
  `features.mutation_testing: true` — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §4 NFR-08.
- **AC-N8.2** `mutmut run` followed by score extraction yields a
  mutation score ≥ 70 — measurement / interpretation boundary is
  owned by the test harness per SPEC.md §4 NFR-08.
- **AC-N8.3** The mutation scope is limited to the `service/` and
  `repository/` layers, with the scope-restriction rationale recorded
  in `harness_config.json` (runtime budget) — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-08.

### NFR-09: 驗證真實性(零 skip 鐵律)

> Source: SPEC.md §4 NFR-09. Dimension: `test_assertion_quality`.

Canonical phrasing: 任何 FR / NFR 的驗證測試不得是 `pytest.skip` / `skipif`
/ `xfail` / 無斷言的 stub. `pytest 03-development/tests -q` 的 skipped 計
數必須為 0. 每個測試函式至少一個 `assert`(`zero_assert == 0`). 反造假
條款:不得以 `--ignore` / `-k` / `--deselect` / `collect_ignore` / 從
`testpaths` 移除目錄的方式排除測試. 本輪特別條款:`FR-07` 的三步
migration 必須以真實資料庫測試(SQLite 檔案,非 in-memory mock),往返可
逆性以實際資料比對驗證. 不得以「migration �輯太難測」為由降級為 skip.
`TRACEABILITY_MATRIX.md` 的 `VERIFIED` 只能在測試實際執行並通過時給出.

**Acceptance criteria**
- **AC-N9.1** `pytest 03-development/tests -q` reports `skipped == 0`
  — measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-09.
- **AC-N9.2** Every test function has at least one `assert`
  (`zero_assert == 0`) — measurement / interpretation boundary is
  owned by the test harness per SPEC.md §4 NFR-09.
- **AC-N9.3** Tests are not excluded via `--ignore`, `-k`,
  `--deselect`, `collect_ignore`, or removal from `testpaths` —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-09.
- **AC-N9.4** The FR-07 three-step migration is tested against a real
  SQLite database file (not an in-memory mock) with round-trip
  reversibility verified via actual data comparison; the test may not
  be downgraded to a skip on the grounds that "migration logic is hard
  to test" — measurement / interpretation boundary is owned by the
  test harness per SPEC.md §4 NFR-09.
- **AC-N9.5** `TRACEABILITY_MATRIX.md` `VERIFIED` cells are set only
  after the cited test has actually run and passed — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-09.

### NFR-10: 整合覆蓋

> Source: SPEC.md §4 NFR-10. Dimension: `integration_coverage`.

Canonical phrasing: `03-development/tests/integration/` 行覆蓋 ≥ 80%.
整合測試以 `httpx.AsyncClient(transport=ASGITransport(app))` 驅動,不得
直接呼叫 handler 函式. 至少涵蓋:CRUD 全鏈、401/403/404/409/422/429/503
每個錯誤碼各一例、migration 往返、rate limit 觸發與恢復、graceful drain.

**Acceptance criteria**
- **AC-N10.1** Line coverage of `03-development/src/` measured while
  running only the integration suite (`03-development/tests/integration/`)
  is ≥ 80% — measurement / interpretation boundary is owned by the
  test harness per SPEC.md §4 NFR-10.
- **AC-N10.2** Integration tests are driven through
  `httpx.AsyncClient(transport=ASGITransport(app))` and do not call
  handler functions directly — measurement / interpretation boundary
  is owned by the test harness per SPEC.md §4 NFR-10.
- **AC-N10.3** Integration tests cover the full CRUD chain and at
  least one example of each of HTTP 401, 403, 404, 409, 422, 429, 503,
  plus the migration round-trip, rate-limit trigger and recovery, and
  graceful drain — measurement / interpretation boundary is owned by
  the test harness per SPEC.md §4 NFR-10.

### NFR-11: 可讀性

> Source: SPEC.md §4 NFR-11. Dimension: `readability`.

Canonical phrasing: 專案 MI(LLOC 加權)≥ 80;單一函式 CC ≤ 10. 單一檔案
≤ 400 行;單一目錄 ≤ 15 檔. 每個 API handler ≤ 40 行(業務邏輯必須下沉到
`service/`).

**Acceptance criteria**
- **AC-N11.1** Project MI (LLOC-weighted) is ≥ 80 — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-11.
- **AC-N11.2** Single-function CC ≤ 10 — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §4 NFR-11.
- **AC-N11.3** Single file ≤ 400 lines; single directory ≤ 15 files —
  measurement / interpretation boundary is owned by the test harness
  per SPEC.md §4 NFR-11.
- **AC-N11.4** Each API handler ≤ 40 lines; business logic must sink
  into `service/` — measurement / interpretation boundary is owned
  by the test harness per SPEC.md §4 NFR-11.

### NFR-12: 系統驗證目標

> Source: SPEC.md §4 NFR-12. Dimension: `execute_verification_target`.

Canonical phrasing: `Makefile` 的 `verify-system` target 必須串接:
1. `alembic upgrade head`; 2. 全套測試; 3. 服務啟動 + `/healthz`、
`/readyz` 冒煙; 4. `alembic downgrade base` 後再 `upgrade head`(往返驗證).
`make verify-system` 必須 exit 0 並在 stdout 印出 `verify-system: PASS`.

**Acceptance criteria**
- **AC-N12.1** `Makefile` declares a `verify-system` target chaining
  `alembic upgrade head`, the full test suite, service start +
  `/healthz`/`/readyz` smoke, and `alembic downgrade base` followed by
  `upgrade head` for round-trip validation — measurement /
  interpretation boundary is owned by the test harness per SPEC.md §4
  NFR-12.
- **AC-N12.2** `make verify-system` exits 0 and prints
  `verify-system: PASS` on stdout — measurement / interpretation
  boundary is owned by the test harness per SPEC.md §4 NFR-12.

---

## 5. Acceptance Criteria Summary

The 27 acceptance items from SPEC.md §8 (each a single machine-decidable
command with an expected output) are summarised here. The AC identifiers
above map 1:1 to the SPEC.md §8 list and to the FR/NFR they belong to.

| # | SPEC.md §8 command (canonical) | Expected | FR / NFR | AC id(s) |
|---|--------------------------------|----------|----------|----------|
| 1 | `pytest 03-development/tests -q` | 全綠,**skipped 計數為 0** | NFR-09 | AC-N9.1 |
| 2 | `pytest 03-development/tests --cov=03-development/src --cov-report=term` | TOTAL **100%** | C-03/C-04/NFR-09 | AC-N9.1 / AC-N9.2 |
| 3 | `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` | TOTAL **≥ 80%** | NFR-10 | AC-N10.1 |
| 4 | `POST /v1/tasks`(有效 write key) | 201 + task id | FR-01 | AC-1.1 |
| 5 | `POST /v1/tasks`(無 `X-API-Key`) | **401** + problem+json | FR-03 | AC-3.1 |
| 6 | `DELETE /v1/tasks/{id}`(write key,非 admin) | **403**,body 不透露該 id 是否存在 | FR-04 / NFR-02 | AC-4.2 / AC-N2.4 |
| 7 | `GET /v1/tasks/{unknown}` | **404** + problem+json | FR-01 | AC-1.3 |
| 8 | `POST /v1/tasks` 重複 name | **409** | FR-01 / FR-10 | AC-1.1 / AC-10.5 |
| 9 | 連續請求超過 `TASKQ_RATE_BURST` | **429** + `Retry-After` header | FR-05 | AC-5.2 |
| 10 | 停掉 DB 後 `GET /readyz` | **503**,detail 指明 DB 不可用 | FR-09 / NFR-03 | AC-9.2 / AC-N3.4 |
| 11 | `alembic downgrade -1` 後 `GET /readyz` | **503**,detail 指明 migration 未到 head | FR-09 | AC-9.2 / AC-9.3 |
| 12 | `alembic upgrade head` → 寫樣本 → `downgrade -1` → `upgrade head` | 樣本資料逐欄相同(**v3 資料搬遷可逆**) | FR-07 / NFR-09 | AC-7.3 / AC-N9.4 |
| 13 | `alembic downgrade base` | exit 0,無殘留表 | FR-07 | AC-7.2 |
| 14 | `GET /v1/tasks?limit=50`(10,000 筆)的 SQL 陳述計數 | **常數**(與筆數無關) | FR-06 / NFR-01 | AC-6.4 / AC-N1.3 |
| 15 | `GET /v1/tasks/{id}` p95(10,000 筆) | **< 30ms** | NFR-01 | AC-N1.1 |
| 16 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | **0 命中** | NFR-02 / FR-02 | AC-N2.1 / AC-2.2 |
| 17 | 掃描 SQL 字串拼接(f-string / `%` / `+` 組 SQL) | **0 命中** | NFR-02 | AC-N2.2 |
| 18 | 查 `api_keys` 表 | 無明文金鑰;`key_hash` 為 64 hex | FR-03 / NFR-02 | AC-3.2 |
| 19 | 觸發 500 後檢查回應 body | 不含堆疊 / SQL / 檔案路徑 | FR-10 / NFR-02 | AC-10.3 / AC-N2.5 |
| 20 | 日誌與 `/v1/metrics` 全文 | 不含 `TASKQ_DB_URL` 的密碼片段 | NFR-04 | AC-N4.2 |
| 21 | `lint-imports` | **exit 0**,且 `service`/`api` 層 import `sqlalchemy` 會被擋 | NFR-06 | AC-N6.3 / AC-N6.2 |
| 22 | `pip-licenses --format=json --with-system` | 每個依賴 license ∈ allowlist | NFR-07 | AC-N7.2 |
| 23 | `bandit -r 03-development/src/` | 0 HIGH,0 MEDIUM | NFR-02 | AC-N2.7 |
| 24 | `mutmut run` 後 `mutmut results` | mutation score **≥ 70** | NFR-08 | AC-N8.2 |
| 25 | 服務關閉時有進行中的任務 | graceful drain;逾時者標記 `interrupted`,無孤兒進程 | FR-08 / NFR-03 | AC-8.2 / AC-8.4 / AC-N3.5 |
| 26 | `grep -c "^TASKQ_" .env.example` | **12** | C-16 | C-16 |
| 27 | `make verify-system` | exit 0 且 stdout 含 `verify-system: PASS` | NFR-12 | AC-N12.2 |

> Note on ambiguous canonical phrasing: SPEC.md §8 #12 (`upgrade head` →
> write sample → `downgrade -1` → `upgrade head`) and SPEC.md §8 #15
> (`p95 < 30ms`) are transcribed verbatim into AC-7.3 and AC-N1.1. Per
> the canonical-interpretation rule (R-CANONICAL-INTERP-001), no
> prescriptive clause (e.g. a specific wall-clock budget for the test
> harness itself) is added by Agent A — measurement / interpretation
> boundary is owned by the test harness per the same canonical line.

---

## 6. Out-of-Scope

- **Multi-tenant isolation beyond per-API-key scope.** No row-level
  security, no tenant_id on tasks (canonical: SPEC.md §3 / §4 — only
  per-token scope is declared).
- **Distributed rate-limiter state sharing.** Token-bucket state lives in
  the database with a row-level lock; no Redis / external cache is
  declared (canonical: SPEC.md §3 FR-05).
- **Web UI / dashboard.** SPEC.md declares only REST + `/v1/metrics`
  JSON; no HTML / SPA frontend is in scope.
- **Cron / scheduled tasks.** Tasks are submitted on demand via
  `POST /v1/tasks/{id}/run`; no scheduled execution is declared
  (canonical: SPEC.md §3 FR-02).
- **Webhook / push callbacks on task completion.** Polling
  `GET /v1/tasks/{id}/runs` is the declared history path (canonical:
  SPEC.md §3 FR-02).
- **Production deployment automation.** SPEC.md §5.3 declares
  `Makefile` for `verify-system` but no Kubernetes manifests / Helm
  charts / Terraform.
- **Observability exporters beyond `/v1/metrics`.** No Prometheus
  scrape contract, OpenTelemetry tracing, or log shipping is declared
  (canonical: SPEC.md §3 FR-09 declares only the in-process endpoint).
- **Authentication methods other than API key.** No OAuth2, JWT, OIDC,
  mTLS, or session cookies are declared (canonical: SPEC.md §3 FR-03).

---

## 7. Open Issues

| ID | Item | Disposition |
|----|------|-------------|
| NFR-99-01 | **SPEC.md §4 NFR-11 ambiguity** — "業務邏輯必須下沉到 `service/`" leaves the boundary between `service/` and `api/deps.py` (the single authn/authz decision point per FR-04 / §6) implicit. | Resolve: confirm with stakeholder whether `api/deps.py` counts as "business logic" for the ≤40-line handler rule (NFR-11) or as a pure dependency adapter. Test harness to confirm via a counter-example handler. |
| NFR-99-02 | **SPEC.md §3 FR-07 v3 column-mapping ambiguity** — "搬遷既有資料後移除原欄位" does not pin the column-by-column mapping between `tasks.result_json` and `task_results.{exit_code,stdout_tail,stderr_tail,duration_ms,finished_at}` (the source `result_json` is JSON; the target is a relational row). | Resolve: confirm the canonical mapping for `result_json` keys → `task_results` columns. Test harness to confirm via a hand-crafted `result_json` sample. |
| NFR-99-03 | **SPEC.md §3 FR-08 graceful-drain ambiguity** — "逾時則標記 `interrupted`" does not specify whether the row is committed (final state visible to readers) or rolled back. | Resolve: confirm whether drained-but-interrupted tasks leave a committed `task_results` row with `exit_code` sentinel for `interrupted`. |
| NFR-99-04 | **SPEC.md §5.3 .env.example count** — `grep -c "^TASKQ_" .env.example` expects 12; SPEC.md §5.1 lists 12 variables but `TASKQ_HOST`/`TASKQ_PORT` are listed without an explicit default; canonical §8 #26 still asserts 12. | Resolve: confirm that `.env.example` declares 12 `TASKQ_*` keys (some with no default). |
| FR-XX-deferred-01 | If a future round extends the SPEC, new `### FR-NN` headings must be transcribed here and into the FR Block JSON (R-SRS-FR-BLOCK-001). | Defer to the next spec revision. |

---

## 8. Risks

> Source: SPEC.md §9. Each risk is transcribed verbatim (Risk / Impact /
> Mitigation) with FR / NFR mapping.

| ID | Risk | Impact | Likelihood | Mitigation | Maps to |
|----|------|--------|------------|------------|---------|
| R1 | **v3 資料搬遷遺失資料** | 高 | 中 | 往返可逆性測試以真實 DB 逐欄比對 | FR-07 / §8 #12 |
| R2 | SQL injection | 高 | 低 | 禁字串拼接 + ORM/參數化 + grep gate | NFR-02 |
| R3 | API key 洩漏 | 高 | 中 | 雜湊儲存 + 常數時間比對 + 明文只印一次 | FR-03 |
| R4 | 403 洩漏資源存在性 | 中 | 中 | 授權判定在資源查詢之前 | FR-04 / §8 #6 |
| R5 | N+1 查詢在大表上崩潰 | 高 | 高 | 顯式預載 + SQL 計數斷言 | NFR-01 / §8 #14 |
| R6 | 錯誤 body 洩漏內部結構 | 中 | 高 | RFC 7807 固定欄位 + detail 白名單 | FR-10 |
| R7 | **`CancelledError` 被吞 → 關閉時卡死** | 中 | 中 | 明文禁令 + 測試斷言 | NFR-03 |
| R8 | 任務 timeout 留下孤兒進程 | 中 | 中 | `kill()` + `await wait()` | FR-08 / §8 #25 |
| R9 | 部署後忘記跑 migration | 高 | 中 | `/readyz` fail closed | FR-09 / §8 #11 |
| R10 | 連線池耗盡 | 中 | 中 | `pool_pre_ping` + 併發上限 | FR-06 / FR-08 |
| R11 | transitive 依賴引入不相容 license | 中 | 中 | lock 檔 + 全樹掃描 | NFR-07 |
| R12 | rate bucket 競態導致超放行 | 低 | 中 | 單一交易 + row-level lock | FR-05 |

---

## 9. Glossary

| Term | Definition |
|------|------------|
| ASGI | Asynchronous Server Gateway Interface. The transport contract used by FastAPI / uvicorn. |
| Alembic | SQLAlchemy's database schema migration tool. Used here for v1 / v2 / v3 revisions (FR-07). |
| API key | Opaque bearer credential sent in the `X-API-Key` header; stored as a SHA-256 hash (FR-03 / NFR-02). |
| CC | Cyclomatic complexity — number of linearly independent paths through a function. Bound by NFR-11 to ≤ 10. |
| CORS | Cross-Origin Resource Sharing. Default-deny in this service (NFR-02). |
| CRG | `code-review-graph` — the framework's structural knowledge graph used by the `architecture` dimension. |
| cursor pagination | Pagination by opaque cursor token, not by offset. Mandated by FR-01; offset pagination on large tables is an N+1 cousin. |
| dependency (FastAPI) | A single function that yields a request-scoped value; here, the single authn/authz decision point per FR-04. |
| dimension | A quality dimension registered in `harness/harness/ssi/prompts/evaluate_dimension.md`; each NFR maps to exactly one. |
| `hmac.compare_digest` | Constant-time comparison from Python's stdlib `hmac`; used for API-key comparison (NFR-02). |
| layer | One of `api` / `service` / `repository` / `models` in the `api > service > repository > models` contract (NFR-06). |
| MI | Maintainability Index — radon-style LLOC-weighted readability score; bound by NFR-11 to ≥ 80. |
| mutation score | Killed mutants / total mutants × 100; bound by NFR-08 to ≥ 70. |
| N+1 | An anti-pattern where a query emits one statement per row in the result set; bound by NFR-01 to never occur on the list endpoint. |
| ORM | Object-Relational Mapper — here, SQLAlchemy 2.x declarative; importable only by `repository/` (NFR-06). |
| problem+json | RFC 7807 `application/problem+json` error envelope mandated by FR-10. |
| rate bucket | Per-token token-bucket state held in the database; capacity `TASKQ_RATE_BURST`, refill `TASKQ_RATE_PER_SEC` (FR-05). |
| scope | Hierarchical API-key permission — `read` < `write` < `admin` (FR-04). |
| `selectinload` / `joinedload` | SQLAlchemy eager-loading strategies mandated by FR-06 / NFR-01. |
| SBOM | Software Bill of Materials — at `08-config/SBOM.json`, per NFR-07. |
| TASKQ_* | The 12 environment variables declared in SPEC.md §5.1, read by `config.py`. |
| `TaskGroup` | `asyncio.TaskGroup` — context manager that awaits a group of tasks (FR-08). |
| `verify-system` | The Makefile target mandated by NFR-12, chaining migration + tests + smoke + migration round-trip. |

---

## 10. FR Block (machine-readable)

<!-- FR:START -->
```json
{
  "version": "1.0",
  "created_at": "2026-08-24",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "Task resource CRUD API: POST / GET / LIST / DELETE on /v1/tasks, cursor-based pagination, 422/404/409 problem+json",
      "implementation_functions": ["taskq_api.api.tasks.create_task", "taskq_api.api.tasks.get_task", "taskq_api.api.tasks.list_tasks", "taskq_api.api.tasks.delete_task", "taskq_api.service.tasks.create_task", "taskq_api.service.tasks.get_task", "taskq_api.service.tasks.list_tasks", "taskq_api.service.tasks.delete_task", "taskq_api.repository.task_repo.*"],
      "verification_method": "integration test via httpx.ASGITransport; assert 201/200/200/204 on success, 422 on validation failure, 404 on unknown id, 409 on duplicate name; assert cursor pagination; assert limit upper bound 200"
    },
    {
      "id": "FR-02",
      "description": "Task execution endpoint: POST /v1/tasks/{id}/run returns 202 + run_id; executes via asyncio.create_subprocess_exec(*shlex.split(command)) with TASKQ_TASK_TIMEOUT; writes task_results row; GET /v1/tasks/{id}/runs returns history newest-first",
      "implementation_functions": ["taskq_api.api.tasks.run_task", "taskq_api.api.tasks.list_runs", "taskq_api.service.runner.run", "taskq_api.service.tasks.schedule_run", "taskq_api.repository.task_repo.record_result", "taskq_api.repository.task_repo.list_runs"],
      "verification_method": "integration test exercises 202, asserts task_results row, asserts timeout kills child process, asserts history ordering"
    },
    {
      "id": "FR-03",
      "description": "API-key authentication: X-API-Key header, SHA-256 hash at rest, hmac.compare_digest constant-time compare, 401 on missing/invalid, plaintext printed once at key create, revoked keys invalid, /healthz and /readyz exempt",
      "implementation_functions": ["taskq_api.api.deps.authenticate", "taskq_api.service.auth.verify_key", "taskq_api.repository.key_repo.lookup_by_hash", "taskq_api.repository.key_repo.create", "taskq_api.repository.key_repo.revoke"],
      "verification_method": "integration test asserts 401 on missing/invalid; unit test asserts hmac.compare_digest usage and no plaintext in api_keys table"
    },
    {
      "id": "FR-04",
      "description": "Scope authorization: read < write < admin hierarchical; insufficient scope returns 403 problem+json without leaking resource existence; single FastAPI dependency is the only authn/authz decision point",
      "implementation_functions": ["taskq_api.api.deps.require_scope", "taskq_api.service.auth.scope_satisfies", "taskq_api.api.deps.authenticate"],
      "verification_method": "integration test asserts 403 with no existence leak; architecture test asserts every /v1 route traverses the same dependency"
    },
    {
      "id": "FR-05",
      "description": "Rate limiting: per-token token bucket in DB with capacity TASKQ_RATE_BURST and refill TASKQ_RATE_PER_SEC; 429 + Retry-After on overflow; row-level lock in single transaction; /healthz and /readyz exempt",
      "implementation_functions": ["taskq_api.api.deps.rate_limit", "taskq_api.service.ratelimit.consume", "taskq_api.repository.rate_repo.get_bucket", "taskq_api.repository.rate_repo.update_bucket"],
      "verification_method": "integration test bursts > TASKQ_RATE_BURST and asserts 429 + Retry-After; unit test asserts row-level lock + single transaction"
    },
    {
      "id": "FR-06",
      "description": "Persistence layer and transaction boundaries: repository/ is the only layer importing sqlalchemy; one Session per request with explicit commit/rollback via context manager; no string-concatenated SQL; explicit eager loading (selectinload/joinedload); pool_size=TASKQ_DB_POOL_SIZE with pool_pre_ping=True",
      "implementation_functions": ["taskq_api.repository.session.session_scope", "taskq_api.repository.task_repo.*", "taskq_api.repository.key_repo.*", "taskq_api.repository.rate_repo.*"],
      "verification_method": "architecture test asserts sqlalchemy not importable outside repository/; integration test asserts N+1 via SQLAlchemy event listener; lint-imports exits 0"
    },
    {
      "id": "FR-07",
      "description": "Schema migration: Alembic v1 (tasks, api_keys) -> v2 (tags, task_tags, tasks.name unique index) -> v3 (split tasks.result_json into task_results with data move, reversible downgrade); upgrade head / downgrade base succeed; round-trip data integrity verified column-by-column against real SQLite file; no destructive shortcuts",
      "implementation_functions": ["migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results", "alembic.env"],
      "verification_method": "integration test runs upgrade head -> write sample -> downgrade -1 -> upgrade head and asserts byte-identical columns; downgrade base leaves no residual tables; offline SQL asserts migration files"
    },
    {
      "id": "FR-08",
      "description": "Async runner: asyncio.TaskGroup manages background execution; graceful drain on shutdown waits up to TASKQ_DRAIN_TIMEOUT (tasks exceeding budget marked interrupted); concurrency cap TASKQ_MAX_CONCURRENT; asyncio.wait_for timeout kills child process via process.kill() + await process.wait(); asyncio.CancelledError must propagate",
      "implementation_functions": ["taskq_api.service.runner.Runner", "taskq_api.service.runner.execute", "taskq_api.service.runner.drain"],
      "verification_method": "integration test asserts graceful drain, orphan-process absence after timeout, CancelledError propagation, and concurrency cap"
    },
    {
      "id": "FR-09",
      "description": "Health and observability: /healthz returns 200 {status:ok} when alive; /readyz returns 200 only when DB reachable AND alembic current == head (else 503 with body naming the failed condition); /v1/metrics requires admin and reports task counts by status, execution-latency percentiles, rate-limit rejection counts",
      "implementation_functions": ["taskq_api.api.health.healthz", "taskq_api.api.health.readyz", "taskq_api.api.health.metrics"],
      "verification_method": "integration test asserts /healthz always 200 while alive; /readyz 503 with detail when DB down; /readyz 503 with detail when alembic behind head; /v1/metrics 200 only with admin scope"
    },
    {
      "id": "FR-10",
      "description": "Error contract (RFC 7807): every non-2xx response uses application/problem+json with fields type/title/status/detail/instance/correlation_id; detail never carries SQL/stack/file-path/schema; correlation_id echoed in X-Correlation-Id header and log; status mapping per SPEC.md §7",
      "implementation_functions": ["taskq_api.errors.problem", "taskq_api.errors.handlers.*", "taskq_api.api.deps.correlation_id"],
      "verification_method": "integration test triggers every error code and asserts Content-Type, field set, and absence of internals in detail; correlation_id echoed in header and log"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "GET /v1/tasks/{id} p95 < 30ms and GET /v1/tasks?limit=50 p95 < 80ms at 10k rows; list endpoint SQL statement count is constant (no N+1); measured via pytest-benchmark / ASGI transport",
      "test_method": "pytest-benchmark suite over 10k-row fixture; SQLAlchemy event listener counts statements on the list endpoint"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "No shell=True/eval(/exec( in codebase (grep 0 hits); no string-concatenated SQL; API keys hashed with hmac.compare_digest; 403 leaks no resource existence; error body carries no stack/SQL/path; CORS deny-by-default; bandit 0 HIGH / 0 MEDIUM",
      "test_method": "grep gates (shell/eval/exec; SQL concatenation), bandit CI gate, integration tests for 403 leak and error-body redaction, CORS unit test"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "Explicit per-request transaction boundaries via context manager; no bare except: / except Exception: pass; asyncio.CancelledError always re-raised; DB-connection failure => /readyz 503 with detail; task timeout kills child process; migration failure rolls back transaction",
      "test_method": "ast-error-handling scanner; integration tests for CancelledError, /readyz 503, task timeout orphan absence, migration rollback"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "Lines matching (sk-[A-Za-z0-9_-]{8,}|token=\\S+|Bearer\\s+\\S+|postgres(ql)?://[^\\s]+) replaced with [REDACTED] before stdout_tail/stderr_tail/log/error-body emission; DB connection string (with password) absent from logs, errors, /v1/metrics; API-key plaintext printed once and not persisted",
      "test_method": "unit tests feed sample secrets into each output channel and assert [REDACTED] substitution; log/metric scan asserts no DB-URL password"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "100% of public functions/classes have docstrings containing [FR-XX] or [NFR-XX] references; every API endpoint has summary + description in /openapi.json",
      "test_method": "ast-docstrings scanner; OpenAPI JSON assertion test"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": ".importlinter declares layers contract api > service > repository > models with config/errors independence and a forbidden contract banning sqlalchemy imports outside repository/; lint-imports exits 0; no contract weakening",
      "test_method": "lint-imports CI gate (exit 0); architecture test attempts sqlalchemy import from service/ and api/ and asserts ImportError"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "Runtime deps pinned with == in requirements.txt; transitives pinned via requirements.lock; license allowlist {MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, PSF}; full-tree scan via pip-licenses --with-system; SBOM at 08-config/SBOM.json with name/version/license/direct-or-transitive per dep",
      "test_method": "pip-licenses --format=json --with-system assert; SBOM JSON schema validation; CI gate for non-allowlist license"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "features.mutation_testing: true in harness_config.json; mutmut score >= 70 over service/ + repository/ with scope-rationale recorded",
      "test_method": "framework mutation-test-score command reads .methodology/mutation_score.json and asserts score >= 70"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "pytest skipped count == 0; zero_assert == 0; no exclusions via --ignore/-k/--deselect/collect_ignore/testpaths removal; FR-07 migration tested against real SQLite file with column-by-column round-trip; TRACEABILITY_MATRIX.md VERIFIED only after actual test pass",
      "test_method": "pytest -q output scan; ast-assertions scanner; integration test for migration round-trip on real SQLite file; traceability verifier"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "Integration suite (03-development/tests/integration/) line-coverage of 03-development/src/ >= 80%; integration tests driven via httpx.AsyncClient(transport=ASGITransport(app)), not direct handler calls; covers full CRUD chain plus 401/403/404/409/422/429/503 each at least once, plus migration round-trip, rate-limit trigger + recovery, graceful drain",
      "test_method": "pytest --cov=03-development/src with --cov-report=term; coverage threshold gate >= 80%; per-error-code integration test enumeration"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "Project MI (LLOC-weighted) >= 80; single-function CC <= 10; single file <= 400 lines; single directory <= 15 files; each API handler <= 40 lines (business logic sinks into service/)",
      "test_method": "radon-mi scanner; radon-cc scanner; file/dir line-count + file-count scans; handler LOC scan"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "description": "Makefile verify-system target chains alembic upgrade head -> full test suite -> service start + /healthz + /readyz smoke -> alembic downgrade base then upgrade head; make verify-system exits 0 and prints verify-system: PASS",
      "test_method": "make verify-system invocation and exit-code + stdout scan"
    }
  ]
}
```
<!-- FR:END -->

> The `type:` vocabulary on each NFR is drawn from
> `harness/core/quality_gate/sab_parser.ALL_NFR_TYPES`:
> `documentation|integration|layering|licensing|maintainability|mutation|performance|reliability|security|testability|verifiability|deployability|scalability|usability`.
>
> Mapping each canonical `dimension` (from SPEC.md §4) to the vocabulary
> above: `performance -> performance`, `security -> security`,
> `error_handling -> reliability`, `documentation -> documentation`,
> `architecture_constraints -> layering`, `license_compliance -> licensing`,
> `mutation_testing -> mutation`, `test_assertion_quality -> testability`,
> `integration_coverage -> integration`, `readability -> maintainability`,
> `execute_verification_target -> verifiability`.
