# Adversarial Bug-Hunt Report — taskq-api (Gate 3 / adversarial_review)

**Run date:** 2026-08-26
**HEAD at scan:** `a3051b349b041bbdd1abbe76c2182f8e24be4d61`
**Targeting manifest:** `.methodology/bug_hunt_targets.json`
**Lenses applied:** correctness, concurrency, resilience

## 掃描摘要

| Module | Severity | Lens | Confirmed | Resolution |
|---|---|---|---|---|
| taskq_api.service.runner | high | concurrency | T-07 (orphan descendants) | open → resolved (commit pending) |
| taskq_api.service.runner | high | correctness | T-08 (redaction regex) | open → resolved (commit pending) |

**raw_count = 2 / confirmed_count = 2 / refuted_count = 0**

Two confirmed critical/high findings — both target the declared threat model (T-07, T-08). The threat model's `mitigation` language was verified line-by-line against the implementation; both mitigations are present in skeleton but neither is actually effective at the attack it claims to block.

## 確認 Bugs(severity 降序)

### 1. T-07 orphan descendants — HIGH
**位置:** `03-development/src/taskq_api/service/runner.py:303-340`(`_reap_after_kill`), `runner.py:446-452`(`create_subprocess_exec` 不帶 `start_new_session=True`)

**問題:** `proc.kill()` 只對 child PID 送 SIGKILL;若 child 已 spawn 子行程(subprocess.Popen / fork),子行程不被殺,被 reparent 到 launchd 變 orphan。SAD §6 T-07 mitigation 寫「integration test asserts no descendant pid remains after timeout」,但實作沒做 process group kill。

**證據:**
- 實測 `python3 -c "import subprocess,time; p = subprocess.Popen(['sleep','30']); time.sleep(15)"` → `run_task(timeout_seconds=3)` 後,`ps -A -o pid,ppid,command` 顯示 `sleep 30`(PPID=1)仍然活著。
- 對照組:直接 `subprocess.Popen(...)` 殺 parent 後,descendant 同樣存活 — 確認這是 `proc.kill()` 本身的行為,而非 runner 特有 bug。
- 整體可達:`TaskCreate._no_injection_chars` 黑名單 `;|&`$()<>\n` 不包含引號,所以 `python3 -c "..."` 通過 schema 驗證,透過 POST `/v1/tasks/{id}/run` 完整可達。

**修復:** `asyncio.create_subprocess_exec(..., start_new_session=True)`(把 child 放進自己的 session/PG),然後 `_reap_after_kill` 改用 `os.killpg(os.getpgid(proc.pid), signal.SIGKILL)`。

### 2. T-08 redaction regex 不完整 — HIGH
**位置:** `03-development/src/taskq_api/service/runner.py:82`(`_REDACTION_PATTERN = re.compile(r"token=\S*")`)、`runner.py:194-198`(`_redact`)、`runner.py:509-510`(寫入 stdout/stderr 前呼叫)

**問題:** 正則只 match `token=...`。T-08 description 明列三種秘密:API key、bearer token、DSN password。實測 `_redact`:
- `Bearer ABCDEFG12345` → 原樣輸出(未遮罩)
- `password=hunter2` → 原樣輸出
- `postgres://user:pwd@host/db` → 原樣輸出
- `api_key=foo` → 原樣輸出

只有 `token=...` 被替換成 `[REDACTED]`。Bearer/DSN/api_key 三類全部 leak 到 `task_results.stdout_tail`/`stderr_tail`,再由 GET `/v1/tasks/{id}/runs`(read scope)回傳給任何持有 read 金鑰的人。

**修復:** 擴充 regex 涵蓋 T-08 三類:`re.compile(r"(?:token|Bearer\s+|password=|api_key=|postgres://[^:]+:[^@]+@)\S*")`。同步更新 `tests/test_nfr_deferred.py:317` 的 forbidden list,避免 source-scan test 漏接。

## 被反駁清單

無(本次 hunt raw=2,confirmed=2;兩個 finding 都是 HIGH 級且 evidence 充分,不需要 refuter 分支)。

## 修復優先順序

1. **T-07** — DoS 級 orphan 累積,每次提交 task 都會 leak 一個長期行程,必須先修。
2. **T-08** — 資訊洩漏,secret 級,會直接造成 token 外洩。

兩個修復獨立、互不依賴,可以平行進行。

## 掃描方法

- 讀取 `.methodology/bug_hunt_targets.json` 的 `threat_model` 區塊,逐條驗證 SAD.md §6 的 `mitigation` 是否真實阻擋對應 attack vector。
- 對每個 finding:
  - **Refuter:** 預設 `is_real=false`,但找不到現有 guard / fallback 能化解,直接被 evidence 擊敗。
  - **Confirmer:** 對 T-07 跑 inline subprocess 驗證 descendant 存活;對 T-08 對 `_redact` 餵多組 secret form,確認未被遮罩。
- 兩個 finding 都達 2/2 verifier is_real,confirmed=true。
- hunter lens: T-07 走 concurrency(proc lifecycle / signal propagation);T-08 走 correctness(input sanitisation 對威脅宣告的覆蓋率)。