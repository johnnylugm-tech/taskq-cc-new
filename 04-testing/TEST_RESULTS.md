# Test Results

**Project**: taskq-cc-new
**Phase**: 4 — Testing
**Test scope**: `03-development/tests` (project test tree, NOT repo root — root also holds the vendored harness copy that contributes thousands of framework-own tests)
**Date**: 2026-08-26
**Python**: `/Users/johnny/projects/taskq-cc-new/.venv/bin/python`

## Summary

| Metric | Value |
|---|---|
| Total cases collected | 228 |
| Passed | 228 |
| Failed | 0 |
| Skipped | 0 |
| Errors | 0 |
| Warnings | 1 (pytest-asyncio default-fixture-loop-scope; cosmetic) |
| Wall-clock | 16.46 s |

**Verbatim pytest summary line** (from the run recorded in `04-testing/coverage_raw.txt`):

```
228 passed, 1 warning in 16.46s
```

This matches `cross_artifact.check_test_count_reconciliation`’s expectation (228 cases from the `03-development/tests` tree; the framework-owned harness tests are NOT in scope for this artifact).

## Test inventory by file

| File | FR/NFR scope | Cases |
|---|---|---|
| `03-development/tests/conftest.py` | shared fixture: sys.path bootstrap for `taskq_api.*` imports | 0 (fixture-only) |
| `03-development/tests/test_fr01.py` | FR-01 (Task CRUD API) | (collected; aggregated in 228) |
| `03-development/tests/test_fr02.py` | FR-02 (Task execution endpoint) | (collected; aggregated in 228) |
| `03-development/tests/test_fr03.py` | FR-03 (API Key auth) | (collected; aggregated in 228) |
| `03-development/tests/test_fr04.py` | FR-04 (Scope authz) | (collected; aggregated in 228) |
| `03-development/tests/test_fr05.py` | FR-05 (Rate limiting) | (collected; aggregated in 228) |
| `03-development/tests/test_fr06.py` | FR-06 (Persistence + tx boundaries) | (collected; aggregated in 228) |
| `03-development/tests/test_fr07.py` | FR-07 (Alembic v1→v2→v3 migrations) | (collected; aggregated in 228) |
| `03-development/tests/test_fr08.py` | FR-08 (Async runner / scheduler) | (collected; aggregated in 228) |
| `03-development/tests/test_fr09.py` | FR-09 (Health, readiness, metrics) | (collected; aggregated in 228) |
| `03-development/tests/test_fr10.py` | FR-10 (RFC 7807 error contract) | (collected; aggregated in 228) |
| `03-development/tests/test_coverage_gaps.py` | targeted unit tests for previously-uncovered lines | (collected; aggregated in 228) |
| `03-development/tests/test_property_invariants.py` | property-based invariants across FRs | (collected; aggregated in 228) |
| `03-development/tests/test_nfr_patterns.py` | NFR pattern verifiers (architectural patterns, license, readability) | (collected; aggregated in 228) |
| `03-development/tests/test_nfr_deferred.py` | NFR-01 perf budgets + NFR-12 execute_verification_target deferred verifiers | (collected; aggregated in 228) |
| `03-development/tests/integration/conftest.py` | integration suite fixture | 0 (fixture-only) |
| `03-development/tests/integration/test_app_stack.py` | end-to-end stack drive (API in → storage out) | (collected; aggregated in 228) |
| `03-development/tests/integration/test_rate_repo.py` | rate-limit repository row-level-lock collaboration | (collected; aggregated in 228) |

## Deferred issues

None. No test was skipped, xfailed, or marked expected-failure in this run. The `test_nfr_deferred.py` module name refers to NFR dimensions whose measurement is **deferred to a later tool** (perf budgets NFR-01, execute_verification_target NFR-12) — the verifiers themselves run as concrete tests inside the 228-case collection.

## Command

```bash
cd /Users/johnny/projects/taskq-cc-new && \
  .venv/bin/python -m pytest 03-development/tests \
    --cov=03-development/src --cov-report=term-missing -q \
    | tee 04-testing/coverage_raw.txt
```

The `coverage_raw.txt` artifact next to this file is the verbatim stdout/stderr from the run whose summary line is quoted above. `cross_artifact.check_test_count_reconciliation` may re-parse it at Gate 3 to confirm the 228-case count.