# RELEASE_CHECKLIST

## Pre-Release Checks
- [ ] All P1-P7 phases completed and artifacts generated.
- [ ] CI pipeline fully passed.
- [ ] Final Sign Off approved.
- [ ] Production environment provisioned.
- [ ] Rollback plan documented.

## Human Context (P8 append)

The "Pre-Release Checks" section above is the framework-generated skeleton (produced
by `harness/scripts/phase8_doc_gen.py` during the P7→P8 advance-phase). The Gate 4
PASS proof, `quality_manifest` composite score (94.4), FR coverage (FR-01..FR-10
all 100.0), and the git tag `gate4-20260826-score94` / hash `b65aa12` live in
`.methodology/agent_b_approvals/QUALITY_REPORT.md.json`,
`RELEASE_NOTES.md.json`, `FINAL_SIGN_OFF.md.json`, and `quality_manifest.json`
— they are intentionally NOT duplicated here. Items below are HUMAN-owned.

### RC-1. Deployment runbook
- **Runbook URL** — `https://wiki.internal/runbooks/taskq-cc-new/release` (internal-only; mirrors the commands in `CONFIG_RECORDS.md §7`).
- **Pre-deploy** — `git checkout gate4-20260826-score94 && .venv/bin/pip install -r requirements.lock && .venv/bin/alembic upgrade head`.
- **Boot** — `uvicorn taskq_api.app:app --host 0.0.0.0 --port 8000 --workers 1` under `systemd` unit `taskq-api.service`.
- **Smoke** — `curl -fsS http://127.0.0.1:8000/healthz` (expect 200) and `curl -fsS http://127.0.0.1:8000/readyz` (expect 200 with `check_db: ok` and `check_migration_head: ok`).
- **Post-deploy** — confirm `/v1/metrics` returns 200 with an `admin`-scope key, then `POST /v1/tasks` with a `read`-scope key returns 200.

### RC-2. Rollback owner + on-call
- **Rollback owner** — Platform / SRE lead (`sre-lead@internal`). Decision authority for `rollback-now`.
- **On-call** — PagerDuty schedule `taskq-api-primary` (24/7). Escalation path: on-call → Platform / SRE lead → Eng Director.
- **Severity gate** — `rollback-now` is authorized when:
  - Gate 4 score regresses below 85/100, OR
  - a P0 defect is confirmed in production (auth bypass, N+1 SQL regression,
    broken Alembic `upgrade head`, or rate-limit bypass).
- **Runbook** — `https://wiki.internal/runbooks/taskq-cc-new/rollback` (mirrors `CONFIG_RECORDS.md §7`).

### RC-3. Post-release monitoring dashboard
- **Primary** — `https://grafana.internal/d/taskq-overview` (request rate, p50/p95/p99 latency, error rate by `type=...`, DB pool saturation, `/readyz` uptime).
- **Auth stream** — `https://grafana.internal/d/taskq-auth` (401 / 403 rate by `correlation_id`, `revoked_at` flips, key-rotation events from `audit_key_event`).
- **Rate-limit stream** — `https://grafana.internal/d/taskq-ratelimit` (`429` rate, `Retry-After` distribution, `TASKQ_RATE_BURST` / `TASKQ_RATE_PER_SEC` utilization).
- **Alerts** — PagerDuty integration `taskq-alerts` fires when: (a) error rate > 1% over 5 min, (b) p95 latency > 500 ms over 5 min, (c) `/readyz` returns non-200 for 3 consecutive probes (30 s), (d) DB pool saturation > 90% for 2 min.

### RC-4. Customer comms template
```
Subject: [taskq-api] v<harness-v4-20260826-score94-18-gb65aa12> deployed — no action required

Hi <customer>,

We deployed taskq-api v<harness-v4-20260826-score94-18-gb65aa12> (commit b65aa12) to
production on <DATE>. No customer action is required.

What's in this release:
- <copy from RELEASE_NOTES.md §"Added" / §"Changed" / §"Fixed">
- <FR coverage summary: which FR IDs shipped, all at score 100.0>

Monitoring: https://status.internal/taskq-api
Rollback owner: sre-lead@internal (see RC-2 above)
Questions: <support alias>

— taskq-api release team
```

Variant for **rollback** communications:
```
Subject: [taskq-api] ROLLBACK to v<previous-green> — investigation in progress

Hi <customer>,

We rolled taskq-api back to v<previous-green> at <TIMESTAMP> UTC because of <brief
description>. Service is restored to the prior green state; no customer data was
lost. Investigation is in progress; updates every <N> hours at
https://status.internal/taskq-api/incidents/<ID>.

— taskq-api release team
```
