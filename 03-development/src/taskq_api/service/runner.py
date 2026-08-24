"""Subprocess runner — task execution service for FR-02.

Spawns tasks as OS subprocesses via ``asyncio.create_subprocess_exec``
(NEVER invoking a shell), enforces the per-task timeout read from
``TASKQ_TASK_TIMEOUT`` (seconds, default 30), and persists the result
row through ``task_repo.task_repo.write_result``.

Pure state-machine logic is exposed as ``state_machine`` so callers can
map ``(initial_status, signals) -> new_status`` without spawning a
process. Both functions cooperate with the FR-02 cancellation contract:
``run_task`` re-raises ``asyncio.CancelledError`` (does not swallow),
and ``state_machine`` raises ``CancelledError`` when the ``cancel=True``
flag is set so a cancel signal during the ``pending`` phase keeps the
row at ``pending`` (no progress was made).

[FR-02]
Citations:
  - FR-02 §3 AC-2.2: ``asyncio.create_subprocess_exec(*shlex.split(cmd))``
    — invoking a shell interpreter is forbidden (NFR-02: 0 bandit
    HIGH/MEDIUM, 0 grep hits for the shell keyword).
  - FR-02 §3 AC-2.3: timeout = ``TASKQ_TASK_TIMEOUT`` (seconds, default 30).
    On timeout the child is ``proc.kill()``-ed and the status becomes
    ``"timeout"``.
  - FR-02 §3 AC-2.4: state machine transitions
    ``pending -> running -> done | failed | timeout``.  CancelledError
    propagates and the row stays pending (no progress was made).
  - FR-02 §3 AC-2.5: results written to ``task_results`` with columns
    ``exit_code`` / ``stdout_tail`` / ``stderr_tail`` / ``duration_ms``
    / ``finished_at``; ``token=...`` secrets are redacted to
    ``[REDACTED]`` before persistence (NFR-04).
  - FR-08: this module is a high-risk module per SAB.json
    ``high_risk_modules``; mutations here are scored by NFR-08.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import uuid
from datetime import datetime, timezone
from typing import Any

import taskq_api.repository.task_repo as task_repo_mod


# ---------------------------------------------------------------------------
# Redaction (NFR-04): replace ``token=<value>`` with the configured marker
# BEFORE writing stdout/stderr tails to the persistence layer. The regex
# captures everything non-whitespace after ``token=`` so a trailing newline
# stays intact.
# ---------------------------------------------------------------------------
_REDACTION_PATTERN = re.compile(r"token=\S*")
_REDACTED_MARKER = "[REDACTED]"

# Timeout budget defaults — env var overrides win.
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DRAIN_TIMEOUT_SECONDS = 1.0

# Conventional shell exit code for "command not found".
_EXIT_COMMAND_NOT_FOUND = 127
# Conventional exit code when tokenisation itself fails.
_EXIT_TOKENISE_FAILURE = -1


# ---------------------------------------------------------------------------
# State machine — pure function, NO side effects. The five TEST_SPEC
# sub-rows (AC-2.4 cases 4..8) are exhaustively enumerated here so the
# state-transition surface is grep-friendly for spec-coverage-check.
# ---------------------------------------------------------------------------
def state_machine(
    initial_status: str,
    *,
    trigger: str | None = None,
    exit_code: int | None = None,
    timeout_triggered: bool = False,
    cancel: bool = False,
) -> dict[str, Any]:
    """Pure status-transition function for FR-02 AC-2.4.

    Args:
        initial_status: starting status (``"pending"`` or ``"running"``).
        trigger: optional trigger name (``"execute"`` advances pending).
        exit_code: subprocess return code (``None`` means "not yet known").
        timeout_triggered: ``True`` iff the watchdog killed the child.
        cancel: ``True`` iff an ``asyncio.CancelledError`` arrived during
            the ``pending`` phase — re-raise so the caller observes a
            row that never progressed past ``pending``.

    Returns:
        Mapping ``{"status": <new_status>}``. Only the status field is
        defined by this contract; callers may attach additional keys.

    Raises:
        asyncio.CancelledError: when ``cancel=True`` — never swallowed
            so FR-02's "does NOT swallow" invariant holds.

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.4 sub-rows 4..8 (TEST_SPEC.md FR-02).
    """
    if cancel:
        # AC8-cancel-propagates: re-raise so the caller observes that
        # no progress was made; the row stays at initial_status="pending".
        raise asyncio.CancelledError()

    if initial_status == "pending" and trigger == "execute":
        # AC4-pending-running: pending -> running on execute trigger.
        return {"status": "running"}

    if initial_status == "running":
        if timeout_triggered:
            # AC7-running-timeout: timeout watchdog fired.
            return {"status": "timeout"}
        if exit_code == 0:
            # AC5-running-done: clean exit.
            return {"status": "done"}
        if exit_code is not None and exit_code != 0:
            # AC6-running-failed: non-zero exit.
            return {"status": "failed"}

    # No transition matched — return the input status unchanged so the
    # caller can distinguish "unreachable case" from a known transition.
    return {"status": initial_status}


# ---------------------------------------------------------------------------
# Small helpers — pure functions, no I/O.
# ---------------------------------------------------------------------------
def _resolve_timeout(timeout_seconds: float | None) -> float:
    """Resolve the effective per-task timeout in seconds.

    Priority:
        1. ``timeout_seconds`` keyword argument (highest).
        2. ``TASKQ_TASK_TIMEOUT`` env var (parsed as float).
        3. Default 30 seconds.
    """
    if timeout_seconds is not None:
        return float(timeout_seconds)
    env_value = os.environ.get("TASKQ_TASK_TIMEOUT")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_SECONDS


def _redact(text: str) -> str:
    """Replace ``token=<value>`` substrings with ``[REDACTED]``."""
    if not text:
        return text
    return _REDACTION_PATTERN.sub(_REDACTED_MARKER, text)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at_dt: datetime) -> int:
    """Wall-clock milliseconds between ``started_at_dt`` and now (UTC)."""
    return int(
        (datetime.now(timezone.utc) - started_at_dt).total_seconds() * 1000
    )


# ---------------------------------------------------------------------------
# Persistence helpers — best-effort, swallow repo failures so the
# subprocess remains the unit of work. Each helper centralises one of
# the three shapes ``run_task`` needs.
# ---------------------------------------------------------------------------
def _safe_update_status(task_id: str | None, status: str) -> None:
    """Best-effort: update the task row's status (no-op without ``task_id``)."""
    if not task_id:
        return
    try:
        task_repo_mod.task_repo.update_status(task_id, status)
    except Exception:
        # The runner must not block on repo failures — the subprocess
        # is the unit of work.
        pass


def _safe_write_result(task_id: str | None, **fields: Any) -> None:
    """Best-effort: append a row to ``task_results`` (no-op without ``task_id``)."""
    if not task_id:
        return
    try:
        task_repo_mod.task_repo.write_result(task_id=task_id, **fields)
    except Exception:
        pass


def _safe_persist_terminal(
    task_id: str | None,
    *,
    status: str,
    run_id: str,
    exit_code: int,
    stdout_tail: str,
    stderr_tail: str,
    duration_ms: int,
    finished_at: str,
) -> None:
    """Best-effort terminal-state persistence.

    Issues ``update_status`` then ``write_result`` inside a single
    try-block so a status-write failure does not leave a partial
    terminal-row orphan. The two calls share one failure boundary
    on purpose — that ordering is part of the GREEN contract.
    """
    if not task_id:
        return
    try:
        task_repo_mod.task_repo.update_status(task_id, status)
        task_repo_mod.task_repo.write_result(
            task_id=task_id,
            run_id=run_id,
            exit_code=exit_code,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            duration_ms=duration_ms,
            finished_at=finished_at,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------
async def _drain_pipes(
    proc: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    """Drain stdout/stderr after kill (best-effort, short timeout).

    Returns ``(b"", b"")`` if the child does not exit before the drain
    budget elapses — we don't want to block the request on a zombie.
    """
    try:
        return await asyncio.wait_for(
            proc.communicate(),
            timeout=_DRAIN_TIMEOUT_SECONDS,
        )
    except Exception:
        return (b"", b"")


# ---------------------------------------------------------------------------
# Public entry point — spawn the subprocess and persist the result row.
# ---------------------------------------------------------------------------
async def run_task(
    command: str,
    *,
    timeout_seconds: float | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute ``command`` as a subprocess and persist the result.

    Args:
        command: shell-style command string. Splitted with ``shlex.split``
            and passed positionally to ``asyncio.create_subprocess_exec``;
            ``shell`` is NEVER used.
        timeout_seconds: optional override for the per-task timeout in
            seconds. Falls back to ``TASKQ_TASK_TIMEOUT`` env var, then
            30 seconds default.
        task_id: optional task identifier — when set, the runner updates
            the row's status and writes the result row through
            ``task_repo.task_repo``.
        run_id: optional pre-allocated run identifier (the handler
            generates one with ``uuid.uuid4()`` so the POST 202 can
            return it before the subprocess finishes).

    Returns:
        ``{"status_name": <status>, "exit_code": <int|None>,
           "run_id": <str>}`` — shape matches the TEST_SPEC AC-2.3
        contract.

    Raises:
        asyncio.CancelledError: re-raised when the awaiting task is
        cancelled (NFR-03 error_handling: 0 bare except;
        CancelledError re-raised).

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.2: subprocess isolation via ``create_subprocess_exec``.
      - FR-02 §3 AC-2.3: timeout enforced via ``asyncio.wait_for`` +
        ``proc.kill()``.
      - FR-02 §3 AC-2.5: result row persisted via ``task_repo``.
    """
    timeout = _resolve_timeout(timeout_seconds)
    if run_id is None:
        run_id = str(uuid.uuid4())
    started_at_dt = datetime.now(timezone.utc)

    # ---- 1) Mark the task as "running" (best-effort). --------------------
    _safe_update_status(task_id, "running")

    # ---- 2) Tokenise the command. Never invoke a shell. -----------------
    try:
        args = shlex.split(command)
    except ValueError as exc:
        _safe_write_result(
            task_id=task_id,
            run_id=run_id,
            exit_code=_EXIT_TOKENISE_FAILURE,
            stdout_tail="",
            stderr_tail=str(exc),
            duration_ms=0,
            finished_at=_now_iso(),
        )
        return {
            "status_name": "failed",
            "exit_code": _EXIT_TOKENISE_FAILURE,
            "run_id": run_id,
        }

    # ---- 3) Spawn the subprocess. ----------------------------------------
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as exc:
        _safe_persist_terminal(
            task_id=task_id,
            status="failed",
            run_id=run_id,
            exit_code=_EXIT_COMMAND_NOT_FOUND,
            stdout_tail="",
            stderr_tail=str(exc),
            duration_ms=0,
            finished_at=_now_iso(),
        )
        return {
            "status_name": "failed",
            "exit_code": _EXIT_COMMAND_NOT_FOUND,
            "run_id": run_id,
        }

    # ---- 4) Wait for the subprocess with the configured timeout. --------
    timeout_triggered = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        timeout_triggered = True
        # Kill the child so it doesn't outlive the request; ignore the
        # race where it has already exited.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        stdout_bytes, stderr_bytes = await _drain_pipes(proc)

    # ---- 5) Compute duration + status. ----------------------------------
    duration_ms = _elapsed_ms(started_at_dt)
    finished_at = _now_iso()
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    stderr_text = stderr_bytes.decode("utf-8", errors="replace")

    if timeout_triggered:
        new_status = "timeout"
        exit_code_value: int | None = None
    else:
        exit_code_value = proc.returncode
        new_status = state_machine(
            "running",
            exit_code=exit_code_value,
        )["status"]

    # ---- 6) Persist the terminal row (best-effort). ----------------------
    _safe_persist_terminal(
        task_id=task_id,
        status=new_status,
        run_id=run_id,
        exit_code=(exit_code_value if exit_code_value is not None else -1),
        stdout_tail=_redact(stdout_text),
        stderr_tail=_redact(stderr_text),
        duration_ms=duration_ms,
        finished_at=finished_at,
    )

    return {
        "status_name": new_status,
        "exit_code": exit_code_value,
        "run_id": run_id,
    }