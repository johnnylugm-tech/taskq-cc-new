# RISK STATUS REPORT — taskq-cc-new

| Field | Value |
|---|---|
| Project | `taskq-cc-new` |
| Phase | 7 — Risk Management |
| As-of | 2026-08-30 |
| Counterparts | `RISK_REGISTER.md` (canonical IDs) · `RISK_MITIGATION_PLANS.md` (P-* plans) |
| Last Gate | Gate 4 PASS @ 94.43/100 (commit `3376dd1`) |
| Total tracked risks | 18 (12 SPEC §9 seeded + 6 operational from Gate degradation log) |
| Open risks (P-* plan in flight) | 5 (O-5, O-4, O-3, O-2, O-1, plus monitored items on the P-* board) |
| Closing risks (controls in place, monitor only) | 12 — covered by Gate 3/Gate 4 evidence |

---

## 1. Executive Summary

The project is in a **post-Gate-4** posture. All 10/10 FRs are COMPLETE per
`CLAUDE.md` Gate 1 (FR-01…FR-10) and the four-pass gate progression table shows:

| Gate | Score | Status |
|------|-------|--------|
| 1 | 10/10 FRs | ✅ PASS |
| 2 | 91.59 | ✅ PASS |
| 3 | 93.681 | ✅ PASS |
| 4 | 94.43 | ✅ PASS |

Against this baseline, **the SPEC §9 risk seed (R1–R12) is largely mitigated by
existing gates and tests**. The novel risks identified during Phase 3–6 are the
ones driving the open mitigation plans, and they cluster in three areas:
(1) **architecture governance** (O-2), (2) **test-traceability hygiene**
(O-1), and (3) **infra runtime budgets** (O-6).

**Headline counts**

- CRITICAL: 3 risks (R5, O-1, O-2)
- HIGH: 5 risks (R1, R3, R6, R9, O-6)
- HIGH-mid: 3 (R2, O-5, R4 if re-banded)
- MEDIUM: 7 (R4, R7, R8, R10, R11, R12, O-3, O-4)
- LOW: 0

**One-week outlook**: none of the open items block release; all have a 2026-09-13
plan deadline or earlier.

---

## 2. Risk Register Snapshot

| ID | Name | I×L | Severity | Plan | Owner | Deadline | Status |
|----|------|----:|----------|------|-------|----------|--------|
| R1 | v3 migration data loss | 5×3 = 15 | HIGH | P-R1 | dba-lead | 2026-09-13 | monitored |
| R2 | SQL injection | 5×2 = 10 | HIGH | P-R2 | sec-lead | 2026-09-13 | mitigated, gated |
| R3 | API key leakage | 5×3 = 15 | HIGH | P-R3 | sec-lead | 2026-09-13 | monitored |
| R4 | 403 resource-presence leak | 3×3 = 9 | MEDIUM | P-R4 | auth-lead | 2026-09-20 | mitigated |
| R5 | N+1 perf catastrophe | 5×5 = 25 | CRITICAL | P-R5 | perf-lead | 2026-09-13 | monitored |
| R6 | Error-body leaks | 3×5 = 15 | HIGH | P-R6 | api-lead | 2026-09-13 | mitigated |
| R7 | CancelledError swallowed | 3×3 = 9 | MEDIUM | P-R7 | runner-lead | 2026-09-20 | mitigated |
| R8 | Orphan subprocess | 3×3 = 9 | MEDIUM | P-R8 | runner-lead | 2026-09-20 | mitigated |
| R9 | Deployment skipped migration | 5×3 = 15 | HIGH | P-R9 | sre-lead | 2026-09-13 | mitigated |
| R10 | Connection-pool exhaustion | 3×3 = 9 | MEDIUM | P-R10 | dba-lead | 2026-09-20 | mitigated |
| R11 | Incompatible transitive license | 3×3 = 9 | MEDIUM | P-R11 | sec-lead | 2026-09-20 | mitigated, gated |
| R12 | Rate-bucket race | 2×3 = 6 | MEDIUM | P-R12 | api-lead | 2026-09-27 | mitigated |
| O-1 | Deferred ACs without verifier proof | 4×4 = 16 | CRITICAL | P-O1 | quality-lead | 2026-09-13 | **open** |
| O-2 | Architecture constraints with no executor | 4×5 = 20 | CRITICAL | P-O2 | arch-lead | 2026-09-13 | **open** |
| O-3 | SSOT scaffold SPEC-token mis-parse | 3×3 = 9 | MEDIUM | P-O3 | infra-lead | 2026-09-27 | partial |
| O-4 | Lessons without ranking/index | 3×3 = 9 | MEDIUM | P-O4 | quality-lead | 2026-09-27 | **open** |
| O-5 | `.coverage.*` sentinel pollution | 2×5 = 10 | HIGH | P-O5 | dba-lead | 2026-09-03 | **open** |
| O-6 | Token / wall-clock budget blow-up | 4×3 = 12 | HIGH | P-O6 | infra-lead | 2026-09-06 | partial |

> Status legend — **mitigated**: control active + tested; **mitigated, gated**:
> additionally enforced by a named CI gate; **monitored**: control active but no
> deterministic test (re-checked at every Phase handover); **partial**: one of the
> controls exists, the gap remains; **open**: plan accepted, mitigation not yet
> executed.

---

## 3. Status by Category

### 3.1 Security (R2, R3, R4, R11)

| ID | Status | Evidence |
|----|--------|----------|
| R2 | mitigated, gated | bandit 0 HIGH; SQLAlchemy ORM-only; lint-imports contract enforced |
| R3 | monitored | gitleaks PASS; auth module 100% test coverage; constant-time compare in place |
| R4 | mitigated | FR-04 sentinel suite green; integration tests confirm 401/403 ordering |
| R11 | mitigated, gated | `pip-licenses --with-system` PASS; lock file checked in |

### 3.2 Performance / Scalability (R5, R10, R12)

| ID | Status | Evidence |
|----|--------|----------|
| R5 | monitored | pytest-benchmark with query_count fixture per list endpoint; SQL-count assertions in CI |
| R10 | mitigated | `pool_pre_ping=True`; concurrency limit in runner; connection-leak integration test |
| R12 | mitigated | row-level lock + single transaction; concurrent-request test simulates 2× bucket |

### 3.3 Migration / Deployment (R1, R9)

| ID | Status | Evidence |
|----|--------|----------|
| R1 | monitored | round-trip reversibility test on `v3_split_results`; baseline hash stored |
| R9 | mitigated | `/readyz` returns 503 when `alembic_version` ≠ `TASKQ_EXPECTED_HEAD`; integration test green |

### 3.4 Subprocess / Async / Lifecycle (R7, R8)

| ID | Status | Evidence |
|----|--------|----------|
| R7 | mitigated | Gate 3 re-verified after `child_pgid=''` regression (see `linting.txt`); ast-error-handling lint pin in harness |
| R8 | mitigated | subprocess lifecycle test asserts `wait()` is awaited; graceful-drain assertion on shutdown |

### 3.5 API error-handling (R6)

| ID | Status | Evidence |
|----|--------|----------|
| R6 | mitigated | RFC 7807 fixed-field schema enforced; detail allow-list at `taskq_api/errors.py`; negative test confirms leakage blocked |

### 3.6 Architecture / Governance (O-2)

| ID | Status | Evidence |
|----|--------|----------|
| O-2 | **open** | import-linter does not enforce 3 of 4 declared architecture constraints; `taskq_api` package sits outside every contract |

### 3.7 Process / Test-Traceability (O-1, O-4)

| ID | Status | Evidence |
|----|--------|----------|
| O-1 | **open** | 45 ACs deferred to verifiers; no proof those verifiers ever ran |
| O-4 | **open** | `.methodology/lessons/*.json` accumulating ≥ 10 entries without indexed view |

### 3.8 Infra / Runtime (O-3, O-6)

| ID | Status | Evidence |
|----|--------|----------|
| O-3 | partial | `requirements.txt` filtered from SPEC; manual cross-check required vs `requirements.lock` |
| O-6 | partial | `max_turns`/`task_timeout` escalation policies documented but not codified in preflight |

### 3.9 Hygiene / Repo-Cleanliness (O-5)

| ID | Status | Evidence |
|----|--------|----------|
| O-5 | **open** | 8 `.coverage.*` sentinel files at root in `git status` snapshot; `.gitignore` missing |

---

## 4. Open Work — Owners & Targets

Sorted by deadline (soonest first), then severity.

| Plan | Risk | Owner | Deadline | Next milestone |
|------|------|-------|----------|----------------|
| P-O5 | O-5 sentinel pollution | dba-lead | 2026-09-03 | Add `.coverage.*` to `.gitignore`; CI gate |
| P-O6 | O-6 budget blow-up | infra-lead | 2026-09-06 | `PERF_BUDGETS.md` + `fr-step-budget` CLI |
| P-R1 | R1 migration data loss | dba-lead | 2026-09-13 | Round-trip test green; baseline hash in `migration_baseline.json` |
| P-R2 | R2 SQL injection | sec-lead | 2026-09-13 | bandit 0 HIGH; grep gate green |
| P-R3 | R3 API key leak | sec-lead | 2026-09-13 | gitleaks PASS; constant-time compare audited |
| P-R5 | R5 N+1 perf | perf-lead | 2026-09-13 | `pytest -m perf --benchmark-only` PASS; `perf_thresholds.yaml` |
| P-R6 | R6 error-body leak | api-lead | 2026-09-13 | Detail allow-list locked; integration test green |
| P-R9 | R9 post-deploy migration | sre-lead | 2026-09-13 | `/readyz` 503 test green; `make verify-system` exit 0 |
| P-O1 | O-1 deferred ACs | quality-lead | 2026-09-13 | 0 ACs in `deferral` after next Gate |
| P-O2 | O-2 architecture governance | arch-lead | 2026-09-13 | 3 import-linter contracts added; `taskq_api` mapped |
| P-R4 | R4 403 leak | auth-lead | 2026-09-20 | `error_matrix.md` published |
| P-R7 | R7 CancelledError | runner-lead | 2026-09-20 | Runbook entry + lint pin re-verified |
| P-R8 | R8 orphan subprocess | runner-lead | 2026-09-20 | CI run that kills runner mid-task |
| P-R10 | R10 pool exhaustion | dba-lead | 2026-09-20 | Soak test at 2× `pool_size` |
| P-R11 | R11 license | sec-lead | 2026-09-20 | CI fail-step on allow-list change |
| P-O3 | O-3 SSOT scaffold | infra-lead | 2026-09-27 | `validate-requirements-tokens` preflight |
| P-O4 | O-4 lessons index | quality-lead | 2026-09-27 | `lessons_index.md` with top-N view |
| P-R12 | R12 rate-bucket race | api-lead | 2026-09-27 | Micro-benchmark at 2× rate |

---

## 5. Trend (gate-over-gate)

Comparing Gate 3 (93.681) → Gate 4 (94.43) is a +0.75 net improvement.

| Dimension | Gate 3 | Gate 4 | Δ | Risks reinforced |
|-----------|-------:|-------:|---:|------------------|
| linting | 100.0 | ≥ 100 | 0 | R7 regression caught at G3; re-fix verified |
| type_safety | 100.0 | ≥ 100 | 0 | runner.py:365 `child_pgid` regression caught |
| test_coverage | 100.0 | ≥ 100 | 0 | R5, R12 controls exercised |
| security | 99.0 | ≥ 99 | 0 | bandit LOW only; R2, R3 controls active |
| secrets_scanning | 100.0 | ≥ 100 | 0 | R3 active |
| license_compliance | 100.0 | ≥ 100 | 0 | R11 active |
| integration_coverage | (Gate 3 dipped to 72, recovered to ≥80) | ≥ 80 | ↑ | R5 derivation locked-in by E2E API/storage combos |
| execute_verification_target | 100.0 | ≥ 100 | 0 | R1 round-trip test |

**Negative findings only at Gate 3**: integration_coverage dipping to 72 triggered
`dimension_below_threshold` (lesson key `02bd20af8f39`). Recovery path:
add real-collab tests (API in, storage out) — recorded as the
remediation action in the lesson store. Status: **recovered at Gate 4**.

---

## 6. Mitigation Effectiveness (signals)

| Signal | Source | Reading |
|--------|--------|--------|
| `degradations.jsonl` rate per Phase | `.methodology/degradations.jsonl` (jsonl tail) | Heaviest at Gate 1/2/3 (≥ 10 `gate:ac-deferred` lines), trended down at Gate 4. O-1 carries forward the artefact until next Gate. |
| Lesson store cardinality | `.methodology/lessons/` ≥ 10 files | Recurring themes: dimension-below-threshold, tool-evidence-missing. O-4 plan addresses discoverability. |
| FR-tier incidents | `fr-step-no-progress` lines | Single occurrence for FR-01 GATE1 (2 consecutive no-progress fix rounds). Closed at next step. Not carried into P7. |
| Infra escalations | `run-fr-step:TDD-GREEN` `TURN_BUDGET`/`TIMEOUT` | Two episodes at FR-01; resolved by `max_turns 50→100`, `task_timeout 600→1200`. Drives O-6. |

---

## 7. Risks Closed (Recent — last 14 days)

| ID | Closure date | Closure evidence |
|----|--------------|------------------|
| Gate 3 regressed: `child_pgid` in `runner.py` | 2026-08-25 | restored to `None`; pyright errorCount=0; captured in `.methodology/gate_evidence/gate3/type_safety.txt` |
| Gate 3 `integration_coverage` dipped to 72 | 2026-08-25 | recovered to ≥ 80 by adding API-in/storage-out integration tests (lesson `02bd20af8f39`) |
| Gate 1 `required_artifact_missing` (6 deliverables) | 2026-08-24 | all 6 deliverables shipped (`.env.example`, `requirements.lock`, `alembic.ini`, `Makefile`, `.methodology/harness_config.json`, `08-config/SBOM.json`) by Gate 4 |

---

## 8. Risks Reopened (none)

No risk has been formally reopened since Gate 3. R5 and R3 keep the "monitored" tag
because their controls are active but no deterministic regression test catches
their re-emergence; this is by design (N+1 regressions and key-leaks are detected
by behaviour, not unit test pinpoint).

---

## 9. Cross-Reference Index

| Doc | Path | Anchor |
|-----|------|--------|
| Risk register | `07-risk/RISK_REGISTER.md` | IDs, scores, owners |
| Mitigation plans | `07-risk/RISK_MITIGATION_PLANS.md` | P-* actions, deadlines |
| Gate 4 manifest | `.methodology/gate4_result.json` | Baseline this report scores against |
| Degradation log | `.methodology/degradations.jsonl` | Source for O-1/O-2/O-3/O-6 evidence |
| Lessons | `.methodology/lessons/*.json` | O-4 source; recurring themes |
| SPEC §9 | `SPEC.md` lines 441–456 | R1–R12 seed |
| FR gate | `CLAUDE.md` | Gate Progression table |
| Handover | `HANDOVER.md` | Phase status (cites P7 deliverables) |

---

## 10. Sign-off Block

| Role | Name | Date | Verdict |
|------|------|------|---------|
| Author | P7 Risk Author (orch-post) | 2026-08-30 | drafted |
| Reviewer (Agent B) | <name pending> | <pending> | pending — `validate-handoff` is the gate |
| Final approver | Johnny | <pending> | pending |

| Field | Value |
|-------|-------|
| Gate progression impact | unchanged — Gate 4 still PASS at 94.43 |
| Release-blocking risks | 0 |
| Work-blocking risks | 0 (every open plan has a named owner + deadline) |
| Next phase readiness | **Yes** if P-O5 + P-O6 close by their respective dates; otherwise **Yes with caveats** |

---

## 11. Self-Review

- **What could be wrong**: (a) The "Trend" section compares dimensions where
  framework evidence is per-Gate but the `Δ` rows reflect best-effort diffing
  not strict numeric subtraction — Gate 4 evidence shows ≥100 for some dims but
  exact equivalence is not asserted; (b) Owner names (`*-lead`) are role placeholders
  that need reconciliation against `state.json` `assignees` (if populated); (c)
  The "Open Work" table counts R4 and R7/R8 at 9 (medium band) but I included
  them in mitigation plans because the threshold-of-9 trigger pulled them in,
  which is correct, but a future reviewer may want them downgraded once R4
  (specifically) clears its next recalibration; (d) The bottom-line "0 release-
  blocking risks" should be re-validated at every Phase handover — the dependency
  on the as-yet-unremediated O-1/O-2 is what makes that statement a snapshot,
  not a permanent one.
- **Unverified assumptions**: that the orchestrator's resource pool contains
  humans assignable to `*-lead` roles; that `validate-handoff --from-phase 7`
  will parse these three files; that the lessons JSON is append-only.
- **Confidence**: **Medium-High** on the SPEC R1–R12 status (anchored to Gate 4
  evidence); **Medium** on the O-* posture (forward-derived from runtime signals,
  not yet re-baselined at a Gate re-run).
