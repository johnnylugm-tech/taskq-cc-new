# TEST_SPEC.md — taskq-api Named Test-Case Catalog (Single Source of Truth)

> **Phase 2 deliverable.** Authored by Agent A (ARCHITECT) Round 1 (2026-08-24).
> Test names from `TEST_INVENTORY.yaml` (v1.2) are preserved verbatim where
> specified. This is the **SINGLE SOURCE OF TRUTH** for all test
> traceability checks at Gates 1-4 (v2.6 unified D4). P3 implements tests
> FROM this catalog — not ad-hoc.

**Project**: taskq-api
**SPEC version**: SPEC.md v1.0.0 (2026-07-30)
**SRS version**: SRS.md APPROVED (2026-08-24)
**SAD version**: SAD.md Round 1 (2026-08-24)
**TEST_INVENTORY.yaml version**: v1.2 (2026-08-24)
**Generated**: 2026-08-24
**Active NFR Patterns**: NP-01, NP-02, NP-03, NP-04, NP-06, NP-07, NP-08, NP-09, NP-10, NP-12, NP-13, NP-15

---

## NFR Pattern Activation (Step 1 + Step 1b + Step 1c Output)

> All 15 patterns enumerated. Trigger column records source:
> `SRS: <keyword>` (Step 1), `SAD: <module>` (Step 1b architecture-risk),
> or `SEC: <threat-id>` (Step 1c threat from SAD §6).

| Pattern | Trigger (SRS keyword / SAD module / SEC threat) | Activated | Where verified in this spec |
|---------|--------------------------------------------------|-----------|-----------------------------|
| NP-01 (auth 401)          | SRS: "X-API-Key" / "401"                         | ✅        | FR-03 happy_path/validation; FR-09 healthz_exempt |
| NP-02 (authz 403)         | SRS: "scope" / "403"                              | ✅        | FR-04 cases 1-3 |
| NP-03 (rate limit 429)    | SRS: "429" / "rate" / "TASKQ_RATE_BURST"          | ✅        | FR-05 cases 2, 4 |
| NP-04 (validation 422)    | SRS: "422" / "validate" / "pydantic"              | ✅        | FR-01 case 2; FR-10 cross |
| NP-05 (idempotency)       | not in SPEC; not a stated requirement            | ❌        | — |
| NP-06 (latency SLA)       | SRS: "p95" / "performance" (NFR-01)               | ✅        | NFR Integration → NP-06 case |
| NP-07 (dependency fault)  | SAD: repository/session (DB); migrations/versions | ✅        | FR-09 case 2; FR-07 case 1; NFR Integration → NP-07 case |
| NP-08 (security attack)   | SRS: "SQL injection" / "shell=True" / NFR-02      | ✅        | FR-02 case 2; FR-06 case 3; NFR Deferred |
| NP-09 (audit log)         | SRS: "redact" / "REDACTED" / NFR-04               | ✅        | FR-02 case 5; NFR Deferred |
| NP-10 (data round-trip)   | SPEC: "round-trip reversibility" FR-07 v3        | ✅        | FR-07 case 3 + Properties P7 |
| NP-11 (backward compat)   | not applicable (round-1 SPEC, no prior version)  | ❌        | — |
| NP-12 (pagination)        | SRS: "cursor-based" / "limit" FR-01               | ✅        | FR-01 cases 4, 5 |
| NP-13 (concurrency)       | SAD: repository/session (shared tx); rate_repo    | ✅        | FR-05 case 3; FR-08 case 3 |
| NP-14 (encryption)        | not in SPEC (API key only SHA-256, not AES)       | ❌        | — |
| NP-15 (timeout)           | SAD: service/runner (subprocess); FR-08 wait_for | ✅        | FR-02 case 3; FR-08 case 4 |

---

## Functional Requirement Test Cases

> Each FR section preserves the authoritative test function names from
> `TEST_INVENTORY.yaml` verbatim. `Inputs` cells use `key="value"` pairs with
> literal (true-form) values, NOT pytest-id underscore form. Sub-assertions
> use Python-expression syntax. `Properties` (Direction B) is opt-in for FRs
> with a clean algebraic invariant; the cell text is itself a valid Python
> expression (predicate engine checks it).

### FR-01: Task resource CRUD API (POST/GET/LIST/DELETE on `/v1/tasks`, cursor pagination)

**Classification**: API_ENDPOINT
**Active Patterns**: NP-04 (422 validation), NP-12 (cursor pagination)
**Acceptance criteria (SRS.md §3 FR-01)**: AC-1.1, AC-1.2, AC-1.3, AC-1.4, AC-1.5, AC-1.6

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_create_task_valid` | scope="write"; body_command="echo hello"; body_name="t-create-1"; expected_status="201"; observed_id_value="00000000-0000-0000-0000-000000abcdef" | happy_path | Q1 |
| 2 | `test_post_invalid_body_returns_422` | scope="write"; body_command=""; body_name=""; observed_status_code="422"; observed_content_type="application/problem+json" | validation | Q2 |
| 3 | `test_get_unknown_returns_404` | scope="read"; path_id="00000000-0000-0000-0000-000000000000"; observed_status_code="404"; observed_content_type="application/problem+json" | validation | Q2 |
| 4 | `test_list_uses_cursor_pagination` | scope="read"; seed_count="75"; query_limit="50"; first_page_size="50"; second_page_size="25"; cursor_header_field="next_cursor" | happy_path | Q1 |
| 5 | `test_limit_default_and_upper_bound` | limit_default="50"; limit_max="200"; limit_above="201"; observed_status_code="422" | boundary | Q3 |
| 6 | `test_delete_removes_task_and_results_in_tx` | scope="admin"; seed_results_count="3"; expected_rows_after_delete="0"; tx_mode="single" | happy_path | Q1 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-id-present | `len(observed_id_value) == 36` | 1 |
| AC1-status-201 | `expected_status == "201"` | 1 |
| AC2-content-type | `observed_content_type == "application/problem+json"` | 2 |
| AC3-content-type | `observed_content_type == "application/problem+json"` | 3 |
| AC4-page-size | `first_page_size == "50"` | 4 |
| AC4-second-page | `to_int(first_page_size) + to_int(second_page_size) == to_int(seed_count)` | 4 |
| AC5-limit-default | `limit_default == "50"` | 5 |
| AC5-limit-rejection | `observed_status_code == "422"` | 5 |
| AC6-tx-atomic | `expected_rows_after_delete == "0"` | 6 |

**Properties** — Direction B is opt-in. FR-01 lacks a clean algebraic
invariant (CRUD correctness is per-row, not per-equation). **Skipped.**

---

### FR-02: Task execution endpoint (POST `/v1/tasks/{id}/run` → 202 + `run_id`)

**Classification**: API_ENDPOINT (multi-scenario state machine inside)
**Active Patterns**: NP-08 (no shell=True), NP-15 (timeout kills child), NP-13 (TaskGroup concurrency)
**Acceptance criteria (SRS.md §3 FR-02)**: AC-2.1, AC-2.2, AC-2.3, AC-2.4, AC-2.5, AC-2.6

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_run_returns_202_with_run_id` | scope="write"; seed_command="echo hi"; expected_status="202"; observed_run_id_value="11111111-2222-3333-4444-555555555555"; run_id_len="36" | happy_path | Q1 |
| 2 | `test_subprocess_uses_exec_no_shell_true` | scope="write"; seed_command="echo hi"; source_path="03-development/src/taskq_api/service/runner.py"; shell_true_hits="0"; subprocess_mode="in_process" | validation | Q2 |
| 3 | `test_per_task_timeout_equals_task_timeout` | scope="write"; seed_command="sleep 60"; taskq_task_timeout="2.0"; observed_status_name="timeout"; subprocess_mode="out_of_process"; shared_TASKQ_HOME=false | validation | Q2 |
| 4 | `test_state_machine_pending_running_done_failed_timeout` | initial_status="pending"; trigger_signal="execute"; observed_status="running" | state_transition | Q4 |
| 5 | `test_state_machine_pending_running_done_failed_timeout` | initial_status="running"; exit_code_value="0"; observed_status="done" | state_transition | Q4 |
| 6 | `test_state_machine_pending_running_done_failed_timeout` | initial_status="running"; exit_code_value="1"; observed_status="failed" | state_transition | Q4 |
| 7 | `test_state_machine_pending_running_done_failed_timeout` | initial_status="running"; child_command="sleep 60"; timeout_seconds="2"; observed_status="timeout" | state_transition | Q4 |
| 8 | `test_state_machine_pending_running_done_failed_timeout` | cancel_signal="asyncio.CancelledError"; observed_status="pending"; cancelled_error_propagated="True" | state_transition | Q4 |
| 9 | `test_results_written_to_task_results_table` | scope="write"; seed_command="echo done"; expected_exit_code="0"; expected_stdout_tail="done\n"; redaction_pattern="token="; redacted_marker="[REDACTED]" | happy_path | Q1 |
| 10 | `test_list_runs_newest_to_oldest` | scope="read"; seed_runs_count="5"; first_item_index="0"; first_run_id_max_age="True" | happy_path | Q1 |

> **State-machine sub-rows (FR-05 P3 2026-07-16 lesson)**: AC-2.4 enumerates
> 5 distinct terminal/intermediate states. Each is its own case row with
> its own Inputs and Expected. One test function, 5 parametrize cases.

> **Subprocess isolation**: cases 2, 3, 4d, 4e spawn subprocesses via
> `asyncio.create_subprocess_exec`; `subprocess_mode` and `shared_TASKQ_HOME`
> are declared explicitly. `out_of_process` cases propagate `PYTHONPATH`
> to the child env because pytest `pythonpath` config does not inherit.

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-status-202 | `expected_status == "202"` | 1 |
| AC1-run-id-shape | `len(observed_run_id_value) == 36` | 1 |
| AC2-shell-zero | `shell_true_hits == "0"` | 2 |
| AC3-timeout-status | `observed_status_name == "timeout"` | 3 |
| AC4-pending-running | `observed_status == "running"` | 4 |
| AC5-running-done | `observed_status == "done"` | 5 |
| AC6-running-failed | `observed_status == "failed"` | 6 |
| AC7-running-timeout | `observed_status == "timeout"` | 7 |
| AC8-cancel-propagates | `cancelled_error_propagated == "True"` | 8 |
| AC9-exit-zero | `expected_exit_code == "0"` | 9 |
| AC9-redaction | `redacted_marker == "[REDACTED]"` | 9 |
| AC10-newest-first | `first_run_id_max_age == "True"` | 10 |

---

### FR-03: API-key authentication (`X-API-Key` header, SHA-256 hashed)

**Classification**: SECURITY_CONTROL
**Active Patterns**: NP-01 (401 on missing/invalid)
**Acceptance criteria (SRS.md §3 FR-03)**: AC-3.1, AC-3.2, AC-3.3, AC-3.4, AC-3.5, AC-3.6

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_missing_or_invalid_api_key_returns_401` | header_x_api_key_present="False"; observed_status_code="401"; observed_content_type="application/problem+json" | validation | Q2 |
| 2 | `test_keys_stored_as_sha256_hash` | plaintext_key="tk_supersecret_1234"; expected_hash_len="64"; expected_hash_charset="hex" | happy_path | Q1 |
| 3 | `test_compare_uses_hmac_compare_digest` | source_path="03-development/src/taskq_api/service/auth.py"; compare_digest_hits="1"; naive_eq_hits="0" | validation | Q2 |
| 4 | `test_key_create_prints_plaintext_exactly_once` | scope="admin"; cli_command="python -m taskq_api key create --scope read"; stdout_token_count="1"; persisted_plaintext_hits="0" | happy_path | Q1 |
| 5 | `test_revoked_key_treated_as_invalid` | key_state="revoked"; expected_status_code="401" | validation | Q2 |
| 6 | `test_healthz_and_readyz_require_no_auth` | endpoint_path="/healthz"; header_x_api_key_present="False"; observed_status_code="200" | happy_path | Q1 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-status-401 | `observed_status_code == "401"` | 1 |
| AC1-content-type | `observed_content_type == "application/problem+json"` | 1 |
| AC2-hash-len | `expected_hash_len == "64"` | 2 |
| AC2-hash-charset | `expected_hash_charset == "hex"` | 2 |
| AC3-compare-digest-used | `compare_digest_hits == "1"` | 3 |
| AC3-naive-eq-absent | `naive_eq_hits == "0"` | 3 |
| AC4-stdout-once | `stdout_token_count == "1"` | 4 |
| AC4-persist-none | `persisted_plaintext_hits == "0"` | 4 |
| AC5-revoked-401 | `expected_status_code == "401"` | 5 |
| AC6-no-auth-200 | `observed_status_code == "200"` | 6 |

---

### FR-04: Scope authorization (`read < write < admin`, single dependency, 403 body no-leak)

**Classification**: SECURITY_CONTROL
**Active Patterns**: NP-02 (403 on insufficient scope), NP-04 (problem+json)
**Acceptance criteria (SRS.md §3 FR-04)**: AC-4.1, AC-4.2, AC-4.3

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_scope_hierarchy_read_lt_write_lt_admin` | read_satisfies_admin="False"; write_satisfies_read="True"; admin_satisfies_read="True"; admin_satisfies_write="True" | happy_path | Q1 |
| 2 | `test_insufficient_scope_returns_403_without_leak` | caller_scope="write"; required_scope="admin"; path_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"; observed_status_code="403"; leak_keyword="not_found"; leak_present="False" | validation | Q2 |
| 3 | `test_single_fastapi_dependency_for_authz` | app_module="taskq_api.app"; dependency_function="require_scope"; v1_route_count="7" | happy_path | Q1 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-read-not-admin | `read_satisfies_admin == "False"` | 1 |
| AC1-write-satisfies-read | `write_satisfies_read == "True"` | 1 |
| AC1-admin-satisfies-read | `admin_satisfies_read == "True"` | 1 |
| AC1-admin-satisfies-write | `admin_satisfies_write == "True"` | 1 |
| AC2-status-403 | `observed_status_code == "403"` | 2 |
| AC2-no-leak | `leak_present == "False"` | 2 |
| AC3-route-coverage | `v1_route_count == "7"` | 3 |

**Properties** — monotonicity of scope hierarchy.

| property_id | invariant | applies_to (case #) |
|---|---|---|
| P4-admin-superset-write | `scope_satisfies("admin", "write")` | 1 |
| P4-write-superset-read | `scope_satisfies("write", "read")` | 1 |
| P4-read-not-superset-write | `not scope_satisfies("read", "write")` | 1 |

> Property `scope_satisfies` is a spec-level function (Python-expression
> syntax). The runtime equivalent lives at `taskq_api.service.auth.scope_satisfies`.

---

### FR-05: Rate limiting (per-token token bucket, 429 + Retry-After)

**Classification**: STATE_MACHINE (token bucket state over time)
**Active Patterns**: NP-03 (429 on exceed), NP-13 (row-level lock concurrency)
**Acceptance criteria (SRS.md §3 FR-05)**: AC-5.1, AC-5.2, AC-5.3, AC-5.4

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_token_bucket_capacity_and_refill_rate` | burst_capacity="20"; refill_per_sec="5.0"; initial_tokens="20"; after_burn_20="0"; after_wait_1s="5" | happy_path | Q1 |
| 2 | `test_exceed_bucket_returns_429_with_retry_after` | burst_capacity="3"; requests_count="5"; expected_429_count="2"; retry_after_header_field="Retry-After"; retry_after_positive="True" | validation | Q2 |
| 3 | `test_bucket_update_uses_row_level_lock` | source_path="03-development/src/taskq_api/repository/rate_repo.py"; with_for_update_hits="1"; state_mode="isolate_per_test" | validation | Q2 |
| 4 | `test_healthz_and_readyz_not_rate_limited` | endpoint_path="/healthz"; requests_count="100"; observed_status_codes_all="200" | happy_path | Q1 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-initial-cap | `initial_tokens == burst_capacity` | 1 |
| AC1-refill-rate | `after_wait_1s == "5"` | 1 |
| AC2-429-count | `expected_429_count == "2"` | 2 |
| AC2-retry-after-pos | `retry_after_positive == "True"` | 2 |
| AC3-row-lock | `with_for_update_hits == "1"` | 3 |
| AC4-no-429-on-health | `observed_status_codes_all == "200"` | 4 |

**Properties** — conservation of bucket capacity.

| property_id | invariant | applies_to (case #) |
|---|---|---|
| P5-bucket-bounded | `tokens_after_consume(bucket, n) >= 0` | 1, 2 |
| P5-bucket-cap | `tokens_after_consume(bucket, 0) <= burst_capacity` | 1 |

> `tokens_after_consume` and `burst_capacity` are spec-level symbols;
> the runtime binds them via `taskq_api.service.ratelimit`.

---

### FR-06: Persistence layer + transaction boundaries (repository only, no string-concat SQL)

**Classification**: DATA_ENTITY
**Active Patterns**: NP-08 (no SQL string concat), NP-13 (one Session per request)
**Acceptance criteria (SRS.md §3 FR-06)**: AC-6.1, AC-6.2, AC-6.3, AC-6.4, AC-6.5

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_all_data_access_via_repository_layer` | source_glob="03-development/src/taskq_api/{service,api}/**/*.py"; sqlalchemy_imports_in_service="0"; sqlalchemy_imports_in_api="0" | happy_path | Q1 |
| 2 | `test_one_session_per_request_with_context_manager` | endpoint_path="/v1/tasks"; active_session_count="1"; exception_role="rollback" | happy_path | Q1 |
| 3 | `test_no_string_concat_sql_uses_orm_or_param` | source_glob="03-development/src/taskq_api/**/*.py"; fstring_sql_hits="0"; pct_sql_hits="0"; plus_sql_hits="0" | validation | Q2 |
| 4 | `test_selectinload_or_joinedload_constant_sql_count` | rows_seeded="100"; emitted_statement_count="3"; observed_row_count="100" | boundary | Q3 |
| 5 | `test_pool_size_and_pool_pre_ping` | observed_pool_size="5"; pool_pre_ping_enabled="True" | happy_path | Q1 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-service-no-orm | `sqlalchemy_imports_in_service == "0"` | 1 |
| AC1-api-no-orm | `sqlalchemy_imports_in_api == "0"` | 1 |
| AC2-one-session | `active_session_count == "1"` | 2 |
| AC3-fstring-zero | `fstring_sql_hits == "0"` | 3 |
| AC3-pct-zero | `pct_sql_hits == "0"` | 3 |
| AC3-plus-zero | `plus_sql_hits == "0"` | 3 |
| AC4-constant-sql | `emitted_statement_count == "3"` | 4 |
| AC4-rows-preserved | `observed_row_count == "100"` | 4 |
| AC5-pool-size | `observed_pool_size == "5"` | 5 |
| AC5-pre-ping | `pool_pre_ping_enabled == "True"` | 5 |

---

### FR-07: Schema migration (Alembic v1 → v2 → v3, real SQLite, round-trip reversibility)

**Classification**: DATA_ENTITY (stateful migration, shared SQLite file)
**Active Patterns**: NP-10 (data round-trip), NP-07 (migration fault)
**Acceptance criteria (SRS.md §3 FR-07)**: AC-7.1, AC-7.2, AC-7.3, AC-7.4, AC-7.5

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_upgrade_head_succeeds_against_real_sqlite` | db_path="/tmp/taskq_upgrade_test.db"; state_mode="isolate_per_test"; initial_revision="base"; target_revision="head"; observed_revision="head"; expected_tables="tasks,api_keys,tags,task_tags,task_results,rate_buckets,alembic_version" | happy_path | Q1 |
| 2 | `test_downgrade_base_no_residual_tables` | db_path="/tmp/taskq_downgrade_test.db"; state_mode="isolate_per_test"; start_revision="head"; target_revision="base"; residual_tables="alembic_version" | happy_path | Q1 |
| 3 | `test_round_trip_reversibility_v3_data_move` | db_path="/tmp/taskq_round_trip.db"; state_mode="isolate_per_test"; precondition="upgrade head, insert sample tasks with result_json, run v3 upgrade, downgrade -1, upgrade head, compare row-by-row"; expected_row_match="True" | integration | Q7 |
| 4 | `test_no_destructive_shortcuts_in_downgrade` | source_glob="03-development/migrations/versions/*.py"; drop_table_hits="0"; drop_column_hits="0"; execute_drop_hits="0" | validation | Q2 |
| 5 | `test_each_migration_covered_by_offline_sql_assert` | migration_files="v1_initial.py,v2_tags.py,v3_split_results.py"; observed_coverage="3/3" | happy_path | Q1 |

> **Stateful isolation declaration**: all cases share a real SQLite file
> (`db_path`). `state_mode="isolate_per_test"` mandates function-scoped
> fixtures (NOT module-scope) so each test starts with an empty file.

> **Spec ambiguity resolution**: case 3 has an Inputs precondition field
> that reconstructs the multi-step scenario because the AC prose ("upgrade
> head → write sample → downgrade -1 → upgrade head") cannot be expressed
> as a single-key Inputs cell. This explicit declaration satisfies the
> spec-ambiguity protocol.

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-revision-head | `observed_revision == "head"` | 1 |
| AC1-tables-present | `expected_tables == "tasks,api_keys,tags,task_tags,task_results,rate_buckets,alembic_version"` | 1 |
| AC2-only-alembic-left | `residual_tables == "alembic_version"` | 2 |
| AC3-row-match | `expected_row_match == "True"` | 3 |
| AC4-drop-absent | `drop_table_hits == "0"` | 4 |
| AC4-execute-drop-absent | `execute_drop_hits == "0"` | 4 |
| AC5-coverage | `observed_coverage == "3/3"` | 5 |

**Properties** — the load-bearing algebraic invariant for FR-07.

| property_id | invariant | applies_to (case #) |
|---|---|---|
| P7-v3-roundtrip | `downgrade_then_upgrade(sample_task_row) == sample_task_row` | 3 |
| P7-downgrade-no-data-loss | `count(rows_after_downgrade) == count(rows_before_downgrade)` | 3 |

> `downgrade_then_upgrade` is the spec-level composite function the test
> performs; the same name MUST appear in the property-based test
> implementation (`hypothesis.given(...)`).
>
> Note: a `P7-upgrade-idempotent` invariant (`upgrade_head(upgrade_head(db)) == upgrade_head(db)`)
> was considered but is omitted here because case 1 only runs `upgrade head`
> once. Adding the property would require a new test case that performs two
> sequential `alembic upgrade head` invocations and asserts the second is a
> no-op; deferred until such a case is introduced in a future round.

---

### FR-08: Async runner (`asyncio.TaskGroup`, graceful drain, timeout kills child)

**Classification**: STATE_MACHINE (idle → running → draining → interrupted)
**Active Patterns**: NP-13 (concurrency cap), NP-15 (timeout), NP-07 (CancelledError)
**Acceptance criteria (SRS.md §3 FR-08)**: AC-8.1, AC-8.2, AC-8.3, AC-8.4, AC-8.5

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_background_uses_asyncio_task_group` | source_path="03-development/src/taskq_api/service/runner.py"; task_group_hits="1"; gather_hits="0" | happy_path | Q1 |
| 2 | `test_drain_waits_for_inflight_with_budget` | inflight_tasks="3"; drain_timeout="10.0"; observed_completed_count="3"; observed_interrupted_count="0" | state_transition | Q4 |
| 3 | `test_concurrency_cap_queues_surplus` | max_concurrent="2"; submitted_count="5"; observed_running_peak="2"; observed_queued_count="3"; state_mode="isolate_per_test" | boundary | Q3 |
| 4 | `test_wait_for_kills_child_no_orphan` | child_command="sleep 60"; timeout_seconds="2"; subprocess_mode="out_of_process"; shared_TASKQ_HOME=false; orphan_pid_count="0" | fault_injection | Q5 |
| 5 | `test_cancelled_error_propagates_not_swallowed` | cancel_signal="asyncio.CancelledError"; re_raised="True"; swallowed_by_except_exception="False" | validation | Q2 |

> **Subprocess isolation**: cases 2, 4 invoke `wait_for` on child
> subprocesses; `subprocess_mode="out_of_process"` + `shared_TASKQ_HOME=false`
> forces per-test subprocess teardown so an orphan from one test cannot
> leak into another.

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-task-group | `task_group_hits == "1"` | 1 |
| AC1-no-gather | `gather_hits == "0"` | 1 |
| AC2-all-completed | `observed_completed_count == "3"` | 2 |
| AC2-no-interrupt | `observed_interrupted_count == "0"` | 2 |
| AC3-cap-respected | `observed_running_peak == "2"` | 3 |
| AC3-surplus-queued | `observed_queued_count == "3"` | 3 |
| AC4-no-orphan | `orphan_pid_count == "0"` | 4 |
| AC5-cancelled-reraised | `re_raised == "True"` | 5 |
| AC5-not-swallowed | `swallowed_by_except_exception == "False"` | 5 |

**Properties** — drain-budget invariant.

| property_id | invariant | applies_to (case #) |
|---|---|---|
| P8-drain-budget | `drain_elapsed_seconds <= drain_timeout` | 2 |
| P8-cancel-pure | `cancelled_error_propagated == "True"` | 5 |

> `drain_elapsed_seconds` is a spec-level variable; tests assert the
> runtime measured value. The orphan-free contract for FR-08 is fully
> covered by case 4's `AC4-no-orphan: orphan_pid_count == "0"`
> sub-assertion, so no separate property row is needed.

---

### FR-09: Health and observability (`/healthz`, `/readyz` fail-closed, `/v1/metrics` admin)

**Classification**: API_ENDPOINT
**Active Patterns**: NP-07 (DB down → 503)
**Acceptance criteria (SRS.md §3 FR-09)**: AC-9.1, AC-9.2, AC-9.3, AC-9.4

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_healthz_returns_200_ok` | endpoint_path="/healthz"; expected_status_code="200"; expected_body_field="status"; expected_body_value="ok" | happy_path | Q1 |
| 2 | `test_readyz_checks_db_and_migration_head` | endpoint_path="/readyz"; db_state="up"; migration_revision="head"; expected_status_code="200"; state_mode="isolate_per_test" | happy_path | Q1 |
| 3 | `test_readyz_fails_closed_on_old_migration` | endpoint_path="/readyz"; migration_revision="v1"; expected_status_code="503"; detail_mentions="migration" | fault_injection | Q5 |
| 4 | `test_metrics_requires_admin_scope` | endpoint_path="/v1/metrics"; caller_scope="read"; expected_status_code="403" | validation | Q2 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-status-200 | `expected_status_code == "200"` | 1 |
| AC1-body-ok | `expected_body_value == "ok"` | 1 |
| AC2-ready-200 | `expected_status_code == "200"` | 2 |
| AC3-fail-closed-503 | `expected_status_code == "503"` | 3 |
| AC3-detail-names-cause | `detail_mentions == "migration"` | 3 |
| AC4-metrics-403 | `expected_status_code == "403"` | 4 |

---

### FR-10: Error contract RFC 7807 (`application/problem+json`, correlation_id)

**Classification**: SECURITY_CONTROL
**Active Patterns**: NP-04 (validation 422)
**Acceptance criteria (SRS.md §3 FR-10)**: AC-10.1, AC-10.2, AC-10.3, AC-10.4, AC-10.5

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_non_2xx_content_type_problem_json` | response_status="422"; observed_content_type="application/problem+json" | validation | Q2 |
| 2 | `test_problem_body_has_required_fields` | required_fields="type,title,status,detail,instance,correlation_id"; field_count="6" | happy_path | Q1 |
| 3 | `test_detail_never_contains_internal_structure` | detail_value="Internal server error"; forbidden_substring_stack="Traceback"; forbidden_substring_sql="SELECT"; forbidden_substring_path="/usr/src" | validation | Q2 |
| 4 | `test_correlation_id_in_header_and_log` | response_header_field="X-Correlation-Id"; log_line_pattern="correlation_id="; header_value_matches_log="True" | happy_path | Q1 |
| 5 | `test_error_code_mapping_matches_spec` | mapping_pairs="422,validation;401,unauthenticated;403,forbidden;404,not-found;409,conflict;429,rate-limited;503,not-ready;500,internal"; observed_pairs="8/8" | happy_path | Q1 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-content-type | `observed_content_type == "application/problem+json"` | 1 |
| AC2-field-count | `field_count == "6"` | 2 |
| AC3-no-stack | `forbidden_substring_stack == "Traceback"` | 3 |
| AC3-no-sql | `forbidden_substring_sql == "SELECT"` | 3 |
| AC3-no-path | `forbidden_substring_path == "/usr/src"` | 3 |
| AC4-header-log-match | `header_value_matches_log == "True"` | 4 |
| AC5-mapping-complete | `observed_pairs == "8/8"` | 5 |

**Properties** — factory idempotence.

| property_id | invariant | applies_to (case #) |
|---|---|---|
| P10-factory-deterministic | `problem_response(status=422, type_uri="/errors/validation", detail="bad input")["title"] == "validation"` | 2 |
| P10-type-uri-stable | `problem_response(status=422, type_uri="/errors/validation", detail="x")["type"] == "/errors/validation"` | 2 |

---

## Cross-Cutting Test Cases

### NFR Integration (verifier role: TEST_SPEC defines the Inputs + Sub-assertions)

> NP-06 (latency SLA) and NP-07 (dependency fault) are the patterns whose
> integration-level verification lives here. All other NFRs are deferred to
> downstream phases (see next section) because their verification is owned
> by Unit/Static/Framework/Deployment tooling, not by an integration test.

#### NP-06 / NFR-01: Latency SLA on list/get endpoints

**Classification**: INTEGRATION

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_nfr01_perf_p95_get_by_id` | seed_rows="10000"; endpoint_path="/v1/tasks/{id}"; sample_count="200"; p95_threshold_ms="30"; precondition="warm cache before sampling"; state_mode="isolate_per_test" | nfr_pattern | Q6 |
| 2 | `test_nfr01_perf_p95_list_limit_50` | seed_rows="10000"; endpoint_path="/v1/tasks"; query_limit="50"; sample_count="200"; p95_threshold_ms="80" | nfr_pattern | Q6 |
| 3 | `test_nfr01_constant_sql_count_event_listener` | seed_rows="10000"; endpoint_path="/v1/tasks"; query_limit="50"; observed_statement_count="3" | nfr_pattern | Q6 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-p95-get | `p95_measured_ms <= p95_threshold_ms` | 1 |
| AC2-p95-list | `p95_measured_ms <= p95_threshold_ms` | 2 |
| AC3-constant-sql | `observed_statement_count == "3"` | 3 |

#### NP-07 / NFR-03 + NFR-10: Dependency fault + integration coverage floor

**Classification**: INTEGRATION

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_nfr03_readyz_503_on_db_failure` | db_state="down"; endpoint_path="/readyz"; expected_status_code="503"; detail_mentions="db" | fault_injection | Q5 |
| 2 | `test_nfr10_integration_coverage_ge_80` | coverage_threshold_pct="80"; observed_integration_cov_pct="≥80" | nfr_pattern | Q6 |
| 3 | `test_nfr10_integration_driven_by_asgi_transport` | driver_module="httpx.AsyncClient"; transport_class="ASGITransport"; direct_handler_call_hits="0" | nfr_pattern | Q6 |
| 4 | `test_nfr10_integration_covers_full_error_code_set` | error_code_set="401,403,404,409,422,429,503"; covered_count="7/7" | nfr_pattern | Q6 |

**Sub-assertions**

| rule_id | predicate | applies_to (case #) |
|---|---|---|
| AC1-readyz-503 | `expected_status_code == "503"` | 1 |
| AC1-detail-names-cause | `detail_mentions == "db"` | 1 |
| AC2-cov-floor | `observed_integration_cov_pct == "≥80"` | 2 |
| AC3-asgi-driver | `direct_handler_call_hits == "0"` | 3 |
| AC4-error-coverage | `covered_count == "7/7"` | 4 |

---

### Deferred to Downstream Phases

> All other NFRs are owned by Unit / Static / Framework / Deployment
> tooling. They do NOT require concrete Inputs/Sub-assertions in this
> spec; the unit-test layer mirrors the test names verbatim. Required by
> `check-test-spec-consistency` so P3 Agent C/D cannot accidentally re-host
> them as integration cases.

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-01 | test_nfr01_perf_p95_get_by_id | unit | p95 latency on single-fetch (latency SLA, owned by pytest-benchmark) |
| 2 | NFR-01 | test_nfr01_perf_p95_list_limit_50 | unit | p95 latency on list-fetch (latency SLA) |
| 3 | NFR-01 | test_nfr01_constant_sql_count_event_listener | unit | constant SQL statement count via event listener |
| 4 | NFR-02 | test_no_shell_true_eval_exec_in_source | unit | grep gate: no shell=True / eval( / exec( |
| 5 | NFR-02 | test_no_string_concat_sql_in_source | unit | grep gate: no SQL string concatenation |
| 6 | NFR-02 | test_keys_hashed_and_compared_with_hmac_compare_digest | unit | API key hashing + constant-time compare |
| 7 | NFR-02 | test_403_body_does_not_leak_resource_existence | unit | 403 body does not leak resource existence |
| 8 | NFR-02 | test_error_body_no_stack_sql_or_paths | unit | problem+json detail excludes internal fragments |
| 9 | NFR-02 | test_cors_denies_by_default_allowlist_only | unit | CORS deny-by-default with TASKQ_CORS_ORIGINS allowlist |
| 10 | NFR-02 | test_bandit_zero_high_zero_medium | unit | bandit returns 0 HIGH / 0 MEDIUM |
| 11 | NFR-03 | test_per_request_tx_boundary_commit_or_rollback | unit | per-request transaction commits or rolls back |
| 12 | NFR-03 | test_no_bare_except_or_except_exception_pass | unit | no bare except / except Exception: pass |
| 13 | NFR-03 | test_cancelled_error_is_reraised | unit | asyncio.CancelledError is re-raised, not swallowed |
| 14 | NFR-03 | test_readyz_503_on_db_failure | unit | /readyz returns 503 when DB is unreachable |
| 15 | NFR-03 | test_task_timeout_terminates_child_no_orphan | unit | subprocess is killed on timeout (no orphan) |
| 16 | NFR-03 | test_failed_migration_rolls_back_to_previous_revision | unit | failed alembic upgrade rolls back transaction |
| 17 | NFR-04 | test_sensitive_lines_replaced_with_redacted | unit | redaction filter masks sk-/token=/Bearer/postgres:// |
| 18 | NFR-04 | test_db_url_password_absent_from_logs_and_metrics | unit | DB URL password absent from logs and /v1/metrics body |
| 19 | NFR-04 | test_key_plaintext_printed_once_and_not_persisted | unit | key plaintext printed exactly once, never persisted |
| 20 | NFR-05 | test_public_fn_class_docstrings_have_fr_or_nfr_ref | unit | 100% public fn/class docstrings include [FR-XX]/[NFR-XX] |
| 21 | NFR-05 | test_openapi_summary_and_description_populated | unit | every OpenAPI route has summary + description |
| 22 | NFR-06 | test_importlinter_exists_with_layers_contract | unit | .importlinter declares api > service > repository > models |
| 23 | NFR-06 | test_sqlalchemy_forbidden_outside_repository | unit | sqlalchemy forbidden outside repository/ layer |
| 24 | NFR-06 | test_lint_imports_exits_zero | unit | lint-imports exits 0 |
| 25 | NFR-06 | test_no_contract_weakening_or_ignore_imports_wildcards | unit | no .importlinter weakening via wildcards / ignore_imports |
| 26 | NFR-07 | test_runtime_deps_pinned_with_eq_eq | unit | requirements.txt pins all runtime deps with == |
| 27 | NFR-07 | test_dependency_license_in_allowlist | unit | every license ∈ allowlist (MIT/BSD/Apache/PSF) |
| 28 | NFR-07 | test_pip_licenses_with_system_full_tree | unit | pip-licenses --with-system reports full-tree compliance |
| 29 | NFR-07 | test_sbom_at_08_config_with_required_schema | unit | 08-config/SBOM.json schema conforms to AC-N7.4 (SBOM schema fields: name/version/license/direct|transitive) |
| 30 | NFR-08 | test_mutation_testing_feature_enabled_in_harness_config | framework | .methodology/harness_config.json enables mutation_testing |
| 31 | NFR-08 | test_mutation_score_ge_70 | framework | mutmut score ≥ 70 |
| 32 | NFR-08 | test_scope_limited_to_service_and_repository | framework | mutation scope limited to service/ + repository/ |
| 33 | NFR-09 | test_pytest_skipped_zero | unit | pytest -q reports 0 skipped |
| 34 | NFR-09 | test_zero_assert_zero | unit | every test function has ≥ 1 assert |
| 35 | NFR-09 | test_no_test_exclusions_via_ignore_deselect_or_testpaths | unit | no --ignore/-k/--deselect/collect_ignore/testpaths removal |
| 36 | NFR-09 | test_fr07_migration_real_sqlite_round_trip | unit | FR-07 round-trip uses real SQLite file (not mock) |
| 37 | NFR-09 | test_verified_status_only_after_test_passes | unit | VERIFIED status only after cited test passes |
| 38 | NFR-11 | test_project_mi_ge_80 | unit | project MI (LLOC weighted) ≥ 80 |
| 39 | NFR-11 | test_single_function_cc_le_10 | unit | single-function cyclomatic complexity ≤ 10 |
| 40 | NFR-11 | test_file_and_directory_size_limits | unit | file ≤ 400 lines, dir ≤ 15 files |
| 41 | NFR-11 | test_api_handler_le_40_lines | unit | API handler ≤ 40 lines |
| 42 | NFR-12 | test_verify_system_chains_alembic_tests_smoke_round_trip | deployment | Makefile verify-system chains alembic + tests + smoke + round-trip |
| 43 | NFR-12 | test_verify_system_exit_zero_prints_pass | deployment | make verify-system exits 0 and prints "verify-system: PASS" |

### Deployment Smoke

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_app_starts_and_health_endpoint_returns_200` | smoke | deployment |

---

## Summary

| Metric | Count |
|---|---|
| FRs covered | 10 (FR-01..FR-10) |
| Total test cases (FR-level, including state-machine sub-rows) | 53 |
| By type: happy_path | 25 |
| By type: validation/failure | 16 |
| By type: boundary | 3 |
| By type: state_transition | 6 |
| By type: fault_injection | 2 |
| By type: integration | 1 (FR-07 case 3) |
| By type: nfr_pattern | 0 (placed under NFR Integration instead) |
| FRs with `**Properties**` (Direction B) | 5 (FR-04, FR-05, FR-07, FR-08, FR-10) |
| Property invariants declared | 11 |
| Active NFR patterns applied | 12 (NP-01..04, NP-06..10, NP-12, NP-13, NP-15) |
| NFR Integration verifier cases | 7 (NP-06: 3, NP-07/NFR-10: 4) |
| Deferred-to-downstream NFR rows | 43 |

> The FR-level count (53) and the NFR Integration count (7) are the cases
> this spec drives in P3. The Deferred-to-Downstream table preserves
> names from `TEST_INVENTORY.yaml` so the P3 mirror gate can match them
> in the unit/static/framework/deployment test files.

---

## Deferred NFR Acceptance Criteria

> Each NFR acceptance criterion below is verified by a downstream tool or
> layer (unit / framework / deployment / NFR Integration verifier) — NOT
> by a TEST_SPEC case. Format follows `ac_deferral_shape()` from
> `harness/core/quality_gate/artifact_consistency.py`:
> `Deferred: AC-Nx.y — <which downstream phase or which tool verifies
> this>, not a TEST_SPEC case.`

Deferred: AC-N1.1 — NFR Integration verifier (test_nfr01_perf_p95_get_by_id via pytest-benchmark), not a TEST_SPEC case.
Deferred: AC-N1.2 — NFR Integration verifier (test_nfr01_perf_p95_list_limit_50 via pytest-benchmark), not a TEST_SPEC case.
Deferred: AC-N1.3 — NFR Integration verifier (test_nfr01_constant_sql_count_event_listener via SQLAlchemy event listener), not a TEST_SPEC case.
Deferred: AC-N2.1 — unit test_no_shell_true_eval_exec_in_source (grep gate over 03-development/src/), not a TEST_SPEC case.
Deferred: AC-N2.2 — unit test_no_string_concat_sql_in_source (grep gate), not a TEST_SPEC case.
Deferred: AC-N2.3 — unit test_keys_hashed_and_compared_with_hmac_compare_digest, not a TEST_SPEC case.
Deferred: AC-N2.4 — unit test_403_body_does_not_leak_resource_existence, not a TEST_SPEC case.
Deferred: AC-N2.5 — unit test_error_body_no_stack_sql_or_paths, not a TEST_SPEC case.
Deferred: AC-N2.6 — unit test_cors_denies_by_default_allowlist_only (TASKQ_CORS_ORIGINS allowlist), not a TEST_SPEC case.
Deferred: AC-N2.7 — unit test_bandit_zero_high_zero_medium (`bandit -r 03-development/src/`), not a TEST_SPEC case.
Deferred: AC-N3.1 — unit test_per_request_tx_boundary_commit_or_rollback, not a TEST_SPEC case.
Deferred: AC-N3.2 — unit test_no_bare_except_or_except_exception_pass, not a TEST_SPEC case.
Deferred: AC-N3.3 — unit test_cancelled_error_is_reraised, not a TEST_SPEC case.
Deferred: AC-N3.4 — NFR Integration verifier (test_nfr03_readyz_503_on_db_failure), not a TEST_SPEC case.
Deferred: AC-N3.5 — unit test_task_timeout_terminates_child_no_orphan, not a TEST_SPEC case.
Deferred: AC-N3.6 — unit test_failed_migration_rolls_back_to_previous_revision, not a TEST_SPEC case.
Deferred: AC-N4.1 — unit test_sensitive_lines_replaced_with_redacted (redaction regex over stdout_tail/stderr_tail/logs/error bodies), not a TEST_SPEC case.
Deferred: AC-N4.2 — unit test_db_url_password_absent_from_logs_and_metrics, not a TEST_SPEC case.
Deferred: AC-N4.3 — unit test_key_plaintext_printed_once_and_not_persisted, not a TEST_SPEC case.
Deferred: AC-N5.1 — unit test_public_fn_class_docstrings_have_fr_or_nfr_ref (ast-docstring scanner over 100% of public fns/classes), not a TEST_SPEC case.
Deferred: AC-N5.2 — unit test_openapi_summary_and_description_populated (assert /openapi.json fields), not a TEST_SPEC case.
Deferred: AC-N6.1 — unit test_importlinter_exists_with_layers_contract (`.importlinter` declares `api > service > repository > models`), not a TEST_SPEC case.
Deferred: AC-N6.2 — unit test_sqlalchemy_forbidden_outside_repository (architecture test asserting ImportError from service/ + api/), not a TEST_SPEC case.
Deferred: AC-N6.3 — unit test_lint_imports_exits_zero, not a TEST_SPEC case.
Deferred: AC-N6.4 — unit test_no_contract_weakening_or_ignore_imports_wildcards, not a TEST_SPEC case.
Deferred: AC-N7.1 — unit test_runtime_deps_pinned_with_eq_eq (parse `requirements.txt` + `requirements.lock`), not a TEST_SPEC case.
Deferred: AC-N7.2 — unit test_dependency_license_in_allowlist (`pip-licenses --format=json --with-system`), not a TEST_SPEC case.
Deferred: AC-N7.3 — unit test_pip_licenses_with_system_full_tree, not a TEST_SPEC case.
Deferred: AC-N7.4 — unit test_sbom_at_08_config_with_required_schema, not a TEST_SPEC case.
Deferred: AC-N8.1 — framework test_mutation_testing_feature_enabled_in_harness_config (`.methodology/harness_config.json`), not a TEST_SPEC case.
Deferred: AC-N8.2 — framework test_mutation_score_ge_70 (mutmut score extraction), not a TEST_SPEC case.
Deferred: AC-N8.3 — framework test_scope_limited_to_service_and_repository, not a TEST_SPEC case.
Deferred: AC-N9.1 — unit test_pytest_skipped_zero (`pytest 03-development/tests -q`), not a TEST_SPEC case.
Deferred: AC-N9.2 — unit test_zero_assert_zero (ast-assertions scanner), not a TEST_SPEC case.
Deferred: AC-N9.3 — unit test_no_test_exclusions_via_ignore_deselect_or_testpaths, not a TEST_SPEC case.
Deferred: AC-N9.4 — unit test_fr07_migration_real_sqlite_round_trip (FR-07 integration test on real SQLite file), not a TEST_SPEC case.
Deferred: AC-N9.5 — unit test_verified_status_only_after_test_passes (traceability verifier), not a TEST_SPEC case.
Deferred: AC-N10.1 — NFR Integration verifier (test_nfr10_integration_coverage_ge_80, pytest --cov), not a TEST_SPEC case.
Deferred: AC-N10.2 — NFR Integration verifier (test_nfr10_integration_driven_by_asgi_transport), not a TEST_SPEC case.
Deferred: AC-N10.3 — NFR Integration verifier (test_nfr10_integration_covers_full_error_code_set), not a TEST_SPEC case.
Deferred: AC-N11.1 — unit test_project_mi_ge_80 (radon-mi scanner, LLOC-weighted), not a TEST_SPEC case.
Deferred: AC-N11.2 — unit test_single_function_cc_le_10 (radon-cc scanner), not a TEST_SPEC case.
Deferred: AC-N11.3 — unit test_file_and_directory_size_limits, not a TEST_SPEC case.
Deferred: AC-N11.4 — unit test_api_handler_le_40_lines (handler LOC scan), not a TEST_SPEC case.
Deferred: AC-N12.1 — deployment test_verify_system_chains_alembic_tests_smoke_round_trip (Makefile target chain), not a TEST_SPEC case.
Deferred: AC-N12.2 — deployment test_verify_system_exit_zero_prints_pass (Makefile `make verify-system`), not a TEST_SPEC case.

---

*Generated by: derive_test_cases.md v1.1 | harness-methodology v2.13.0*
