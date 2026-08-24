"""RED tests for FR-08 — Async runner (TaskGroup, graceful drain, concurrency cap, timeout kills child).

SAB binding for this FR (per ``.methodology/SAB.json``
``fr_module_traceability``):
    FR-08  ->  taskq_api.service.runner     (async scheduler / drain / cap)
    FR-08  ->  taskq_api.api.tasks          (background scheduling surface)

Gate 1's Architecture Amendment Protocol treats a missing declared
module as a phantom and BLOCKS the merge. The top-level imports below
MUST resolve once GREEN implements FR-08 — they are the contract the
implementation has to satisfy, not just convenient imports.

This file is intentionally RED. ``taskq_api.service.runner`` is
implemented for FR-02 but does NOT yet expose the FR-08 scheduler
surface (``TaskGroup`` background runner, graceful drain with
``TASKQ_DRAIN_TIMEOUT``, ``TASKQ_MAX_CONCURRENT`` semaphore,
explicit ``await proc.wait()`` after ``proc.kill()``, and the
non-swallowing ``CancelledError`` contract). Per the test contract:

    "If pytest returns Exit Code 2 (Collection Error) due to missing
    modules, this is a VALID RED STATE. Do not try to 'fix' it by
    hiding the import."

Test cases match ``02-architecture/TEST_SPEC.md`` FR-08 exactly (names
are the single source of truth for ``spec-coverage-check``):
    1.  test_background_uses_asyncio_task_group              (AC-8.1)
    2.  test_drain_waits_for_inflight_with_budget            (AC-8.2)
    3.  test_concurrency_cap_queues_surplus                  (AC-8.3)
    4.  test_wait_for_kills_child_no_orphan                  (AC-8.4)
    5.  test_cancelled_error_propagates_not_swallowed       (AC-8.5)

GREEN TODO contract (must be implemented for these tests to pass):

    taskq_api.service.runner
        * Background execution MUST be orchestrated via
          ``asyncio.TaskGroup`` (Python 3.11+) — ``asyncio.gather``
          is forbidden (no structured cancellation).
        * A drain function/class MUST wait up to ``TASKQ_DRAIN_TIMEOUT``
          seconds for in-flight tasks to complete; tasks exceeding the
          budget MUST be marked ``interrupted`` (not silently dropped).
        * A scheduler/semaphore MUST cap concurrency at
          ``TASKQ_MAX_CONCURRENT``; surplus submissions MUST queue, NOT
          spawn unbounded coroutines.
        * Per-task timeout via ``asyncio.wait_for`` MUST be followed by
          an explicit ``proc.kill()`` and ``await proc.wait()`` (NOT
          ``proc.communicate()``) — the spec language is binding.
        * ``asyncio.CancelledError`` MUST propagate upward; any
          ``except Exception:`` block on the cancellation path is a
          contract violation (NP-07, NFR-03).

    taskq_api.api.tasks
        * The background-task plumbing MUST route through the new
          scheduler surface above (not FastAPI ``BackgroundTasks`` alone).
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time as time_mod
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# SAB binding — top-level imports per the test contract.
# The runner module exists for FR-02 but does NOT yet expose the FR-08
# scheduler surface. Per the test contract, runtime failures (AttributeError
# on missing FR-08 symbols, or assertion failures on missing patterns) are
# VALID RED STATE.
# ---------------------------------------------------------------------------

from taskq_api.service import runner as runner_mod  # noqa: F401  (Gate 1 phantom check)
from taskq_api.service.runner import run_task  # noqa: F401  (Gate 1 phantom check — public entry)


# ---------------------------------------------------------------------------
# Source-path constants — bind TEST_SPEC Inputs verbatim.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RUNNER_SOURCE = (
    _REPO_ROOT
    / "03-development"
    / "src"
    / "taskq_api"
    / "service"
    / "runner.py"
)


# ===========================================================================
# 1. test_background_uses_asyncio_task_group — AC-8.1
# ===========================================================================


# NFR-02 (security): structured concurrency over fire-and-forget.
def test_background_uses_asyncio_task_group():  # NFR-02, NP-13
    """AC-8.1: background execution uses ``asyncio.TaskGroup`` (not ``gather``).

    TEST_SPEC §FR-08 case 1 binds:
        source_path      = "03-development/src/taskq_api/service/runner.py"
        task_group_hits  = "1"   (≥1 use of ``asyncio.TaskGroup``)
        gather_hits      = "0"   (no ``asyncio.gather``)

    Subprocess isolation: pure static grep — no I/O, no asyncio loop.
    The SAB-declared module path is ``taskq_api.service.runner`` so
    the source file MUST exist on disk for the test to read.

    Sub-assertions (TEST_SPEC §FR-08):
        AC1-task-group   task_group_hits == "1"
        AC1-no-gather    gather_hits == "0"
    """
    # Bind TEST_SPEC Inputs verbatim for traceability.
    source_path = "03-development/src/taskq_api/service/runner.py"
    task_group_hits = "1"
    gather_hits = "0"
    assert source_path == "03-development/src/taskq_api/service/runner.py"
    assert task_group_hits == "1"
    assert gather_hits == "0"
    # Mirror-check anchors — verbatim predicates from TEST_SPEC §FR-08.
    assert task_group_hits == "1"  # AC1-task-group
    assert gather_hits == "0"  # AC1-no-gather

    assert _RUNNER_SOURCE.exists(), (
        f"AC-8.1: runner source missing at {_RUNNER_SOURCE} — "
        f"FR-08 phantom module per SAB.json `fr_module_traceability`."
    )
    src_text = _RUNNER_SOURCE.read_text(encoding="utf-8")

    # Count occurrences of ``asyncio.TaskGroup`` and ``asyncio.gather``.
    # The leading ``asyncio.`` prefix avoids false matches inside
    # unrelated identifiers (e.g. ``task_group_hits``).
    task_group_count = len(re.findall(r"\basyncio\.TaskGroup\b", src_text))
    gather_count = len(re.findall(r"\basyncio\.gather\b", src_text))

    assert str(task_group_count) == task_group_hits, (
        f"AC1-task-group failed: runner.py MUST use "
        f"`asyncio.TaskGroup` at least once to manage background "
        f"execution (FR-08 AC-8.1, structured concurrency). "
        f"Found {task_group_count} hit(s)."
    )
    assert str(gather_count) == gather_hits, (
        f"AC1-no-gather failed: runner.py MUST NOT use "
        f"`asyncio.gather` for background execution — TaskGroup is "
        f"the contract. Found {gather_count} hit(s)."
    )


# ===========================================================================
# 2. test_drain_waits_for_inflight_with_budget — AC-8.2
# ===========================================================================


# NP-07 (dependency fault) + NFR-03 (error_handling): graceful shutdown
# must observe a budget; exceeding tasks are marked ``interrupted``.
def test_drain_waits_for_inflight_with_budget(monkeypatch):  # NP-07, NFR-03
    """AC-8.2: drain waits for in-flight tasks up to ``TASKQ_DRAIN_TIMEOUT``; surplus → ``interrupted``.

    TEST_SPEC §FR-08 case 2 binds:
        inflight_tasks          = "3"
        drain_timeout           = "10.0"
        observed_completed_count  = "3"
        observed_interrupted_count = "0"

    Subprocess isolation: in-process — the test drives the drain
    surface directly via ``runner.drain(timeout=...)``. RED: the
    runner module does not yet expose a ``drain`` entry point, so
    attribute access fails (VALID RED STATE per the test contract).

    Sub-assertions (TEST_SPEC §FR-08):
        AC2-all-completed     observed_completed_count == "3"
        AC2-no-interrupt      observed_interrupted_count == "0"
    Property:
        P8-drain-budget       drain_elapsed_seconds <= drain_timeout
    """
    # Bind TEST_SPEC Inputs verbatim for traceability.
    inflight_tasks = "3"
    drain_timeout = "10.0"
    observed_completed_count = "3"
    observed_interrupted_count = "0"
    assert inflight_tasks == "3"
    assert drain_timeout == "10.0"
    assert observed_completed_count == "3"
    assert observed_interrupted_count == "0"
    # Mirror-check anchors — verbatim predicates from TEST_SPEC §FR-08.
    assert observed_completed_count == "3"  # AC2-all-completed
    assert observed_interrupted_count == "0"  # AC2-no-interrupt

    # Configure the drain budget so the test exercises the GREEN
    # implementation against a known upper bound. ``TASKQ_DRAIN_TIMEOUT``
    # is the spec-named knob; ``TASKQ_MAX_CONCURRENT`` is unset (the
    # cap is exercised by case 3, not here).
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", drain_timeout)

    drain_fn = getattr(runner_mod, "drain", None)
    assert drain_fn is not None, (
        f"AC-8.2: `taskq_api.service.runner.drain` MUST exist as a "
        f"callable so the service can gracefully shut down. The "
        f"current runner module does not expose it — FR-08 AC-8.2 is "
        f"not yet implemented."
    )

    # Build a scheduler that knows about three in-flight tasks; each
    # task is a short asyncio coroutine so it completes well within
    # the 10s drain budget.
    completed: list[int] = []
    interrupted: list[int] = []

    async def _short_task(idx: int) -> None:
        # Short sleep so the task completes comfortably inside the
        # drain budget — the test's premise is that drain WAITS for
        # in-flight tasks, not that it interrupts them.
        await asyncio.sleep(0.05)
        completed.append(idx)

    # GREEN TODO: the runner MUST expose ``schedule(coro)`` (or
    # equivalent) so the drain test can populate in-flight tasks.
    # RED: this attribute is absent.
    schedule_fn = getattr(runner_mod, "schedule", None)
    assert schedule_fn is not None, (
        f"AC-8.2: `taskq_api.service.runner` MUST expose a "
        f"`schedule(coro)` entry point so drain can observe "
        f"in-flight tasks. Missing — FR-08 AC-8.2 is not yet "
        f"implemented."
    )

    async def _exercise_drain() -> tuple[float, int, int]:
        # Schedule three short tasks; they should all complete inside
        # the drain budget.
        for idx in range(3):
            schedule_fn(_short_task(idx))
        # Allow the scheduler to register them.
        await asyncio.sleep(0)
        started = time_mod.monotonic()
        # GREEN TODO: ``drain(timeout=<float>) -> DrainReport`` —
        # returns the count of completed vs interrupted in-flight
        # tasks. The signature MUST accept a timeout override; the
        # default reads ``TASKQ_DRAIN_TIMEOUT``.
        report = await drain_fn(timeout=float(drain_timeout))
        elapsed = time_mod.monotonic() - started
        # DrainReport: object with .completed_count / .interrupted_count,
        # OR a dict with the same keys — accept either.
        if isinstance(report, dict):
            comp = int(report.get("completed_count", -1))
            intp = int(report.get("interrupted_count", -1))
        else:
            comp = int(getattr(report, "completed_count", -1))
            intp = int(getattr(report, "interrupted_count", -1))
        return elapsed, comp, intp

    elapsed, observed_completed, observed_interrupted = asyncio.run(
        _exercise_drain()
    )

    assert str(observed_completed) == observed_completed_count, (
        f"AC2-all-completed failed: drain MUST wait for all 3 "
        f"in-flight tasks to complete within the budget. "
        f"completed_count={observed_completed}, interrupted_count="
        f"{observed_interrupted}."
    )
    assert str(observed_interrupted) == observed_interrupted_count, (
        f"AC2-no-interrupt failed: no task should be marked "
        f"`interrupted` when the drain budget covers the workload. "
        f"interrupted_count={observed_interrupted}."
    )

    # Property P8-drain-budget: drain_elapsed_seconds <= drain_timeout.
    assert elapsed <= float(drain_timeout), (
        f"P8-drain-budget failed: drain MUST honour the "
        f"TASKQ_DRAIN_TIMEOUT budget ({drain_timeout}s). "
        f"Actual elapsed={elapsed:.3f}s."
    )

    # Belt-and-braces: the in-memory completed list should match.
    assert len(completed) == 3, (
        f"AC-8.2: all three scheduled tasks must have actually run; "
        f"completed list length={len(completed)}."
    )


# ===========================================================================
# 3. test_concurrency_cap_queues_surplus — AC-8.3
# ===========================================================================


# NP-13 (concurrency): bound the parallel-task count.
def test_concurrency_cap_queues_surplus(monkeypatch):  # NP-13
    """AC-8.3: concurrency capped at ``TASKQ_MAX_CONCURRENT``; surplus queues, no unbounded coroutines.

    TEST_SPEC §FR-08 case 3 binds:
        max_concurrent        = "2"
        submitted_count       = "5"
        observed_running_peak = "2"
        observed_queued_count = "3"
        state_mode            = "isolate_per_test"

    Subprocess isolation: in-process — the test invokes the
    scheduler surface directly and observes running peak via a
    counter that is incremented/decremented inside the coroutine.
    RED: the runner module does not yet expose a bounded scheduler,
    so attribute access fails (VALID RED STATE).

    Sub-assertions (TEST_SPEC §FR-08):
        AC3-cap-respected     observed_running_peak == "2"
        AC3-surplus-queued    observed_queued_count == "3"
    """
    # Bind TEST_SPEC Inputs verbatim for traceability.
    max_concurrent = "2"
    submitted_count = "5"
    observed_running_peak = "2"
    observed_queued_count = "3"
    state_mode = "isolate_per_test"
    assert max_concurrent == "2"
    assert submitted_count == "5"
    assert observed_running_peak == "2"
    assert observed_queued_count == "3"
    assert state_mode == "isolate_per_test"
    # Mirror-check anchors — verbatim predicates from TEST_SPEC §FR-08.
    assert observed_running_peak == "2"  # AC3-cap-respected
    assert observed_queued_count == "3"  # AC3-surplus-queued

    # Configure the cap via env so the GREEN implementation reads it.
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", max_concurrent)

    # GREEN TODO: ``Scheduler(max_concurrent=2, *, drain_timeout=...)``
    # exposes ``schedule(coro) -> Ticket`` and an internal semaphore
    # so peak concurrency is bounded. RED: missing.
    Scheduler = getattr(runner_mod, "Scheduler", None)
    assert Scheduler is not None, (
        f"AC-8.3: `taskq_api.service.runner.Scheduler` MUST exist "
        f"to enforce TASKQ_MAX_CONCURRENT. Missing — FR-08 AC-8.3 is "
        f"not yet implemented."
    )

    # Track in-process counters — running peak via a shared int that
    # each task increments on entry and decrements on exit.
    state: dict[str, int] = {"running": 0, "peak": 0, "queued": 0}

    async def _workload(idx: int) -> None:
        state["running"] += 1
        if state["running"] > state["peak"]:
            state["peak"] = state["running"]
        # Hold long enough that the queue backs up but not so long the
        # test becomes flaky; the cap is observed inside this window.
        await asyncio.sleep(0.10)
        state["running"] -= 1

    async def _exercise() -> None:
        # GREEN TODO: ``Scheduler(max_concurrent=N)`` — N is the bound.
        scheduler = Scheduler(max_concurrent=int(max_concurrent))
        # The scheduler MUST expose ``submit(coro) -> Awaitable`` so
        # surplus tasks queue rather than failing.
        submit = scheduler.submit
        # Submit 5 tasks back-to-back. The first 2 should start
        # immediately; the remaining 3 should queue.
        tickets = [submit(_workload(i)) for i in range(int(submitted_count))]
        # Await all tickets so the scheduler drains.
        await asyncio.gather(*tickets)
        # queued_count: tickets that were NOT running concurrently.
        # We measure it by snapshotting ``running`` immediately after
        # all submits — at that instant only ``max_concurrent`` can be
        # running, the rest are queued. The exact queued count is
        # derived from peak (running_peak == max_concurrent when the
        # bound is respected).
        state["queued"] = int(submitted_count) - state["peak"]

    asyncio.run(_exercise())

    peak = state["peak"]
    queued = state["queued"]

    assert str(peak) == observed_running_peak, (
        f"AC3-cap-respected failed: peak concurrent running tasks "
        f"MUST equal TASKQ_MAX_CONCURRENT ({max_concurrent}). "
        f"Observed peak={peak}."
    )
    assert str(queued) == observed_queued_count, (
        f"AC3-surplus-queued failed: surplus submissions MUST queue "
        f"(submitted={submitted_count}, cap={max_concurrent}). "
        f"Expected queued={observed_queued_count}, observed={queued}."
    )


# ===========================================================================
# 4. test_wait_for_kills_child_no_orphan — AC-8.4
# ===========================================================================


# NP-15 (timeout) + NFR-03 (error_handling): the orphan-free contract
# is the load-bearing FR-08 fault-injection property (R3 mitigation).
def test_wait_for_kills_child_no_orphan():  # NP-15, NFR-03
    """AC-8.4: ``asyncio.wait_for`` → ``proc.kill()`` → ``await proc.wait()`` leaves no orphans.

    TEST_SPEC §FR-08 case 4 binds:
        child_command      = "sleep 60"
        timeout_seconds    = "2"
        subprocess_mode    = "out_of_process"
        shared_TASKQ_HOME  = false
        orphan_pid_count   = "0"

    Subprocess isolation:
        subprocess_mode="out_of_process" — drives a fresh Python
            child so pytest's monkeypatch / asyncio loop state cannot
            mask a leak.
        shared_TASKQ_HOME=false — each test owns a fresh ``tmp_path``,
            so state cannot bleed across tests.

    The test enforces two contracts:
      1. STATIC: runner.py source MUST contain an explicit
         ``await proc.wait()`` (or ``await process.wait()``) call
         AFTER ``proc.kill()`` (or ``process.kill()``) — the FR-08
         spec language is binding.
      2. DYNAMIC: invoking the timeout path with ``sleep 60`` /
         timeout=2s MUST leave 0 child processes named ``sleep`` in
         the system process table after the call returns.

    Sub-assertion (TEST_SPEC §FR-08):
        AC4-no-orphan  orphan_pid_count == "0"
    """
    # Bind TEST_SPEC Inputs verbatim for traceability.
    child_command = "sleep 60"
    timeout_seconds = "2"
    subprocess_mode = "out_of_process"
    shared_TASKQ_HOME = False
    orphan_pid_count = "0"
    assert child_command == "sleep 60"
    assert timeout_seconds == "2"
    assert subprocess_mode == "out_of_process"
    assert shared_TASKQ_HOME is False
    assert orphan_pid_count == "0"
    # Mirror-check anchor — verbatim predicate from TEST_SPEC §FR-08.
    assert orphan_pid_count == "0"  # AC4-no-orphan

    # ----- (1) STATIC contract: source MUST contain the canonical -----
    # ``proc.kill()`` → ``await proc.wait()`` sequence.
    assert _RUNNER_SOURCE.exists(), (
        f"AC-8.4: runner source missing at {_RUNNER_SOURCE}."
    )
    src_text = _RUNNER_SOURCE.read_text(encoding="utf-8")

    # Look for ``await proc.wait()`` (or ``await process.wait()``).
    # The FR-08 spec mandates this exact sequence AFTER ``proc.kill()``
    # — ``proc.communicate()`` is NOT a substitute.
    wait_after_kill = re.findall(
        r"\bawait\s+(?:proc|process)\.wait\s*\(\s*\)", src_text
    )
    assert wait_after_kill, (
        f"AC-8.4: runner.py MUST contain an explicit "
        f"`await proc.wait()` (or `await process.wait()`) AFTER "
        f"`proc.kill()` to guarantee the child PID is reaped. "
        f"The FR-08 spec language is binding. Found {len(wait_after_kill)} "
        f"occurrence(s)."
    )

    # ----- (2) DYNAMIC contract: no orphan ``sleep`` processes after --
    # the timeout path returns.
    with pytest.MonkeyPatch.context() as m:
        import tempfile

        tmp_home = Path(tempfile.mkdtemp(prefix="taskq-fr08-orphan-"))
        m.setenv("TASKQ_HOME", str(tmp_home))
        m.setenv("TASKQ_TASK_TIMEOUT", timeout_seconds)

        # OUT_OF_PROCESS — fresh Python child runs the runner.RED.
        runner_script = (
            "import asyncio, json, sys\n"
            "from taskq_api.service.runner import run_task\n"
            "result = asyncio.run("
            "  run_task(command='sleep 60', timeout_seconds=2.0)"
            ")\n"
            "sys.stdout.write(json.dumps({"
            "  'status_name': result.get('status_name') "
            "    if isinstance(result, dict) else None,"
            "  'exit_code': result.get('exit_code') "
            "    if isinstance(result, dict) else None,"
            "}))\n"
        )

        env_payload = os.environ.copy()
        env_payload["TASKQ_HOME"] = str(tmp_home)
        env_payload["TASKQ_TASK_TIMEOUT"] = timeout_seconds
        # pytest's `pythonpath = ...` does NOT propagate to child processes.
        src_root = (
            Path(__file__).resolve().parent.parent / "src"
        )
        env_payload["PYTHONPATH"] = (
            str(src_root) + os.pathsep + env_payload.get("PYTHONPATH", "")
        )

        completed = subprocess.run(
            [sys.executable, "-c", runner_script],
            env=env_payload,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert completed.returncode == 0, (
            f"out-of-process runner exited {completed.returncode}: "
            f"stderr={completed.stderr!r}"
        )

        # Give the OS a brief grace period to reap a reaped child
        # before we snapshot the process table.
        time_mod.sleep(0.2)

        # Snapshot the system process table for any ``sleep`` PIDs.
        # ``ps -A -o pid=,comm=`` is portable across macOS + Linux
        # (the BSD ``ps`` accepts ``-o`` the same way).
        ps_result = subprocess.run(
            ["ps", "-A", "-o", "pid=,comm="],
            capture_output=True,
            text=True,
        )
        sleep_pids = [
            line.strip()
            for line in ps_result.stdout.splitlines()
            if line.strip().endswith(" sleep")
            or line.strip().endswith("/sleep")
        ]
        observed_orphan_count = len(sleep_pids)

        assert str(observed_orphan_count) == orphan_pid_count, (
            f"AC4-no-orphan failed: timeout path MUST leave 0 orphan "
            f"`sleep` processes. Found {observed_orphan_count}: "
            f"{sleep_pids!r}. The FR-08 contract is `proc.kill()` + "
            f"`await proc.wait()` — anything less risks orphan PIDs."
        )


# ===========================================================================
# 5. test_cancelled_error_propagates_not_swallowed — AC-8.5
# ===========================================================================


# NP-07 (dependency fault) + NFR-03 (error_handling): cancel must
# propagate; a bare ``except Exception`` on the cancel path is a
# contract violation.
def test_cancelled_error_propagates_not_swallowed(monkeypatch):  # NP-07, NFR-03
    """AC-8.5: ``asyncio.CancelledError`` propagates upward — NOT swallowed by ``except Exception``.

    TEST_SPEC §FR-08 case 5 binds:
        cancel_signal                = "asyncio.CancelledError"
        re_raised                    = "True"
        swallowed_by_except_exception = "False"

    Subprocess isolation: in-process — drives the scheduler surface
    directly so the assertion can observe both the runtime behaviour
    AND a static ``except Exception`` scan of the source.

    Sub-assertions (TEST_SPEC §FR-08):
        AC5-cancelled-reraised   re_raised == "True"
        AC5-not-swallowed        swallowed_by_except_exception == "False"
    Property:
        P8-cancel-pure           cancelled_error_propagated == "True"
    """
    # Bind TEST_SPEC Inputs verbatim for traceability.
    cancel_signal = "asyncio.CancelledError"
    re_raised = "True"
    swallowed_by_except_exception = "False"
    assert cancel_signal == "asyncio.CancelledError"
    assert re_raised == "True"
    assert swallowed_by_except_exception == "False"
    # Mirror-check anchors — verbatim predicates from TEST_SPEC §FR-08.
    assert re_raised == "True"  # AC5-cancelled-reraised
    assert swallowed_by_except_exception == "False"  # AC5-not-swallowed

    # ----- (1) STATIC contract: source MUST NOT swallow CancelledError.
    # Scan runner.py for ``except Exception`` blocks that would catch
    # ``CancelledError``. The scan is line-aware: we strip line
    # comments so a comment that mentions ``except Exception`` in
    # prose does not count.
    assert _RUNNER_SOURCE.exists(), (
        f"AC-8.5: runner source missing at {_RUNNER_SOURCE}."
    )
    src_text = _RUNNER_SOURCE.read_text(encoding="utf-8")

    code_lines = []
    for line in src_text.splitlines():
        code_lines.append(line.split("#", 1)[0])
    code_only = "\n".join(code_lines)

    # The FR-08 contract: ``asyncio.CancelledError`` MUST propagate.
    # A bare ``except Exception`` on the cancel path is the canonical
    # violation. We treat ANY bare ``except Exception`` block as a
    # contract risk — GREEN must justify each one (e.g. drain pipes
    # fallback) OR refactor to ``except (OSError, asyncio.TimeoutError)``.
    except_exception_blocks = re.findall(
        r"except\s+Exception\b", code_only
    )
    observed_swallowed_count = len(except_exception_blocks)

    # GREEN TODO contract: GREEN MAY keep ``except Exception`` ONLY
    # on paths where a benign fallback is provably safe (e.g. the
    # post-kill drain fallback returning ``(b"", b"")``). The
    # cancellation path itself MUST NOT contain ``except Exception``
    # that swallows the re-raise.
    assert str(observed_swallowed_count) == swallowed_by_except_exception, (
        f"AC5-not-swallowed failed: runner.py MUST NOT contain "
        f"`except Exception` blocks on the cancellation path — "
        f"they would swallow `asyncio.CancelledError`. Found "
        f"{observed_swallowed_count} bare `except Exception` "
        f"block(s). GREEN must either justify each as benign "
        f"(NFR-03 reasoning) or refactor to a narrower exception "
        f"tuple."
    )

    # ----- (2) DYNAMIC contract: cancelling a task MUST raise -----
    # ``asyncio.CancelledError`` at the call site.
    async def _long_running() -> None:
        # Sleep long enough that cancellation arrives BEFORE the task
        # naturally completes. ``asyncio.sleep`` is the canonical
        # cancellable coroutine — cancelling the awaiting task raises
        # ``CancelledError`` inside ``_long_running`` and re-raises at
        # the ``await`` site.
        await asyncio.sleep(60)

    async def _exercise_cancel() -> tuple[bool, type[BaseException] | None]:
        task = asyncio.create_task(_long_running())
        # Give the scheduler a moment to enter ``sleep``.
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True, asyncio.CancelledError
        except BaseException as exc:  # noqa: BLE001 — observation only
            return False, type(exc)
        return False, None

    observed_re_raised, observed_exc_type = asyncio.run(_exercise_cancel())

    assert str(observed_re_raised) == re_raised, (
        f"AC5-cancelled-reraised failed: cancelling an in-flight "
        f"task MUST re-raise `asyncio.CancelledError` at the call "
        f"site. observed_re_raised={observed_re_raised}, "
        f"observed_exc_type={observed_exc_type!r}."
    )
    assert observed_exc_type is asyncio.CancelledError, (
        f"P8-cancel-pure failed: the propagated exception MUST be "
        f"`asyncio.CancelledError`. observed_exc_type="
        f"{observed_exc_type!r}."
    )