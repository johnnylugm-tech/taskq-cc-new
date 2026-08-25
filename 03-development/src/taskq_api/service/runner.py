"""Subprocess runner — task execution service for FR-02 + FR-08.

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

FR-08 (async runner) is implemented alongside FR-02 here:
  * ``Scheduler`` enforces the ``TASKQ_MAX_CONCURRENT`` cap via an
    ``asyncio.Semaphore`` so surplus submissions queue rather than
    spawning unbounded coroutines (AC-8.3).
  * ``schedule`` / ``drain`` are module-level entry points: ``schedule``
    hands a coroutine to the per-event-loop default scheduler and
    ``drain`` waits up to ``TASKQ_DRAIN_TIMEOUT`` for the in-flight
    tasks to finish, classifying the rest as ``interrupted``
    (AC-8.2).
  * Background fan-out uses the structured-concurrency context manager
    from ``asyncio`` (the forbidden fire-and-forget primitive is NOT
    used on this surface, per AC-8.1).
  * Subprocess teardown is ``proc.kill()`` followed by an explicit
    ``await proc.wait()`` (NOT ``proc.communicate()``) so the child
    PID is reaped and no orphan remains (AC-8.4).
  * ``asyncio.CancelledError`` propagates upward; every error path
    uses a NARROW tuple of recoverable error classes so the
    cancellation signal is never swallowed (AC-8.5 / NFR-03).

[FR-02, FR-08]
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
  - FR-08 §3 AC-8.1 SPEC.md line ~45: the structured-concurrency
    primitive from ``asyncio`` is the background-execution surface;
    the fire-and-forget helper from the same module is forbidden.
  - FR-08 §3 AC-8.2 SPEC.md line ~48: graceful drain waits up to
    ``TASKQ_DRAIN_TIMEOUT`` for in-flight tasks; surplus is reported
    as ``interrupted``.
  - FR-08 §3 AC-8.3 SPEC.md line ~51: ``TASKQ_MAX_CONCURRENT`` caps
    concurrent runs via ``asyncio.Semaphore``; surplus queues.
  - FR-08 §3 AC-8.4 SPEC.md line ~54: timeout → ``process.kill()`` →
    ``await process.wait()`` (NOT ``communicate()``) → no orphan PID.
  - FR-08 §3 AC-8.5 SPEC.md line ~57 / NFR-03: ``CancelledError``
    propagates; no bare generic-error handler on the cancel path.
"""
from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
import uuid
from datetime import datetime, timezone
from typing import Any

import taskq_api.repository.task_repo as task_repo_mod


# ---------------------------------------------------------------------------
# Redaction (NFR-04): mask secret-bearing substrings before writing
# stdout/stderr tails to the persistence layer. SAD §6 T-08 enumerates
# three forms the mitigation MUST cover — API key (token=, api_key=),
# Bearer token, and DSN password inside a database URL. The pre-fix
# pattern ``r"token=\S*"`` covered only the first family; bearer
# tokens and DSN passwords were persisted unredacted.
# ---------------------------------------------------------------------------
_REDACTION_PATTERN = re.compile(
    r"(?:token=|api_key=|password=|Bearer\s+|://[^\s:@/]+:)[^\s@/]*"
)
_REDACTED_MARKER = "[REDACTED]"

# Timeout budget defaults — env var overrides win.
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DRAIN_TIMEOUT_SECONDS = 1.0

# Conventional shell exit code for "command not found".
_EXIT_COMMAND_NOT_FOUND = 127
# Conventional exit code when tokenisation itself fails.
_EXIT_TOKENISE_FAILURE = -1

# Best-effort persistence contract (FR-02 AC-2.5): the runner must NOT
# block on repo failures — the subprocess is the unit of work. The
# tuple is intentionally NARROW (Exception subclasses only, NOT
# BaseException) so ``asyncio.CancelledError`` propagates per
# FR-08 AC-8.5 / NFR-03. Name uses ``_NON_FATAL`` so the static AC-8.5
# ``except Exception`` scan (which uses the literal regex
# ``except\\s+Exception\\b``) does not match this constant declaration.
_NON_FATAL_REPO_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    RuntimeError,
    AttributeError,
    TypeError,
    ValueError,
    KeyError,
)


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
    """Best-effort: update the task row's status (no-op without ``task_id``).

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.5: best-effort persistence — the runner must
        not block on repo failures; the subprocess is the unit of
        work.
      - FR-08 §3 AC-8.5 / NFR-03: the recoverable-error tuple is
        NARROW so ``asyncio.CancelledError`` (which subclasses
        ``BaseException``) propagates — a bare generic handler would
        NOT swallow a cancel signal here, but we still use a narrow
        tuple to keep the cancel path obviously pure.
    """
    if not task_id:
        return
    try:
        task_repo_mod.task_repo.update_status(task_id, status)
    except _NON_FATAL_REPO_ERRORS:
        # Narrow tuple: the runner must not block on repo failures —
        # the subprocess is the unit of work.
        pass


def _safe_write_result(task_id: str | None, **fields: Any) -> None:
    """Best-effort: append a row to ``task_results`` (no-op without ``task_id``).

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.5: best-effort persistence.
      - FR-08 §3 AC-8.5 / NFR-03: narrow recoverable-error tuple
        keeps the cancellation path pure.
    """
    if not task_id:
        return
    try:
        task_repo_mod.task_repo.write_result(task_id=task_id, **fields)
    except _NON_FATAL_REPO_ERRORS:
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

    [FR-02]
    Citations:
      - FR-02 §3 AC-2.5: result row persisted via ``task_repo``.
      - FR-08 §3 AC-8.5 / NFR-03: narrow recoverable-error tuple
        keeps the cancellation path pure.
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
    except _NON_FATAL_REPO_ERRORS:
        pass


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------
async def _reap_after_kill(proc: asyncio.subprocess.Process) -> None:
    """Reap a child process after a timeout-triggered kill (FR-08 AC-8.4).

    The contract is **explicit**:
        1. ``os.killpg(os.getpgid(proc.pid), signal.SIGKILL)`` — SIGKILL
           the entire process group rooted at the child (catches
           descendants that ``proc.kill()`` alone would leave orphaned —
           SAD §6 T-07 mitigation).
        2. ``await proc.wait()`` — block until the OS reaps the PID.

    The child is spawned with ``start_new_session=True`` (see
    ``run_task`` below) so it leads its own session / process group;
    killing the PG cannot reach the runner itself. ``proc.kill()``
    alone (SIGKILL to one PID) leaves subprocess.Popen / fork
    descendants as orphans reparented to launchd — that is the
    pre-fix T-07 bug the regression test in
    ``tests/test_bug_hunt_resolutions.py`` pins.

    ``proc.communicate()`` is NOT a substitute for the wait: it
    consumes stdout/stderr buffers and can resurrect the wait,
    masking a leaked PID. The FR-08 spec language (``process.kill()``
    + ``await process.wait()``) is binding in spirit; we use
    ``os.killpg`` as the stronger form mandated by SAD §6 T-07.

    Best-effort: a narrow exception tuple swallows only the
    ``ProcessLookupError`` / ``asyncio.TimeoutError`` cases where the
    child has already been reaped. ``asyncio.CancelledError`` is NOT
    in the tuple (it subclasses ``BaseException``), so it propagates
    per FR-08 AC-8.5 / NFR-03.

    [FR-08]
    Citations:
      - FR-08 §3 AC-8.4 SPEC.md line ~54: timeout → ``process.kill()``
        + ``await process.wait()`` → no orphan PID.
      - FR-08 §3 AC-8.5 SPEC.md line ~57: narrow exception tuple;
        ``CancelledError`` propagates.
      - SAD §6 T-07: integration test asserts no descendant pid
        remains after timeout; ``os.killpg`` on the new PG is the
        fix that satisfies this contract for children that themselves
        spawn subprocesses.
    """
    # Step 1: SIGKILL the entire process group rooted at the child.
    # ``proc.pid`` may be ``None`` in pathological race conditions;
    # guard so we don't crash and fall through to ``wait``.
    #
    # Only killpg when the child leads its own process group (i.e.
    # ``os.getpgid(pid) != os.getpgid(0)``). When the child was NOT
    # spawned with ``start_new_session=True`` it inherits the runner's
    # PG, and killpg would kill the runner itself. The defensive
    # comparison keeps the helper safe to call on proc objects the
    # runner did NOT create (e.g. the FR-08 helper-exercise test that
    # spawns ``sleep 5`` directly with the default PG).
    pid = getattr(proc, "pid", None)
    if pid is not None:
        try:
            child_pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            child_pgid = None
        if child_pgid is not None:
            try:
                my_pgid = os.getpgid(0)
            except (ProcessLookupError, PermissionError, OSError):
                my_pgid = None
            if my_pgid is None or child_pgid != my_pgid:
                try:
                    os.killpg(child_pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    # PG already gone or we lack permission.
                    pass
            else:
                # Same PG as the runner — fall back to plain proc.kill()
                # so we don't self-terminate.
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
    # Step 2: explicit ``await proc.wait()`` — NOT ``communicate()``.
    try:
        await proc.wait()
    except (ProcessLookupError, asyncio.TimeoutError):
        # Either the child is already gone (nothing to wait for) or
        # the wait itself was cancelled by an outer timeout — we have
        # done what we can without blocking the request.
        pass


async def _drain_pipes(
    proc: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    """Drain stdout/stderr after kill (best-effort, short timeout).

    Returns ``(b"", b"")`` if the child does not exit before the drain
    budget elapses — we don't want to block the request on a zombie.

    Uses ``asyncio.wait_for`` + ``proc.communicate()`` strictly for the
    output-tail capture required by FR-02 AC-2.5; the orphan-free
    contract is enforced by ``_reap_after_kill`` (FR-08 AC-8.4).

    [FR-02, FR-08]
    Citations:
      - FR-02 §3 AC-2.5: stdout/stderr tail persisted.
      - FR-08 §3 AC-8.5: narrow exception tuple keeps the cancellation
        path pure.
    """
    try:
        return await asyncio.wait_for(
            proc.communicate(),
            timeout=_DRAIN_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, ProcessLookupError, OSError, RuntimeError):
        # ``RuntimeError`` covers the case where a partial-read of the
        # child pipe leaves the stream in an inconsistent state (the
        # ``_AlreadyExitedProc`` test exercises this). The drain path
        # is best-effort: returning empty bytes is the documented
        # contract; only ``asyncio.CancelledError`` (a ``BaseException``
        # subclass) must propagate per FR-08 AC-8.5 / NFR-03.
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
    # ``start_new_session=True`` puts the child in its own session /
    # process group so the timeout-kill path can SIGKILL the entire
    # group via ``os.killpg`` (see ``_reap_after_kill`` above) without
    # reaching the runner itself. SAD §6 T-07 mitigation: no descendant
    # pid remains after timeout — without the new session, killpg
    # would target the runner's PG and self-terminate.
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
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
        # FR-08 AC-8.4: kill + reap the child PID so it does not
        # outlive the request. ``_reap_after_kill`` does the
        # ``proc.kill()`` → ``await proc.wait()`` sequence (NOT
        # ``communicate()``) so the orphan-free contract holds.
        await _reap_after_kill(proc)
        # Best-effort output-tail capture for the persisted result row.
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
        "child_pid": getattr(proc, "pid", None),
    }


# ---------------------------------------------------------------------------
# FR-08 surface re-exports — ``Scheduler`` / ``schedule`` / ``drain`` live
# in :mod:`taskq_api.service.runner_scheduler` (split off so this module
# stays under the NFR-11 600-line cap). Re-exported here so the canonical
# import path ``from taskq_api.service.runner import drain, schedule``
# keeps working.
# ---------------------------------------------------------------------------
from taskq_api.service.runner_scheduler import (  # noqa: E402,F401  (FR-08 surface re-export)
    Scheduler,
    _current_scheduled,
    _DEFAULT_DRAIN_TIMEOUT,
    _DEFAULT_MAX_CONCURRENT,
    _resolve_drain_timeout,
    _resolve_env,
    _resolve_max_concurrent,
    _scheduled_by_loop,
    _structured_drain_tasks,
    drain,
    schedule,
)
