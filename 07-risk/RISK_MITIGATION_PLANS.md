# RISK MITIGATION PLANS — taskq-cc-new

| Field | Value |
|---|---|
| Project | `taskq-cc-new` |
| Phase | 7 — Risk Management |
| Counterpart doc | `RISK_REGISTER.md` (authoritative identifier map) |
| Threshold | mitigations required when `likelihood × impact ≥ 9` (HIGH/CRITICAL band) |
| Owner signing | project maintainer (orch-post routes through Johnny for sign-off) |
| Date | 2026-08-30 |

> **Definition of done** (per mitigation plan): (i) action items ascribed, (ii) deadline
> calendared, (iii) verification artefact named, (iv) rollback path documented for any
> change that touches the data path.

---

## 0. Plan Index (sorted by score, descending)

| Plan | Risk | Score | Severity | Owner | Deadline | Status |
|------|------|------:|----------|-------|----------|--------|
| P-R5 | R5 N+1 perf catastrophe | 25 | CRITICAL | perf-lead | 2026-09-13 | monitored |
| P-O2 | O-2 architecture constraints without executor | 20 | CRITICAL | arch-lead | 2026-09-13 | open |
| P-O1 | O-1 deferred ACs without verifier presence check | 16 | CRITICAL | quality-lead | 2026-09-13 | open |
| P-R1 | R1 v3 migration data loss | 15 | HIGH | dba-lead | 2026-09-13 | monitored |
| P-R3 | R3 API key leakage | 15 | HIGH | sec-lead | 2026-09-13 | monitored |
| P-R6 | R6 error-body leaks | 15 | HIGH | api-lead | 2026-09-13 | mitigated |
| P-R9 | R9 post-deploy missing migration | 15 | HIGH | sre-lead | 2026-09-13 | mitigated |
| P-O6 | O-6 token/wall-clock budget blow-up | 12 | HIGH | infra-lead | 2026-09-06 | open |
| P-R2 | R2 SQL injection | 10 | HIGH | sec-lead | 2026-09-13 | mitigated |
| P-O5 | O-5 .coverage.* sentinel pollution | 10 | HIGH | dba-lead | 2026-09-03 | open |
| P-R4 | R4 403 resource-presence leak | 9 | MEDIUM | auth-lead | 2026-09-20 | mitigated |
| P-R7 | R7 CancelledError swallowed | 9 | MEDIUM | runner-lead | 2026-09-20 | mitigated |
| P-R8 | R8 orphan subprocess | 9 | MEDIUM | runner-lead | 2026-09-20 | mitigated |
| P-R10 | R10 connection-pool exhaustion | 9 | MEDIUM | dba-lead | 2026-09-20 | mitigated |
| P-R11 | R11 incompatible transitive license | 9 | MEDIUM | sec-lead | 2026-09-20 | mitigated, gated |
| P-R12 | R12 rate-bucket race | 6 | MEDIUM | api-lead | 2026-09-27 | mitigated |
| P-O3 | O-3 SSOT scaffold mis-parsed SPEC tokens | 9 | MEDIUM | infra-lead | 2026-09-27 | open |
| P-O4 | O-4 lessons index without ranking | 9 | MEDIUM | quality-lead | 2026-09-27 | open |

All 18 plans are formal (score ≥ 9 trigger). The 12 §9 entries are bundled with the 6 operational ones.

---

## 1. CRITICAL Plans (score ≥ 16)

### P-R5 — N+1 query catastrophe (R5, score 25)

- **Mitigation approach** (from SPEC §9): explicit eager-load + SQL-count assertion
  (NFR-01, §8 #14).
- **Action items**
  1. Maintain the list of "must-be-eager" relationships in `taskq_api/repository/`
     and enforce via static check (`:raises SQLAlchemyWarning` annotation helper).
  2. Add pytest-benchmark with `query_count` fixture for every list endpoint.
  3. Fail CI when query count > N for the canonical example dataset (N is per-endpoint,
     currently N+0; documented in `03-development/tests/perf_thresholds.yaml`).
  4. Weekly perf regression report pasted into `.methodology/perf_log.md`.
- **Owner**: perf-lead.
- **Deadline**: 2026-09-13 (next Gate 4 re-run).
- **Verification**: `pytest -m perf --benchmark-only` exit 0; SQL count assertions
  pass for all list endpoints; CI gate blocks merges that exceed threshold.
- **Rollback**: a benchmark threshold breach flips the PR red; rollback == revert
  merge commit. No data path change.
- **Status**: monitored — Gate 4 PASS at 94.43 (perf component ≥ 90).

### P-O2 — Architecture constraints without deterministic executor (O-2, score 20)

- **Mitigation approach**: install import-linter contracts covering the 3 declared-only
  constraints and the `taskq_api` package that currently sits outside every contract.
- **Action items**
  1. Add 3 contracts to `.import-linter` (or `setup.cfg` `[importlinter:contract:…]`):
     `no_sqlalchemy_above_repository`, `models_sqlalchemy_orm_declarative_only`,
     `downward_only_layer_dependencies`.
  2. Map every module under `taskq_api/` to a layer; verify the `taskq_api` parent
     package is not a free node.
  3. Add `lint-imports` to the Gate 3 evidence list. Confirm the previously blocking
     `gate:arch-constraints` line in `degradations.jsonl` no longer appears at next
     Phase.
  4. Update CLAUDE.md "Architecture Constraints" table with the executor name +
     success message rather than the empty placeholder.
- **Owner**: arch-lead.
- **Deadline**: 2026-09-13.
- **Verification**: `lint-imports` exit 0; the three contracts bound; Gate 3
  arch-constraints dim returns ≥ 80.
- **Rollback**: contracts are non-breaking; deletion = full reversal of the import
  rules.
- **Status**: open — `degradations.jsonl` shows the gap across Gate 3 still.

### P-O1 — 45 acceptance criteria deferred without verifier-presence proof (O-1, score 16)

- **Mitigation approach**: every deferred AC must point to a verifier that
  demonstrably ran; otherwise the AC must be promoted back into `TEST_SPEC.md`.
- **Action items**
  1. Read the latest `degradations.jsonl` `gate:ac-deferred` line; enumerate the
     45 deferred ACs (N1, N2, N3, N4, N5, N6, N7, N8, N9, N10, N11, N12 groups).
  2. For each AC, point it at one of: a real pytest case, a real harness tool
     (`bandit`, `gitleaks`, `pip-licenses`, etc.) whose evidence file exists, or
     promotion back into `TEST_SPEC.md` as a parameterised case.
  3. Add a preflight check that fails the Gate when `gate:ac-deferred` count > 0
     *and* the linked verifier evidence file is missing.
  4. Re-run Gate 1; expected effect is the `deferred` list shrinks to 0.
- **Owner**: quality-lead.
- **Deadline**: 2026-09-13.
- **Verification**: post-mitigation `gate:ac-deferred` line absent from
  `degradations.jsonl` for the next Gate run.
- **Rollback**: revert TEST_SPEC.md changes; gate preflight revert.
- **Status**: open — repeated ≥ 10 times in `degradations.jsonl`.

---

## 2. HIGH Plans (score 10–15)

### P-R1 — v3 migration data loss (R1, score 15)

- **Mitigation approach** (SPEC §9): round-trip reversibility test on a real DB,
  column-by-column (FR-07, §8 #12).
- **Action items**
  1. The `migrations.versions.v3_split_results` revision ships with both
     forward (`v2_to_v3`) and backward (`v3_to_v2`) branches.
  2. Migration round-trip test seeds ≥ 1 000 rows across all migrated tables,
     asserts column counts and row counts match both directions.
  3. Capture baseline hash of rows before/after each migration run; stored in
     `.methodology/migration_baseline.json`.
- **Owner**: dba-lead.
- **Deadline**: 2026-09-13.
- **Verification**: `pytest tests/migrations/test_round_trip_v3.py -v` PASS; baseline
  hash unchanged across reruns.
- **Rollback**: rollback = revert migration heads; data path is left untouched.
- **Status**: monitored.

### P-R3 — API key leakage (R3, score 15)

- **Mitigation approach** (SPEC §9): hash storage + constant-time comparison +
  plaintext printed exactly once (FR-03).
- **Action items**
  1. `auth.py` uses `secrets.compare_digest` for any comparison.
  2. Plaintext `api_key` is printed exactly once (on creation); logs redact on
     subsequent reads via the canonical `[REDACTED]` token.
  3. gitleaks precommit hook + CI scan (`gitleaks detect --source . --no-banner`).
  4. Force-rotate at next Gate 4 (idempotent — does not change behaviour).
- **Owner**: sec-lead.
- **Deadline**: 2026-09-13.
- **Verification**: `bandit -r src/` 0 HIGH; `gitleaks detect` PASS; no plaintext
  keys in any log output (sampled across tests).
- **Rollback**: rotate exposed key, revert log redaction if mis-applied.
- **Status**: monitored.

### P-R6 — Error body leaks internal structure (R6, score 15)

- **Mitigation approach** (SPEC §9): RFC 7807 fixed fields + detail whitelist
  (FR-10).
- **Action items**
  1. Centralised exception handlers (`app.main.exception_handler`) emit only
     `type`, `title`, `status`, `detail` (when in allow-list); `instance` & `traceback`
     never reach the wire.
  2. Detail allow-list is a single source of truth in `taskq_api/errors.py`.
  3. Integration tests assert that a `RuntimeError` produces a body without
     stack-trace fields and without `__cause__`.
- **Owner**: api-lead.
- **Deadline**: 2026-09-13.
- **Verification**: integration tests PASS; negative test confirms leakage blocked;
  OpenAPI consumer does not raise on any error code.
- **Rollback**: revert handler tightening; bodies widen but stay RFC 7807-shaped.
- **Status**: mitigated.

### P-R9 — Deployment without migration (R9, score 15)

- **Mitigation approach** (SPEC §9): `/readyz` fail-closed (FR-09, §8 #11).
- **Action items**
  1. `/readyz` performs `SELECT version_num FROM alembic_version`; returns
     503 when the head revision does not match the configured `TASKQ_EXPECTED_HEAD`.
  2. The check is exercised end-to-end by `tests/integration/test_readyz.py`.
  3. CI gate has a 503-when-stale assertion that simulates the missing-row case.
- **Owner**: sre-lead.
- **Deadline**: 2026-09-13.
- **Verification**: integration test PASS; `make verify-system` exit 0
  (`NFR-12`).
- **Rollback**: revert readyz tightening; behaviour degrades to "always ready".
- **Status**: mitigated.

### P-O6 — Token / wall-clock budget blow-up (O-6, score 12)

- **Mitigation approach**: codify escalation policies + add a per-step budget
  calculator in the harness (not under `harness/`, per HR-17 — handle in
  `harness_cli.py` wrappers).
- **Action items**
  1. Document current escalation thresholds (`max_turns 50→100`, `task_timeout
     600→1200`) in `04-testing/PERF_BUDGETS.md`.
  2. Define stop-the-line rule: if a single step escalates ≥ 2×, the next
     step refuses to re-dispatch and surfaces to Johnny for review.
  3. Add `fr-step-budget` CLI that emits the recommended ceiling based on step
     type, FR tier, and historical median duration.
  4. Validate the rule with FR-01 playback against a deterministic fixture.
- **Owner**: infra-lead.
- **Deadline**: 2026-09-06 (this risk is infra-side; safety-critical ahead of
  P7→P8 transition).
- **Verification**: dry-run replay produces 0 escalations; document signed.
- **Rollback**: rules are advisory; no automation is blocked in absence.
- **Status**: open.

### P-R2 — SQL injection (R2, score 10)

- **Mitigation approach** (SPEC §9): no string concat + ORM / parameterised
  + grep gate (NFR-02).
- **Action items**
  1. `bandit -r src/` for `B608` (SQL injection).
  2. `grep -RIn "execute(" src/` is forbidden unless wrapped by SQLAlchemy text()
     parameters.
  3. CI fails on any raw `execute(f"...{var}...")`.
  4. Quarter-full repo sample audited by sec-lead.
- **Owner**: sec-lead.
- **Deadline**: 2026-09-13.
- **Verification**: bandit 0 HIGH; grep gate exit 0; reviewed repo sample shows
  no offenders.
- **Rollback**: revert of a specific call site — no global rule change.
- **Status**: mitigated, gated.

### P-O5 — `.coverage.*` sentinel pollution at root (O-5, score 10)

- **Mitigation approach**: ignore the artefacts and clean the snapshot.
- **Action items**
  1. Add `.coverage.*` to `.gitignore` (root) and `.gitignore` in
     `03-development/`.
  2. Move existing 8 sentinel files (`git status` output) to a single
     `coverage_archive/` if needed for evidence, or delete.
  3. CI runs `git ls-files | grep -E '^\.coverage\.'` and fails on any hit.
- **Owner**: dba-lead.
- **Deadline**: 2026-09-03 (one-day fix).
- **Verification**: `git status` clean of `.coverage.*`; CI gate clean.
- **Rollback**: removing a `.gitignore` entry restores old behaviour — none
  planned.
- **Status**: open.

---

## 3. MEDIUM Plans (score 9, bundled)

### P-R4 / P-R7 / P-R8 / P-R10 / P-R11 / P-R12 + P-O3 / P-O4

These eight risks score at the threshold and have controls already in place from
Gate 3/Gate 4 evidence. They receive a focused plan so the operator knows the
already-running control, the owner, and the recalibration date.

| Plan | Risk | Existing control | Recalibration action | Owner | Deadline |
|------|------|------------------|----------------------|-------|----------|
| P-R4 | 403 leak (R4) | FR-04 sentinel suite + integration tests | Re-run on next FR-tier incident; document the differential matrix in `06-quality/error_matrix.md` | auth-lead | 2026-09-20 |
| P-R7 | CancelledError swallowed (R7) | `ast-error-handling` lint pin in harness | Re-verify after gate 3 regression fix (already complete) is captured in `linting.txt` line; add runbook entry | runner-lead | 2026-09-20 |
| P-R8 | Orphan subprocess (R8) | `kill()` + `await wait()` test | Add CI run that kills runner mid-task and asserts `wait()` completes | runner-lead | 2026-09-20 |
| P-R10 | Connection-pool exhaustion (R10) | `pool_pre_ping=True` + concurrency limit | Add a soak test that runs 2× configured `pool_size` requests; assert no timeouts | dba-lead | 2026-09-20 |
| P-R11 | Incompatible transitive license (R11) | `pip-licenses --with-system` + lock file | Add CI fail step when allow-list changes by 1+ entry | sec-lead | 2026-09-20 |
| P-R12 | Rate-bucket race (R12) | Row-level lock with single transaction | Add micro-benchmark asserting concurrency clamp under 2× rate | api-lead | 2026-09-27 |
| P-O3 | SSOT scaffold SPEC-token mis-parse (O-3) | `requirements.txt` is filtered | Write a `validate-requirements-tokens` preflight that flags ambiguous lines in SPEC §0/§2; manual cross-check vs `requirements.lock` | infra-lead | 2026-09-27 |
| P-O4 | Lessons without index (O-4) | JSON file store | Add `lessons_index.md` keyed by `dimension` + `phase`; surface top-N each phase | quality-lead | 2026-09-27 |

**Verification per bundled plan**: rerun the named test/tool on or before the
deadline; record the output in `.methodology/quality_manifest.json`
`gate_results.<next-gate>.breakdown.<dim>`.

---

## 4. Cross-Plan Coordination Notes

| Plan(s) | Coordination |
|---------|--------------|
| P-R5, P-R6, P-R9 | All three gate perf and reliability behaviours; coordinate so the next Gate run aggregates changes into a single CI signal flip. |
| P-R3, P-R2 | Both touch security; rotate keys (P-R3) before changing log-verbosity (P-R6) so redaction picks up new code paths. |
| P-O2, P-O1 | Both ingest the same degradation log; sequence O-2 first (smaller blast radius) so O-1's preflight can rely on a stable arch-constraint check. |
| P-O6, P-R5 | Budget tool (O-6) should emit ceilings referenced by the perf regression (R5) check. |
| P-O5 | Independent one-day fix; execute first to keep the snapshot tidy for the rest. |

---

## 5. Escalation Path

| Severity | First responder | Escalate after | Final escalation |
|----------|-----------------|---------------|------------------|
| CRITICAL | Risk-owner (`*-lead`) within 24 h | If unmitigated in 72 h → Johnny | Project sponsor |
| HIGH | Risk-owner within 48 h | If unmitigated in 5 days → Johnny | Project sponsor |
| MEDIUM | Risk-owner within 1 week | If unmitigated in 2 weeks → orch-post | Johnny |

---

## 6. Self-Review

- **What could be wrong**: (a) The threshold-of-9 trigger is generous — almost every
  risk above qualifies — and that may look like a paperwork exercise; the boundary
  case is R12 (6) which we kept in mitigation plans because the SPEC §9 row mapped
  across from the medium band; (b) The owners (`*-lead`) are role names; the
  project has not yet assigned actual humans to them in `state.json`. If real
  allocations differ, the names need to be reconciled in HANDOVER.md; (c) The
  deadlines are calibrated for one Gate cycle (~2 weeks) — if Gate 4 re-runs earlier
  than 2026-09-13 they should be pulled forward; if later, they should be expanded.
- **Unverified assumptions**: that every Gate re-run will surface the named
  verification artefact in the same form; that `degradations.jsonl` continues to
  be append-only (vs rotated); that the importer of this doc is the same project
  load-context used at Gate run-time.
- **Confidence**: **Medium-High**. Plans for SPEC R1–R12 are well-grounded;
  the operational O-* plans are forward-derived from runtime signals, and the
  confidence on those is closer to Medium because we have not yet seen the
  remediation actually clear the gate.
