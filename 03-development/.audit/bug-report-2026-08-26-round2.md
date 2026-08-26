# Adversarial Bug-Hunt Report — taskq-api (Gate 3 / adversarial_review, Round 2)

**Run date:** 2026-08-26
**HEAD at scan:** `b503b590f2cb6d3683fd93689db8e09c6422a543`
**Targeting manifest:** `.methodology/bug_hunt_targets.json`
**Lenses applied:** correctness, concurrency, resilience

## 掃描摘要

| Threat | Owner module | Lens | Severity | Mitigation Effective | Status |
|---|---|---|---|---|---|
| T-01 (spoofing) | taskq_api.service.auth | correctness | high | YES | refuted (attack blocked) |
| T-02 (tampering) | taskq_api.api.tasks | correctness | high | YES | refuted |
| T-03 (EoP) | taskq_api.api.deps | correctness | high | YES | refuted |
| T-04 (info disc) | taskq_api.api.deps | correctness | high | YES | refuted |
| T-05 (SQL inj) | taskq_api.repository.task_repo | correctness | high | YES | refuted |
| T-06 (shell inj) | taskq_api.service.runner | correctness | high | YES | refuted |
| T-07 (orphan) | taskq_api.service.runner | concurrency | high | YES | **resolved** (commit a4a6bc5) |
| T-08 (redaction) | taskq_api.service.runner | correctness | high | YES | **resolved** (commit a4a6bc5) |
| T-09 (repudiation) | taskq_api.errors | resilience | medium | YES | refuted |
| T-10 (DoS lock) | taskq_api.repository.rate_repo | concurrency | medium | YES | refuted |

**raw_count = 10 / confirmed_count = 2 / refuted_count = 8**

All 10 declared STRIDE-lite threats (SAD.md §6) were re-verified at current HEAD. The two previous-round findings (T-07, T-08) remain resolved — fixes are in place, repro tests pass. No new confirmed bugs were discovered this round.

## 確認 Bugs(severity 降序)

### 1. T-07 orphan descendants — HIGH (resolved)
**位置:** `runner.py:360-391`(`_reap_after_kill`), `runner.py:509`(Popen session flag)

**狀態:** **RESOLVED** at commit `a4a6bc5`; repro test `test_t07_no_orphan_descendant_after_timeout` PASSES at current HEAD. `os.killpg(os.getpgid(proc.pid), SIGKILL)` 配合 `start_new_session=True` 確實殺掉 child 及其整個 process group。

### 2. T-08 redaction regex 不完整 — HIGH (resolved)
**位置:** `runner.py:85-87`(expanded regex)

**狀態:** **RESOLVED** at commit `a4a6bc5`; 4 個 repro tests(T-08 bearer/DSN/api_key/password forms)PASS。Regex 已涵蓋 SAD §6 T-08 列舉的全部三類秘密形式(token=, Bearer, DSN password)加上 api_key=。

## 被反駁清單(8 threats,各一句理由)

- **T-01** spoofing: `auth.py:91` 用 `hmac.compare_digest`,對 X-API-Key 做常數時間比對,forged key 一律 401。
- **T-02** tampering: `schemas.py:22` 黑名單 + `schemas.py:35` max_length=1000 + `tasks.py:64` dict-based create(repo 無 SQL concat),多層防禦。
- **T-03** EoP: `tasks.py:138` DELETE 綁定 `_require_admin`;`auth.py:60-64` rank-based check,read(0) < admin(2) → False → 403。
- **T-04** info disc: `deps.py:253-256` 403 detail 是 generic message,無 resource id、無 not-found 線索。
- **T-05** SQL inj: 全 source tree 走 SQLAlchemy ORM/parameter binding;`test_no_string_concat_sql_in_source` 0 hits,`test_bandit_zero_high_zero_medium` 0 hits.
- **T-06** shell inj: `runner.py:480` shlex.split + `runner.py:505-510` create_subprocess_exec(*args) shell=False,無 shell 解譯。
- **T-09** repudiation: `errors.py:115-127` + `app.py:73-76` + `app.py:106-112` + `app.py:115` 同步 emit correlation_id 在 body / header / log 三處。
- **T-10** DoS lock: `rate_repo.py:154-216` 鎖只持有 select + arithmetic + update(微秒級),transaction 內無 I/O/await。

## 修復優先順序

兩個 HIGH 都已在 a4a6bc5 commit 中修復並落地 repro tests。**本輪不需新修復**。

## 掃描方法

- 讀取 `.methodology/bug_hunt_targets.json` 的 `threat_model` 10 條 + high_risk 11 條。
- 對每條 threat:追 data flow(攻擊輸入 → 是否到 mitigation code → mitigation 是否真的阻擋)。
- 重跑既有 repro tests:`pytest 03-development/tests/test_bug_hunt_resolutions.py -v` → 8/8 PASS。
- 檢查 pattern tests:`pytest 03-development/tests/test_nfr_patterns.py -v` → 11/11 PASS(no shell=True、no SQL concat、hmac.compare_digest only、403 不 leak existence 等)。
- 兩個 verified bug 沿用上輪 commit a4a6bc5 的 SHA + repro path(commit 仍存在於 HEAD 之前的歷史中,code 仍在 current HEAD 的 source 中)。
- hunter lens:T-07/10 → concurrency;其他 → correctness。