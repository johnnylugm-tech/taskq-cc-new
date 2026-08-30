# CONFIG_RECORDS.md - taskq-cc-new

> On-demand Lazy Load template.

## 1. Version Information
- Version: vharness-v4-20260826-score94-18-gb65aa12
- Git Commit: b65aa12
- Release Date: 2026-08-30

## 2. Runtime Configuration
| Environment | Config |
|-------------|--------|
| Development | SQLite at `sqlite:///$(mktemp -d)/taskq_app.db`; `uvicorn taskq_api.app:app --reload --port 8000` (Python 3.11, FastAPI 0.115.0, SQLAlchemy 2.0.36). Pool size `TASKQ_DB_POOL_SIZE=5`; rate limit `TASKQ_RATE_BURST=20` / `TASKQ_RATE_PER_SEC=5.0`. |
| Production | SQLite at path resolved from `TASKQ_DB_URL` (default `sqlite:///$TMPDIR/taskq_app.db`); `uvicorn taskq_api.app:app --host 0.0.0.0 --port 8000 --workers 1`. Pool size `TASKQ_DB_POOL_SIZE=10`; rate limit `TASKQ_RATE_BURST=40` / `TASKQ_RATE_PER_SEC=10.0`; per-task timeout `TASKQ_TASK_TIMEOUT=30`; scheduler drain `TASKQ_DRAIN_TIMEOUT=30`; concurrency cap `TASKQ_MAX_CONCURRENT=8`. Alembic chain head = `v3_split_results`. |

## 3. Dependency List
Top-level runtime dependencies (full inventory in `08-config/SBOM.json`, CycloneDX 1.5):
```
fastapi==0.115.0        # web framework
uvicorn==0.30.6         # ASGI server
pydantic==2.9.2         # data model validation
sqlalchemy==2.0.36      # ORM (repository layer only — NFR-06)
alembic==1.13.3         # migration tool (FR-07)
httpx==0.27.2           # test client
starlette==0.38.6       # FastAPI transport
anyio==4.6.0            # async concurrency
click==8.1.7            # CLI entrypoint
pytest==8.3.3           # test runner
pytest-benchmark==4.0.0 # performance signal
import-linter==2.3      # architecture_constraints lint gate
mutmut==2.4.4           # mutation testing (NFR-08)
bandit==1.7.10          # security scan
ruff==0.6.8             # lint/format
Mako==1.3.5             # alembic templating
```
Pinned lock: `requirements.lock`. Tool versions: ruff 0.6.8, mypy latest, bandit 1.7.10.

## 4. Environment Variables
| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TASKQ_DB_URL` | url | `sqlite:///$TMPDIR/taskq_app.db` | SQLAlchemy engine URL. Read at import time by `taskq_api.repository.session.DB_URL` and by `migrations/env.py` so Alembic and the app share the same DB. |
| `TASKQ_DB_POOL_SIZE` | int | `5` | SQLAlchemy `QueuePool` size for the canonical engine. Production recommendation: `10`. |
| `TASKQ_MAX_CONCURRENT` | int | module default (`_DEFAULT_MAX_CONCURRENT` in `service/runner_scheduler.py`) | Hard cap on concurrent task executions. Below this the scheduler accepts new work; above it queues. |
| `TASKQ_DRAIN_TIMEOUT` | float | `30` (seconds) | Maximum time `Scheduler.drain()` waits for in-flight tasks before reporting `interrupted` for the rest. |
| `TASKQ_TASK_TIMEOUT` | float | `30` (seconds) | Per-task execution timeout used by `Runner.run` when no `timeout_seconds` kwarg is supplied. |
| `TASKQ_RATE_BURST` | int | `20` | Token-bucket capacity for the FR-05 rate limiter. Resolved at module import (`taskq_api.api.deps.RATE_BURST`). |
| `TASKQ_RATE_PER_SEC` | float | `5.0` | Token-bucket refill rate (tokens/second). Resolved at module import (`taskq_api.api.deps.RATE_PER_SEC`). |

API keys (`X-API-Key`) are NOT environment variables — they are persisted in the `keys` table (SHA-256 hashed, scope column) and seeded via `taskq_api.repository.key_repo`. See `04-testing/TEST_PLAN.md` and FR-03 for the auth contract.

## 5. Deployment Log
| Date | Version | Method | Executor |
|------|---------|--------|----------|
| 2026-08-30 | harness-v4-20260826-score94-18-gb65aa12 | `git checkout gate4-20260826-score94` + `pip install -r requirements.lock` + `alembic upgrade head` + `uvicorn taskq_api.app:app --host 0.0.0.0 --port 8000 --workers 1` | `harness-cli advance-phase` (CI: GitHub Actions workflow, tag `gate4-20260826-score94`) |

## 6. Configuration Change Log
| Phase | Change | Rationale |
|-------|--------|----------|
| Phase 1 | Defined `TASKQ_*` env-var surface (DB_URL, DB_POOL_SIZE, TASK_TIMEOUT, MAX_CONCURRENT, DRAIN_TIMEOUT) | Locked runtime knobs at import-time so per-FR tests can override via monkeypatch without code changes |
| Phase 3 | Added `TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC` (FR-05 token-bucket) | Required NFR-01 throttle; defaults 20/5.0 chosen to match SPEC §3 FR-05 |
| Phase 6 | Bumped `TASKQ_DB_POOL_SIZE` recommendation from `5` → `10` in production runbook | Gate 4 load test (NFR-01 performance) observed pool saturation above 5 concurrent workers |
| Phase 8 | Documented and pinned every `TASKQ_*` knob in this CONFIG_RECORDS file | Round 55 `cross_artifact.check_unfilled_placeholders` makes unfilled template placeholders a CRITICAL that fails Phase Truth |

## 7. Rollback SOP
**Trigger Condition**: Gate 4 score regresses below 85/100 OR a `P0` defect is confirmed in production (auth bypass, N+1 SQL regression, Alembic chain broken on upgrade head, rate-limit bypass). Severity tag `rollback-now` is set by the on-call (see `## Human Context` below).

**Commands**:
```bash
# 1. Stop the running service
systemctl stop taskq-api        # or: kill $(pgrep -f 'uvicorn taskq_api.app')

# 2. Roll back to the last green tag
cd /opt/taskq-cc-new
git fetch --tags
git checkout gate4-20260826-score94      # last Gate-4 PASS tag

# 3. Re-install pinned dependencies
.venv/bin/pip install -r requirements.lock --force-reinstall --no-deps

# 4. Roll the DB back (Alembic) — note: this is destructive for forward migrations
.venv/bin/alembic downgrade -1            # one revision
# For a full rewind to the pre-incident schema:
# .venv/bin/alembic downgrade base        # DESTRUCTIVE — drops all data; only on rollback-now

# 5. Restore env-file to the last green snapshot
cp /etc/taskq/taskq.env.bak /etc/taskq/taskq.env

# 6. Restart and verify
systemctl start taskq-api
curl -fsS http://127.0.0.1:8000/healthz  # expect 200
curl -fsS http://127.0.0.1:8000/readyz   # expect 200 + check_db ok

# 7. If the rollback fails, escalate to the rollback owner (see Human Context).
```

## 8. Configuration Compliance
- [ ] Phase 7 risk mitigations implemented
- [ ] Monitoring thresholds configured
- [ ] Circuit breaker enabled

## 9. Human Context (P8 append)

The framework-generated sections above (1–8) were produced deterministically by
`harness/scripts/phase8_doc_gen.py` during the P7→P8 advance-phase. The following
items are HUMAN-owned and live in source control only because no automated
agent can supply them — they encode organizational ownership, secret cadence,
and audit-log pointers that the framework has no signal for.

### 9.1 Ownership per config item

| Item | Owner | Backup | Source-of-truth module |
|------|-------|--------|------------------------|
| `TASKQ_DB_URL` (production) | Platform / SRE lead | Backend on-call | `03-development/src/taskq_api/repository/session.py` (`DB_URL`) + `03-development/src/migrations/env.py` |
| `TASKQ_DB_POOL_SIZE` (production) | Platform / SRE lead | Backend on-call | `03-development/src/taskq_api/repository/session.py` (`POOL_SIZE`) |
| `TASKQ_MAX_CONCURRENT`, `TASKQ_DRAIN_TIMEOUT` | Service-owner (queue team) | Backend on-call | `03-development/src/taskq_api/service/runner_scheduler.py` |
| `TASKQ_TASK_TIMEOUT` | Service-owner (queue team) | Backend on-call | `03-development/src/taskq_api/service/runner.py` (`_resolve_timeout`) |
| `TASKQ_RATE_BURST`, `TASKQ_RATE_PER_SEC` | API-platform team | Backend on-call | `03-development/src/taskq_api/api/deps.py` (`RATE_BURST` / `RATE_PER_SEC`) + `03-development/src/taskq_api/service/ratelimit.py` |
| API-key lifecycle (issue / rotate / revoke) | Security team | Auth on-call | `03-development/src/taskq_api/repository/key_repo.py` + `taskq_api/api/deps.py::require_api_key` (FR-03, NFR-04) |
| Alembic migration chain (head = `v3_split_results`) | Backend on-call | DB on-call | `03-development/src/migrations/versions/v1_initial.py`, `v2_tags.py`, `v3_split_results.py` |
| `08-config/SBOM.json` (CycloneDX 1.5) | Release manager | Security team | Regenerated by `harness` during Gate 4 PASS |

### 9.2 Secret rotation cadence

| Secret | Where stored | Rotation cadence | Owner |
|--------|--------------|------------------|-------|
| API keys stored in `keys` table (SHA-256 hash + scope + `revoked_at`) | SQLite `keys` row | Every **90 days**; revoked keys remain in the table for audit (`revoked_at` non-null, FR-03 AC-3.5) | Security team |
| `/etc/taskq/taskq.env` (TASKQ_DB_URL, TASKQ_DB_POOL_SIZE, pool sizing) | Vault path `secret/taskq/prod`, mounted at `/etc/taskq/taskq.env` at boot | Every **180 days**, OR on personnel change, OR on any suspected disclosure | Platform / SRE lead |
| Alembic migration `downgrade` operator credentials | Vault path `secret/taskq/db-admin` | **No standing credentials.** Issued via JIT via Vault for the rollback window only. | DB on-call |

Rotation procedure for API keys:
1. `POST /v1/keys` (admin scope) → mint a new key, distribute out-of-band.
2. `POST /v1/keys/{key_id}/revoke` (admin scope) → flip `revoked_at`. The old key
   immediately fails `require_api_key` with `type=/errors/unauthenticated` (FR-03 §3 AC-3.5).
3. Audit log entry written by `key_repo.revoke` — see `09-maintenance/audit_log.md`
   for the schema.

Rotation procedure for `taskq.env`:
1. Issue new DB URL via Vault; write to a `taskq.env.new` sibling.
2. `systemctl reload taskq-api` (graceful — uvicorn re-imports `DB_URL` only on
   process restart, so a full restart is required).
3. `rm taskq.env.new` after `systemctl restart taskq-api` confirms `/readyz` returns 200.

### 9.3 Access audit log reference

- **Application audit log** — every `require_api_key` failure emits a WARNING log
  line with `correlation_id=...` and the presented key's SHA-256 prefix (first 8
  hex chars only — NFR-04 forbids logging the full hash or any plaintext). Sink:
  `journald → Loki` under label `{app="taskq-api", stream="auth"}`. Query:
  `{app="taskq-api"} |= "unauthenticated"`.
- **Key lifecycle audit** — every `create_key` / `revoke` / `rotate` writes a row
  to `audit_key_event(key_id, action, actor, ts, correlation_id)`. This table is
  Append-only (no `UPDATE` / `DELETE` in code) and is read by the security team's
  weekly review. See `03-development/src/taskq_api/repository/key_repo.py` for
  the write paths and `09-maintenance/audit_log.md` for the reader.
- **Migration audit** — `alembic upgrade` / `downgrade` runs are recorded in
  `alembic_version.version_num` plus the stdout of the operator; the
  `_reset_head_token_if_present` / `_stamp_head_token_at_chain_head` helpers in
  `migrations/env.py` keep the symbolic `'head'` token in sync so the
  audit-readable `version_num` is the actual revision id after every run.
- **External references** — the platform-wide audit dashboard
  (https://grafana.internal/d/taskq-audit, internal-only) cross-references the
  three streams above. Ownership of that dashboard: Platform / SRE lead.
- **Compliance evidence** — Gate 4 produced `06-quality/QUALITY_REPORT.md` with
  `Secrets Scanning = 100.0/100` and `Security = 99.0/100`. The 1-point Security
  deduction is the `subprocess` import in `migrations/env.py` (informational,
  not exploitable).
