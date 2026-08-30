# RISK REGISTER — taskq-cc-new

| Field | Value |
|---|---|
| Project | `taskq-cc-new` |
| Phase | 7 — Risk Management |
| Author | P7 Risk Author (orch-post) |
| Date | 2026-08-30 |
| Source-of-truth | `SPEC.md` §9 risk matrix (R1–R12) + Gate 3/Gate 4 degradation log (`.methodology/degradations.jsonl`) + lessons (`gate-block` lessons) |
| Gate reference | Gate 4 PASS @ 94.43/100 (commit `3376dd1`, baseline `8817cc8`) |
| FR universe | FR-01…FR-10 (all 10/10 FRs COMPLETE per Gate 1) |
| Re-eval cadence | re-score after every Gate; ad-hoc on any FR-tier incident |

---

## 1. Scoring Convention

| Impact (I) | 5=catastrophic | 4=major | 3=moderate | 2=minor | 1=negligible |
|---|---|---|---|---|---|
| Likelihood (L) | 5=almost certain | 4=likely | 3=possible | 2=unlikely | 1=rare |

`Score = L × I`. Severity tiers:

| Band | Score | Tier | Mitigation expectation |
|---|---|---|---|
| Critical | ≥ 16 | **CRITICAL** | Immediate mitigation plan, named owner, weekly tracking |
| High | 10–15 | **HIGH** | Formal mitigation plan (see `RISK_MITIGATION_PLANS.md`) |
| Medium | 6–9 | **MEDIUM** | Mitigation plan bundled by category, monitored |
| Low | ≤ 5 | **LOW** | Monitoring + documented rationale, no plan required |

SPEC §9 uses Chinese `高/中/低` for impact and likelihood. This register maps them deterministically:

| SPEC qualifier | Score |
|---|---|
| 高 (impact) | 5 |
| 中 (impact) | 3 |
| 低 (impact) | 2 |
| 高 (likelihood) | 5 |
| 中 (likelihood) | 3 |
| 低 (likelihood) | 2 |

> `SDG-3.1 × SDG-3.1 ⇒ SDG-3.1²` is **deliberately** not allowed. Re-mapping a deployed system under duress is a 5-impact event; likelihood sits at 1 (already shipped, infra guards in place).

---

## 2. SPEC §9 Risk Matrix — Authoritative Seed (R1–R12)

The following 12 risks are **directly imported from SPEC.md §9** with their declared mitigation; this register augments each with explicit L/I scores, severity band, current evidence, and tracking owner.

| ID | Risk | Impact (SPEC) | Likelihood (SPEC) | I | L | Score | Severity | Mitigation (SPEC) | Tracking owner | Current evidence (Gate 4) |
|----|------|--------------:|------------------:|--:|--:|------:|----------|-------------------|----------------|---------------------------|
| R1 | **v3 資料搬遷遺失資料** | 高 | 中 | 5 | 3 | **15** | **HIGH** | 往返可逆性測試以真實 DB 逐欄比對 (FR-07 / §8 #12) | dba-lead | migrations round-trip evidence in P6 baseline; `v3_split_results` migration integrated; 100% test coverage on 630/630 statements. STATUS: **mitigated, monitored** |
| R2 | SQL injection | 高 | 低 | 5 | 2 | **10** | **HIGH** | 禁字串拼接 + ORM/參數化 + grep gate (NFR-02) | sec-lead | bandit 0 HIGH / 0 MEDIUM / 1 LOW; lint-imports contract enforced; SQLAlchemy ORM-only model layer. STATUS: **mitigated, gated** |
| R3 | API key 洩漏 | 高 | 中 | 5 | 3 | **15** | **HIGH** | 雜湊儲存 + 常數時間比對 + 明文只印一次 (FR-03) | sec-lead | gitleaks `no leaks found`; auth module 100% test coverage; constant-time compare utility audited in P3 Gate 1. STATUS: **mitigated, monitored** |
| R4 | 403 洩漏資源存在性 | 中 | 中 | 3 | 3 | **9** | **MEDIUM** | 授權判定在資源查詢之前 (FR-04 / §8 #6) | auth-lead | FR-04 sentinel suite covers differential 401/403/404 ordering; integration tests verify authorization precedes lookup. STATUS: **mitigated** |
| R5 | **N+1 查詢在大表上崩潰** | 高 | 高 | 5 | 5 | **25** | **CRITICAL** | 顯式預載 + SQL 計數斷言 (NFR-01 / §8 #14) | perf-lead | pytest-benchmark harness + SQL count assertions enforce N+1 detection; perf regression threshold in CI. STATUS: **mitigated, gated** |
| R6 | 錯誤 body 洩漏內部結構 | 中 | 高 | 3 | 5 | **15** | **HIGH** | RFC 7807 固定欄位 + detail 白名單 (FR-10) | api-lead | exception handlers reject unknown fields; integration tests assert body shape; gate 4 evidence captured. STATUS: **mitigated** |
| R7 | **`CancelledError` 被吞 → 關閉時卡死** | 中 | 中 | 3 | 3 | **9** | **MEDIUM** | 明文禁令 + 測試斷言 (NFR-03) | runner-lead | ast-error-handling lint pin in harness; explicit `except CancelledError: raise` blocks audited in `taskq_api/service/runner.py` (Gate 3 re-verified after `child_pgid=''` regression fix per `linting.txt`). STATUS: **mitigated** |
| R8 | 任務 timeout 留下孤兒進程 | 中 | 中 | 3 | 3 | **9** | **MEDIUM** | `kill()` + `await wait()` (FR-08 / §8 #25) | runner-lead | subprocess lifecycle tests assert `wait()` is awaited; graceful-drain assertion on shutdown covers in-flight tasks (FR-08). STATUS: **mitigated** |
| R9 | 部署後忘記跑 migration | 高 | 中 | 5 | 3 | **15** | **HIGH** | `/readyz` fail closed (FR-09 / §8 #11) | sre-lead | `alembic_version` check inside `/readyz`; integration test asserts 503 response when schema version missing; `make verify-system` exit 0 (NFR-12). STATUS: **mitigated** |
| R10 | 連線池耗盡 | 中 | 中 | 3 | 3 | **9** | **MEDIUM** | `pool_pre_ping` + 併發上限 (FR-06/FR-08) | dba-lead | SQLAlchemy engine config includes `pool_pre_ping=True`; concurrency limiter enforced in runner; connection-leak integration test. STATUS: **mitigated** |
| R11 | transitive 依賴引入不相容 license | 中 | 中 | 3 | 3 | **9** | **MEDIUM** | lock 檔 + 全樹掃描 (NFR-07) | sec-lead | `pip-licenses --with-system` runs full-tree; `requirements.lock` checked in; `test_dependency_license_in_allowlist` + `test_pip_licenses_with_system_full_tree` PASS (Gate 3 evidence). STATUS: **mitigated, gated** |
| R12 | rate bucket 競態導致超放行 | 低 | 中 | 2 | 3 | **6** | **MEDIUM** | 單一交易 + row-level lock (FR-05) | api-lead | ratelimit service uses row-level lock with single transaction boundary; concurrent-request test simulates ≥ 2× the bucket to confirm clamp. STATUS: **mitigated** |

> **Summary of seed risks**: 12 entries → 1 CRITICAL (R5), 5 HIGH (R1, R3, R6, R9, R2 by score), 6 MEDIUM (R4, R7, R8, R10, R11, R12), 0 LOW.

---

## 3. Operational Risks — Derived from Gate 3/Gate 4 Degradations

The following risks did **not** appear in SPEC §9 but surfaced during Gate execution or in the lesson store. They are appended (numbered `O-*` to avoid clashing with the SPEC seed) and tracked in the same register.

| ID | Risk | Category | I | L | Score | Severity | Source evidence | Tracking owner |
|----|------|----------|--:|--:|------:|----------|-----------------|----------------|
| O-1 | **45 acceptance criteria deferred to named external verifiers without a check that those verifiers ever ran** | process / test-traceability | 4 | 4 | **16** | **CRITICAL** | `.methodology/degradations.jsonl` `gate:ac-deferred` repeated ≥ 10× across Gate 1/2. Lesson: `tool_evidence_missing` raises `dimension_below_threshold`. | quality-lead |
| O-2 | **Architecture constraints without deterministic executor** — `no_sqlalchemy_above_repository`, `models_sqlalchemy_orm_declarative_only`, `downward_only_layer_dependencies` declared in CLAUDE.md but never enforced by `import-linter`; `taskq_api` package sits outside every contract | architecture / governance | 4 | 5 | **20** | **CRITICAL** | `.methodology/degradations.jsonl` `gate:arch-constraints` (`declared_only=[…]`, `uncovered_modules=["taskq_api"]`). The framework only enforces `no_circular_dependencies` today. | arch-lead |
| O-3 | **SSOT scaffold mis-parsed SPEC tokens**, silently dropping `httpx.asgitransport`, `三個`, `X-API-Key`, `per-token`, `RFC` and §0 `rest`/`async` | dependency / SSOT | 3 | 3 | **9** | **MEDIUM** | `.methodology/degradations.jsonl` `gate:env-repair` (11 deps emitted vs ambiguous SPEC cells). `requirements.txt` is filtered, not authoritative — divergence possible next time SPEC is re-scanned. | infra-lead |
| O-4 | **Lesson key collision risk / growth of dimension-bypass** — recurring `dimension_below_threshold` lessons accumulate without a top-N ranking; rerun may keep skipping the same gap | process / lessons | 3 | 3 | **9** | **MEDIUM** | `.methodology/lessons/gate-block/*.json` (≥ 10 entries; recurring themes: `integration_coverage` 72→80, `test_coverage` 97→100). New operators read JSON files directly. | quality-lead |
| O-5 | **Mutable `coverage.json` + sentinel files accumulating at root** (`.coverage.<host>.pid<PID>.<rand>.<rand>.*`) clutter worktree and may leak into PRs | hygiene / process | 2 | 5 | **10** | **HIGH** | `git status` shows 8 such files at HEAD/snapshot. No `.gitignore` entry for `.coverage.*` glob. | dba-lead |
| O-6 | **Token / wall-clock budget blow-up** on FR `CODE-FIX` and `TDD-GREEN` steps; `max_turns` and `task_timeout` escalation are infra-side runtime risks, not code bugs | infra / runtime | 4 | 3 | **12** | **HIGH** | `.methodology/degradations.jsonl` `run-fr-step:TDD-GREEN` — `TURN_BUDGET`, `TIMEOUT`, escalation to `max_turns 100` / `task_timeout 1200`. P4+/P7 risk may reproduce. | infra-lead |

**Summary of operational risks**: 6 entries → 2 CRITICAL (O-1, O-2), 2 HIGH (O-5, O-6), 2 MEDIUM (O-3, O-4).

---

## 4. Combined Risk Ledger (R1–R12 + O-1…O-6)

Sorted by severity then score (descending).

| Rank | ID | Category | I | L | Score | Severity | Status |
|-----:|----|----------|--:|--:|------:|----------|--------|
| 1 | R5 | performance / N+1 | 5 | 5 | 25 | **CRITICAL** | mitigated, gated |
| 2 | O-2 | architecture / governance | 4 | 5 | 20 | **CRITICAL** | partial — needs import-linter contracts |
| 3 | O-1 | process / test-traceability | 4 | 4 | 16 | **CRITICAL** | open — deferral list not retired |
| 4 | R1 | data-loss / migration | 5 | 3 | 15 | **HIGH** | mitigated, monitored |
| 5 | R3 | security / credential | 5 | 3 | 15 | **HIGH** | mitigated, monitored |
| 6 | R6 | API / error-handling | 3 | 5 | 15 | **HIGH** | mitigated |
| 7 | R9 | deployment / readiness | 5 | 3 | 15 | **HIGH** | mitigated |
| 8 | O-6 | infra / runtime | 4 | 3 | 12 | **HIGH** | partial — escalation policies exist but not codified |
| 9 | R2 | security / SQLi | 5 | 2 | 10 | **HIGH** | mitigated, gated |
| 10 | O-5 | hygiene / process | 2 | 5 | 10 | **HIGH** | open — needs `.gitignore` fix |
| 11 | R4 | authz / leak | 3 | 3 | 9 | **MEDIUM** | mitigated |
| 12 | R7 | async / cancellation | 3 | 3 | 9 | **MEDIUM** | mitigated |
| 13 | R8 | subprocess / lifecycle | 3 | 3 | 9 | **MEDIUM** | mitigated |
| 14 | R10 | connection / pool | 3 | 3 | 9 | **MEDIUM** | mitigated |
| 15 | R11 | license / compliance | 3 | 3 | 9 | **MEDIUM** | mitigated, gated |
| 16 | R12 | rate-limit / race | 2 | 3 | 6 | **MEDIUM** | mitigated |
| 17 | O-3 | dependency / SSOT | 3 | 3 | 9 | **MEDIUM** | partial — manual cross-check required |
| 18 | O-4 | lessons / discovery | 3 | 3 | 9 | **MEDIUM** | open — needs lessons index |

---

## 5. Risk Categories (cross-cutting)

| Category | Risks | Highest score |
|----------|-------|---------------|
| Security (authn/authz/secret/SQLi/license) | R2, R3, R4, R11 | 15 |
| Performance / N+1 / pool / rate-limit | R5, R10, R12 | 25 |
| Migration / deployment | R1, R9 | 15 |
| Subprocess / async / lifecycle | R7, R8 | 9 |
| API error-handling | R6 | 15 |
| Architecture / governance | O-2 | 20 |
| Process / test-traceability | O-1, O-4 | 16 |
| Infra / runtime | O-3, O-6 | 12 |
| Hygiene / repo-cleanliness | O-5 | 10 |

---

## 6. Risk Review Cadence

| Trigger | Action |
|---------|--------|
| New Gate score posted | Re-score every risk whose mitigation status could move |
| Any new degradations line keyed `gate:*` or `fr-step-no-progress` | Append `O-*` row, assign owner within 1 business day |
| FR-tier incident in production | Promote score by +1 (or +2 if systemic); surface in next `RISK_STATUS_REPORT.md` |
| Quarterly / every Phase handover | Owner walks `RISK_STATUS_REPORT.md` end-to-end, updates status field |

---

## 7. Provenance & Cross-References

| Document | Path | Used for |
|----------|------|----------|
| Risk matrix | `SPEC.md` §9 (lines 441–456) | R1–R12 seed + mitigations |
| Gate 4 result | `.methodology/gate4_result.json` | PASS at 94.43; per-dim scores anchoring mitigations |
| Gate 3 result | `.methodology/gate3_result.json` | Pre-Gate-4 evidence trail (regression on `child_pgid` regression fixed) |
| Degradation log | `.methodology/degradations.jsonl` | O-1, O-2, O-3, O-6 evidence |
| Lesson store | `.methodology/lessons/*.json` | O-4 evidence, recurring themes |
| Session history | `.methodology/sessions_spawn.log` | Token-budget / wall-clock incidents backing O-6 |
| Git status snapshot | `git status` at 2026-08-30 | O-5 evidence (8 `.coverage.*` files at root) |

---

## 8. Self-Review

- **What could be wrong**: (a) the SPEC §9 numeric mapping (`高→5, 中→3, 低→2`) is one plausible mapping but not the only one — `高→4, 低→1` would push R5 to 16, R2 to 4, etc.; (b) I elevated O-5 (`.coverage.*` clutter) to HIGH even though the underlying harm is purely hygiene, on the basis that the absence of a `.gitignore` entry is a near-certain leak into a PR (`L=5`); (c) O-1 and O-2 were asserted from gate-blocking degradations observed during Phase 3–6 but their current state at Gate 4 may already be partly retired — readers should treat the **mitigation-plans doc** as the source of truth for which are still open.
- **Unverified assumptions**: the chain-of-custody on the lessons JSON is treated as authoritative; an attacker who can edit `.methodology/lessons/` would inflate their view of completed mitigations, so a checksum/SBOM-style anchor would help.
- **Confidence**: **Medium-High** — High for the SPEC R1–R12 seed, Medium for the O-* additions because each was reverse-derived from runtime signals rather than first-party incident reports.
