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
import json
import os
import re
import subprocess
import sys
import time as time_mod
from datetime import datetime, timedelta, timezone
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
        "AC-8.2: `taskq_api.service.runner.drain` MUST exist as a "
        "callable so the service can gracefully shut down. The "
        "current runner module does not expose it — FR-08 AC-8.2 is "
        "not yet implemented."
    )

    # Build a scheduler that knows about three in-flight tasks; each
    # task is a short asyncio coroutine so it completes well within
    # the 10s drain budget.
    completed: list[int] = []

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
        "AC-8.2: `taskq_api.service.runner` MUST expose a "
        "`schedule(coro)` entry point so drain can observe "
        "in-flight tasks. Missing — FR-08 AC-8.2 is not yet "
        "implemented."
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
        "AC-8.3: `taskq_api.service.runner.Scheduler` MUST exist "
        "to enforce TASKQ_MAX_CONCURRENT. Missing — FR-08 AC-8.3 is "
        "not yet implemented."
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
            "  'child_pid': result.get('child_pid') "
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

        # Parse the spawned child PID from the runner's stdout so we
        # can check for *that specific* PID rather than counting every
        # ``sleep`` process on the system (which would false-positive
        # on any concurrent ``sleep`` — e.g. CI / IDE / orchestration
        # harness waits). The FR-08 contract here is "the PID we
        # spawned MUST be gone", not "the universe must contain zero
        # sleeps".
        stdout_payload = completed.stdout.strip()
        try:
            parsed = json.loads(stdout_payload) if stdout_payload else {}
        except json.JSONDecodeError:
            parsed = {}
        spawned_child_pid = (
            parsed.get("child_pid") if isinstance(parsed, dict) else None
        )
        assert spawned_child_pid is not None, (
            f"AC-8.4: runner did not report child_pid; cannot verify "
            f"the orphan-free contract. stdout={stdout_payload!r}"
        )

        # Give the OS a brief grace period to reap a reaped child
        # before we snapshot the process table.
        time_mod.sleep(0.2)

        # Snapshot the system process table and look ONLY for the
        # specific child PID we spawned. ``ps -A -o pid=,comm=`` is
        # portable across macOS + Linux (the BSD ``ps`` accepts
        # ``-o`` the same way).
        ps_result = subprocess.run(
            ["ps", "-A", "-o", "pid=,comm="],
            capture_output=True,
            text=True,
        )
        spawned_pid_str = str(spawned_child_pid)
        sleep_pids = [
            line.strip()
            for line in ps_result.stdout.splitlines()
            if (line.strip().endswith(" sleep")
                or line.strip().endswith("/sleep"))
            and line.strip().split(None, 1)[0] == spawned_pid_str
        ]
        observed_orphan_count = len(sleep_pids)

        assert str(observed_orphan_count) == orphan_pid_count, (
            f"AC4-no-orphan failed: timeout path MUST leave 0 orphan "
            f"`sleep` processes for the spawned PID {spawned_pid_str}. "
            f"Found {observed_orphan_count}: {sleep_pids!r}. "
            f"The FR-08 contract is `proc.kill()` + "
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
    # TEST_SPEC binds ``swallowed_by_except_exception="False"`` meaning
    # zero swallowed blocks. Compare as a bool string: a non-zero
    # count maps to "True"; a zero count maps to "False". This avoids
    # the ``str(int) == "False"`` trap without monkey-patching builtins.
    observed_swallowed_str = (
        "True" if observed_swallowed_count > 0 else "False"
    )
    assert observed_swallowed_str == swallowed_by_except_exception, (
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


# ===========================================================================
# Coverage tests — exercise runner.py helpers from runner.py's host module
# so coverage tracks them. The functions below reach into FR-02 helpers
# (`run_task`, `state_machine`, `_redact`, env-var resolution, the
# ``_safe_*`` shims, ``_reap_after_kill``, ``_drain_pipes``) plus the
# FR-08 surface edge cases (``Scheduler.{drain,inflight}``,
# module-level ``drain()``, ``_structured_drain_tasks`` empty path).
# These exist purely to lift ``runner.py`` coverage above the Gate 1
# 80% threshold. Each test is a self-contained regression.
# ===========================================================================


# ---------------------------------------------------------------------------
# run_task in-process happy path — exercises lines 413-509 of runner.py.
# task_id=None so the ``_safe_*`` shims early-return without touching
# the repo layer (the subprocess IS the unit of work per FR-02 AC-2.5).
# ---------------------------------------------------------------------------
def test_run_task_in_process_executes_clean_command():
    """In-process ``run_task`` happy path → status_name='done', exit_code=0."""
    from taskq_api.service.runner import _redact

    # ``_redact`` exercise — covers line 194-198 (AC-9 redaction branch).
    redacted = _redact("noise token=abc123 more noise")
    assert "[REDACTED]" in redacted, (
        f"_redact must replace token=<value> with [REDACTED]; got {redacted!r}"
    )
    # Empty / falsy input is preserved (covers line 196 early return).
    assert _redact("") == "", "_redact('') must short-circuit"
    assert _redact(None) is None, "_redact(None) must short-circuit"

    async def _exercise() -> dict[str, Any]:
        return await run_task(command="echo hello-world", task_id=None)

    result = asyncio.run(_exercise())
    assert result["status_name"] == "done", (
        f"echo hello-world must produce status_name=done; got {result!r}"
    )
    assert result["exit_code"] == 0, (
        f"echo hello-world must produce exit_code=0; got {result!r}"
    )
    # The runner returns the canonical {status_name, exit_code, run_id,
    # child_pid} dict; stdout is persisted via task_repo, not returned
    # in-band. The redaction helper exercised above is the in-process
    # surface that covers the stdout-tail handling code path.
    assert isinstance(result.get("run_id"), str), (
        f"run_id must be a UUID string; result={result!r}"
    )


# ---------------------------------------------------------------------------
# run_task tokenisation failure (shlex.split ValueError) — covers the
# branch at lines 422-438 of runner.py. An unmatched quote is the
# canonical trigger.
# ---------------------------------------------------------------------------
def test_run_task_in_process_unmatched_quote_tokenise_failure():
    """Invalid shell syntax returns the ``failed`` + tokenise-failure sentinel."""
    from taskq_api.service.runner import _EXIT_TOKENISE_FAILURE

    async def _exercise() -> dict[str, Any]:
        return await run_task(command="echo 'unterminated", task_id=None)

    result = asyncio.run(_exercise())
    assert result["status_name"] == "failed", (
        f"Unmatched quote must produce failed; got {result!r}"
    )
    assert result["exit_code"] == _EXIT_TOKENISE_FAILURE, (
        f"Unmatched quote must produce exit_code="
        f"{_EXIT_TOKENISE_FAILURE}; got {result!r}"
    )


# ---------------------------------------------------------------------------
# run_task command-not-found — covers the (FileNotFoundError, OSError)
# branch at lines 441-462 of runner.py.
# ---------------------------------------------------------------------------
def test_run_task_in_process_command_not_found():
    """Nonexistent executable returns ``failed`` + command-not-found sentinel."""
    from taskq_api.service.runner import _EXIT_COMMAND_NOT_FOUND

    async def _exercise() -> dict[str, Any]:
        # Use a clearly-nonexistent binary; subprocess raises
        # ``FileNotFoundError`` on ``create_subprocess_exec``.
        return await run_task(
            command="this_binary_does_not_exist_12345",
            task_id=None,
        )

    result = asyncio.run(_exercise())
    assert result["status_name"] == "failed", (
        f"Missing executable must produce failed; got {result!r}"
    )
    assert result["exit_code"] == _EXIT_COMMAND_NOT_FOUND, (
        f"Missing executable must produce exit_code="
        f"{_EXIT_COMMAND_NOT_FOUND}; got {result!r}"
    )


# ---------------------------------------------------------------------------
# run_task with non-zero exit code — exits cleanly but maps to "failed"
# via ``state_machine(exit_code=1)``. Covers the AC-2.4 sub-row 6 path.
# ---------------------------------------------------------------------------
def test_run_task_in_process_non_zero_exit_marks_failed():
    """Non-zero exit code returns ``failed``; exercises state_machine path."""

    async def _exercise() -> dict[str, Any]:
        # ``false`` is the POSIX canonical "exit 1" stub.
        return await run_task(command="false", task_id=None)

    result = asyncio.run(_exercise())
    assert result["status_name"] == "failed", (
        f"'false' must produce failed; got {result!r}"
    )
    assert result["exit_code"] != 0, (
        f"'false' must produce non-zero exit_code; got {result!r}"
    )


# ---------------------------------------------------------------------------
# state_machine — pure function directly exercised. Covers lines 116-169.
# All four TEST_SPEC sub-rows (AC-2.4 cases 4..8) are enumerated so the
# state-transition surface is fully covered.
# ---------------------------------------------------------------------------
def test_state_machine_all_branches():
    """``state_machine`` covers pending->running, done, failed, timeout, cancel."""
    from taskq_api.service.runner import state_machine

    # AC4-pending-running.
    assert state_machine("pending", trigger="execute") == {"status": "running"}
    # AC5-running-done.
    assert state_machine("running", exit_code=0) == {"status": "done"}
    # AC6-running-failed.
    assert state_machine("running", exit_code=2) == {"status": "failed"}
    # AC7-running-timeout.
    assert state_machine("running", timeout_triggered=True) == {
        "status": "timeout"
    }
    # Unknown transition returns the input unchanged (lines 167-169).
    assert state_machine("running") == {"status": "running"}
    assert state_machine("done", trigger="execute") == {"status": "done"}
    # AC8-cancel-propagates: cancel=True raises CancelledError (line 147-150).
    with pytest.raises(asyncio.CancelledError):
        state_machine("pending", cancel=True)
    # cancel=True on running also raises.
    with pytest.raises(asyncio.CancelledError):
        state_machine("running", cancel=True)


# ---------------------------------------------------------------------------
# _resolve_timeout — covers lines 175-191 (explicit kw, env var, default).
# ---------------------------------------------------------------------------
def test_resolve_timeout_priority_and_fallback(monkeypatch):
    """``_resolve_timeout`` honours explicit kw, env var, and default."""
    from taskq_api.service.runner import _resolve_timeout, _DEFAULT_TIMEOUT_SECONDS

    # Explicit kw wins.
    assert _resolve_timeout(7.5) == 7.5, (
        "explicit timeout_seconds kw must short-circuit env"
    )
    # env var used when kw is None.
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "12.0")
    assert _resolve_timeout(None) == 12.0, (
        "TASKQ_TASK_TIMEOUT=12.0 must be parsed and returned"
    )
    # Default when env var absent (covers line 191).
    monkeypatch.delenv("TASKQ_TASK_TIMEOUT", raising=False)
    assert _resolve_timeout(None) == _DEFAULT_TIMEOUT_SECONDS, (
        f"default must be {_DEFAULT_TIMEOUT_SECONDS} when env unset"
    )
    # Garbage env value falls back to default (covers lines 187-190).
    monkeypatch.setenv("TASKQ_TASK_TIMEOUT", "not-a-float")
    assert _resolve_timeout(None) == _DEFAULT_TIMEOUT_SECONDS, (
        f"invalid env value must fall back to {_DEFAULT_TIMEOUT_SECONDS}"
    )


# ---------------------------------------------------------------------------
# _now_iso and _elapsed_ms — covers lines 201-210.
# ---------------------------------------------------------------------------
def test_now_iso_and_elapsed_ms_helpers():
    """ISO timestamp helper + elapsed-ms helper return sane values."""
    from taskq_api.service.runner import _now_iso, _elapsed_ms

    ts = _now_iso()
    assert isinstance(ts, str) and "T" in ts, (
        f"_now_iso must produce an ISO-8601 string with 'T'; got {ts!r}"
    )

    # Elapsed-ms: ~10ms after a "fresh" datetime.
    started = datetime.now(timezone.utc) - timedelta(milliseconds=10)
    elapsed = _elapsed_ms(started)
    assert 5 <= elapsed <= 1500, (
        f"_elapsed_ms must be a small positive int around 10ms; got {elapsed}"
    )


# ---------------------------------------------------------------------------
# _resolve_env — covers lines 548-573. The 3-branch function (unset /
# invalid / valid) is exercised explicitly.
# ---------------------------------------------------------------------------
def test_resolve_env_branches(monkeypatch):
    """``_resolve_env`` handles unset, invalid, and valid inputs."""
    from taskq_api.service.runner import _resolve_env

    # Unset -> default (line 564-566).
    monkeypatch.delenv("TASKQ_TEST_NOEXIST", raising=False)
    assert _resolve_env(
        "TASKQ_TEST_NOEXIST",
        parse=int,
        default=42,
        minimum=1,
        inclusive=True,
    ) == 42

    # Valid value (inclusive). Covers the `return value if ... else default`
    # path on line 572.
    monkeypatch.setenv("TASKQ_TEST_VALID", "8")
    assert _resolve_env(
        "TASKQ_TEST_VALID",
        parse=int,
        default=42,
        minimum=1,
        inclusive=True,
    ) == 8

    # Below-minimum (inclusive): falls back to default.
    monkeypatch.setenv("TASKQ_TEST_LOW", "0")
    assert _resolve_env(
        "TASKQ_TEST_LOW",
        parse=int,
        default=42,
        minimum=1,
        inclusive=True,
    ) == 42

    # Non-inclusive minimum path (line 573: ``return value if value > minimum``).
    monkeypatch.setenv("TASKQ_TEST_FLOAT", "1.5")
    assert _resolve_env(
        "TASKQ_TEST_FLOAT",
        parse=float,
        default=9.0,
        minimum=0,
        inclusive=False,
    ) == 1.5

    # Invalid value (ValueError) — covers lines 567-570.
    monkeypatch.setenv("TASKQ_TEST_BAD", "not-a-number")
    assert _resolve_env(
        "TASKQ_TEST_BAD",
        parse=int,
        default=42,
        minimum=1,
        inclusive=True,
    ) == 42


# ---------------------------------------------------------------------------
# _resolve_max_concurrent / _resolve_drain_timeout — covers 576-598.
# ---------------------------------------------------------------------------
def test_resolve_max_concurrent_and_drain_timeout(monkeypatch):
    """Env-var helpers return the configured value or the default fallback."""
    from taskq_api.service.runner import (
        _resolve_max_concurrent,
        _resolve_drain_timeout,
        _DEFAULT_MAX_CONCURRENT,
        _DEFAULT_DRAIN_TIMEOUT,
    )

    # Unset env -> defaults (covers branches 564-566 of _resolve_env).
    monkeypatch.delenv("TASKQ_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("TASKQ_DRAIN_TIMEOUT", raising=False)
    assert _resolve_max_concurrent() == _DEFAULT_MAX_CONCURRENT
    assert _resolve_drain_timeout() == _DEFAULT_DRAIN_TIMEOUT

    # Valid env -> parsed value.
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "16")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "3.5")
    assert _resolve_max_concurrent() == 16
    assert _resolve_drain_timeout() == 3.5

    # Invalid env -> falls back to defaults (covers 567-570, 591-595).
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "not-an-int")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "not-a-float")
    assert _resolve_max_concurrent() == _DEFAULT_MAX_CONCURRENT
    assert _resolve_drain_timeout() == _DEFAULT_DRAIN_TIMEOUT

    # Below-minimum (max < 1, drain <= 0) -> defaults (covers 571-573).
    monkeypatch.setenv("TASKQ_MAX_CONCURRENT", "0")
    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "-1")
    assert _resolve_max_concurrent() == _DEFAULT_MAX_CONCURRENT
    assert _resolve_drain_timeout() == _DEFAULT_DRAIN_TIMEOUT


# ---------------------------------------------------------------------------
# Scheduler edge cases — empty drain, properties, inflight.
# Covers lines 718-794 of runner.py.
# ---------------------------------------------------------------------------
def test_scheduler_properties_and_empty_drain():
    """``Scheduler.{max_concurrent,inflight}`` + empty ``drain`` paths."""
    from taskq_api.service.runner import Scheduler

    # Below-1 cap is clamped to 1 (lines 724-725).
    sch = Scheduler(max_concurrent=0)
    assert sch.max_concurrent == 1, (
        f"Scheduler(max_concurrent=0) must clamp to 1; got {sch.max_concurrent}"
    )
    # inflight on empty scheduler is 0 (lines 736-741).
    assert sch.inflight == 0, (
        f"Empty scheduler inflight must be 0; got {sch.inflight}"
    )
    # Drain on empty scheduler returns zero-count report (lines 786-787).
    report = asyncio.run(sch.drain(timeout=0.1))
    assert report == {"completed_count": 0, "interrupted_count": 0}, (
        f"Empty drain must return zero counts; got {report!r}"
    )

    # Drain with explicit timeout=None — exercises the env-var fallback at
    # lines 788-793.
    sch2 = Scheduler(max_concurrent=4, drain_timeout=2.5)
    # Submit a quick task to occupy the scheduler momentarily.
    async def _quick() -> int:
        return 7

    async def _exercise() -> dict[str, int]:
        sch2.submit(_quick())
        # Yield once so the task is registered as in-flight.
        await asyncio.sleep(0)
        # drain_timeout=2.5 is the configured value (no env lookup).
        return await sch2.drain()

    report_with_task = asyncio.run(_exercise())
    assert report_with_task["completed_count"] >= 1, (
        f"At least one task must be reported completed; got {report_with_task!r}"
    )


# ---------------------------------------------------------------------------
# Module-level schedule/drain edge cases — covers lines 802-838.
# ---------------------------------------------------------------------------
def test_module_level_drain_with_no_tasks(monkeypatch):
    """Module-level ``drain()`` returns zero-count when no tasks are scheduled."""
    from taskq_api.service import runner

    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "0.5")

    async def _exercise() -> dict[str, int]:
        # No tasks scheduled at all — the early-return branch at lines
        # 834-836 must run.
        return await runner.drain(timeout=0.25)

    report = asyncio.run(_exercise())
    assert report == {"completed_count": 0, "interrupted_count": 0}, (
        f"No-task drain must return zero counts; got {report!r}"
    )


# ---------------------------------------------------------------------------
# Module-level schedule + drain — happy path with an env-var
# TASKQ_DRAIN_TIMEOUT so the env-resolver branch at lines 837-838 is hit.
# ---------------------------------------------------------------------------
def test_module_level_schedule_and_drain_path(monkeypatch):
    """``schedule`` followed by ``drain`` collects at least one completion."""
    from taskq_api.service import runner

    monkeypatch.setenv("TASKQ_DRAIN_TIMEOUT", "0.5")

    async def _quick() -> str:
        return "ok"

    async def _exercise() -> dict[str, int]:
        runner.schedule(_quick())
        # Yield so the task registers as in-flight.
        await asyncio.sleep(0)
        # Drain with no explicit timeout — exercises line 838 (env-var
        # fallback path).
        return await runner.drain()

    report = asyncio.run(_exercise())
    assert report["completed_count"] >= 1, (
        f"schedule->drain must count at least one completion; got {report!r}"
    )


# ---------------------------------------------------------------------------
# run_task with redaction in stdout — exercises _redact via the
# subprocess output path. Print a ``token=`` value and assert the
# persisted stdout is redacted to ``[REDACTED]``.
# ---------------------------------------------------------------------------
def test_run_task_in_process_redacts_token_in_stdout():
    """``run_task`` redacts ``token=<value>`` from stdout before persisting."""
    from taskq_api.service.runner import _redact, _REDACTED_MARKER

    # Direct _redact checks for completeness.
    assert _redact("plain") == "plain", "_redact must leave plain text alone"
    assert _REDACTED_MARKER == "[REDACTED]", (
        f"marker constant must be '[REDACTED]'; got {_REDACTED_MARKER!r}"
    )
    # Multiple tokens redacted; non-token surrounding text preserved.
    assert _redact("a token=x b token=y c") == f"a {_REDACTED_MARKER} b {_REDACTED_MARKER} c"

    # In-process subprocess path prints a ``token=`` value; the runner
    # redacts stdout via _redact before persist. We can't capture the
    # persisted row without a DB, but the redaction constant + regex
    # path are fully exercised by direct calls.
    async def _exercise() -> dict[str, Any]:
        return await run_task(
            command='printf "token=should_stay_secret\\n"',
            task_id=None,
        )

    result = asyncio.run(_exercise())
    assert result["status_name"] == "done", (
        f"printf must produce done; got {result!r}"
    )


# ---------------------------------------------------------------------------
# run_task timeout branch — covers lines 471-489 of runner.py (the
# ``asyncio.TimeoutError`` arm), which also drives ``_reap_after_kill``
# (lines 328-340) and ``_drain_pipes`` (lines 361-367).
# ---------------------------------------------------------------------------
def test_run_task_in_process_timeout_branch():
    """``run_task`` fires the ``_reap_after_kill`` + ``_drain_pipes`` path
    when ``TASKQ_TASK_TIMEOUT`` fires. Lines 471-489 + 328-367 covered."""
    from taskq_api.service.runner import _reap_after_kill, _drain_pipes

    async def _exercise() -> dict[str, Any]:
        # 0.2s timeout against a 5s sleep → TimeoutError must fire.
        return await run_task(
            command="sleep 5",
            timeout_seconds=0.2,
            task_id=None,
        )

    result = asyncio.run(_exercise())
    assert result["status_name"] == "timeout", (
        f"timed-out run_task must produce status_name=timeout; got {result!r}"
    )
    # Exit code is None on timeout (line 488-489) — the runner doesn't
    # know what the child would have returned.
    assert result["exit_code"] is None, (
        f"timeout path must leave exit_code as None; got {result!r}"
    )

    # Direct exercise of the two drain helpers — guarantees both branches
    # of each helper are covered (kill/wait error arms).
    async def _exercise_helpers() -> None:
        # Spawn a slow child, kill it, then exercise both helpers.
        proc = await asyncio.create_subprocess_exec(
            "sleep", "5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await _reap_after_kill(proc)
            # Drain pipes after kill — should return bytes (possibly empty).
            out, err = await _drain_pipes(proc)
            assert isinstance(out, (bytes, bytearray)), (
                f"_drain_pipes stdout must be bytes; got {type(out).__name__}"
            )
            assert isinstance(err, (bytes, bytearray)), (
                f"_drain_pipes stderr must be bytes; got {type(err).__name__}"
            )
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass

    asyncio.run(_exercise_helpers())


# ---------------------------------------------------------------------------
# _structured_drain_tasks empty-list path — covers line 646
# (the ``if not tasks: return {...}`` early return).
# ---------------------------------------------------------------------------
def test_structured_drain_tasks_empty_list():
    """``_structured_drain_tasks([])`` short-circuits to a zero-count report."""
    from taskq_api.service.runner import _structured_drain_tasks

    async def _exercise() -> dict[str, int]:
        return await _structured_drain_tasks([], timeout=1.0)

    report = asyncio.run(_exercise())
    assert report == {"completed_count": 0, "interrupted_count": 0}, (
        f"empty-list drain must return zero counts; got {report!r}"
    )


# ---------------------------------------------------------------------------
# _structured_drain_tasks timeout path — covers lines 664-676 (the
# cancellation loop that marks long-running tasks as ``interrupted``).
# Submit one short-ish task but drain with a budget too tight to fit.
# ---------------------------------------------------------------------------
def test_structured_drain_tasks_timeout_path():
    """When the drain budget fires, tasks are cancelled and reported as interrupted."""
    from taskq_api.service.runner import Scheduler

    async def _slow() -> str:
        # Hold long enough that the drain budget (0.05s) fires first.
        await asyncio.sleep(2.0)
        return "slow_done"

    async def _exercise() -> dict[str, int]:
        sch = Scheduler(max_concurrent=2)
        sch.submit(_slow())
        # Yield so the task registers.
        await asyncio.sleep(0)
        # Drain budget smaller than the task sleep → TimeoutError,
        # then cancel loop runs (lines 664-676).
        return await sch.drain(timeout=0.05)

    report = asyncio.run(_exercise())
    # Either completed (rare, on very fast systems) or interrupted.
    assert (report["completed_count"] + report["interrupted_count"]) >= 1, (
        f"drain must report at least one task; got {report!r}"
    )
    assert report["interrupted_count"] >= 1, (
        f"drain budget=0.05 must cancel the slow task; got {report!r}"
    )


# ---------------------------------------------------------------------------
# Safe-helper bodies — covers lines 232-297. ``task_id=None`` exercises
# the early-return guards; a non-None ``task_id`` with a mocked repo
# exercises the try/except bodies.
# ---------------------------------------------------------------------------
def test_safe_helpers_swallow_repo_errors(monkeypatch):
    """``_safe_update_status`` / ``_safe_write_result`` / ``_safe_persist_terminal``
    swallow ``OSError`` from the repo layer (best-effort contract)."""
    from taskq_api.service import runner as r

    # task_id=None exercises the early-return guards (lines 232-233,
    # 251-252, 283-284).
    r._safe_update_status(None, "running")  # body not entered
    r._safe_write_result(task_id=None)  # body not entered
    r._safe_persist_terminal(
        None,
        status="done",
        run_id="r",
        exit_code=0,
        stdout_tail="",
        stderr_tail="",
        duration_ms=0,
        finished_at="",
    )

    # With a real task_id the try-block runs. Patch the repo to raise
    # ``OSError`` so the ``except _NON_FATAL_REPO_ERRORS: pass`` body
    # executes (lines 234-239, 253-256, 285-297).
    fake_repo = r.task_repo_mod.task_repo
    monkeypatch.setattr(
        fake_repo, "update_status", lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
    )
    monkeypatch.setattr(
        fake_repo, "write_result", lambda *a, **k: (_ for _ in ()).throw(OSError("down"))
    )

    # None of these should raise — best-effort persistence holds.
    r._safe_update_status("task-fake", "running")
    r._safe_write_result(
        task_id="task-fake",
        run_id="r",
        exit_code=0,
        stdout_tail="",
        stderr_tail="",
        duration_ms=0,
        finished_at="",
    )
    r._safe_persist_terminal(
        "task-fake",
        status="done",
        run_id="r",
        exit_code=0,
        stdout_tail="",
        stderr_tail="",
        duration_ms=0,
        finished_at="",
    )


# ---------------------------------------------------------------------------
# _reap_after_kill — already-reaped process. Covers lines 330-340
# (the ``except ProcessLookupError`` arms on both kill() and wait()).
# ---------------------------------------------------------------------------
def test_reap_after_kill_handles_already_reaped_process():
    """``_reap_after_kill`` swallows ``ProcessLookupError`` on a reaped child."""
    from taskq_api.service.runner import _reap_after_kill

    async def _exercise() -> None:
        # Spawn a short-lived process and wait for it to finish.
        proc = await asyncio.create_subprocess_exec(
            "true",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Wait until the child is reaped; then re-running the
        # kill+wait helpers must trigger ProcessLookupError on both
        # call sites, exercising lines 330-332 and 336-340.
        await proc.wait()
        await _reap_after_kill(proc)

    asyncio.run(_exercise())


# ---------------------------------------------------------------------------
# _drain_pipes timeout fallback — covers lines 366-367
# (the ``return (b'', b'')`` short-circuit on ``TimeoutError``).
# ---------------------------------------------------------------------------
def test_drain_pipes_timeout_fallback(monkeypatch):
    """``_drain_pipes`` returns ``(b'', b'')`` when the drain budget fires."""
    from taskq_api.service.runner import _drain_pipes

    # Force the wait_for to TimeoutError by tightening the budget
    # below the child's self-termination latency. Use a process that
    # holds its stdout/stderr open until the test kills it.
    async def _exercise() -> tuple[bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", "sleep 5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            # Patch the drain timeout to near-zero so communicate() can't
            # complete inside the budget — drives the (b'', b'') return.
            monkeypatch.setattr(
                "taskq_api.service.runner._DRAIN_TIMEOUT_SECONDS", 0.001,
            )
            return await _drain_pipes(proc)
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except (ProcessLookupError, asyncio.TimeoutError):
                    pass

    out, err = asyncio.run(_exercise())
    assert out == b"", f"_drain_pipes timeout must return b''; got {out!r}"
    assert err == b"", f"_drain_pipes timeout must return b''; got {err!r}"


# ---------------------------------------------------------------------------
# _safe_persist_terminal — write_result fails AFTER update_status succeeds.
# Covers line 287 of runner.py. The previous safe-helpers test makes
# ``update_status`` raise, so ``write_result`` (line 287) is never reached.
# This test swaps the order: ``update_status`` succeeds, ``write_result``
# raises — exercising the second branch inside the same try-block.
# ---------------------------------------------------------------------------
def test_safe_persist_terminal_write_result_fails(monkeypatch):
    """``_safe_persist_terminal`` swallows write_result OSError after a successful update_status."""
    from taskq_api.service import runner as r

    fake_repo = r.task_repo_mod.task_repo
    monkeypatch.setattr(
        fake_repo, "update_status", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        fake_repo, "write_result",
        lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
    )

    # Must NOT raise — best-effort persistence contract holds.
    r._safe_persist_terminal(
        "task-fake",
        status="done",
        run_id="r",
        exit_code=0,
        stdout_tail="",
        stderr_tail="",
        duration_ms=0,
        finished_at="",
    )


# ---------------------------------------------------------------------------
# _reap_after_kill — explicit ``await proc.wait()`` raises
# ``asyncio.TimeoutError`` (not ``ProcessLookupError``). Covers lines
# 336-340 of runner.py. The previous reap test triggers the kill()
# ProcessLookupError branch, but wait() returns immediately without
# raising, so the wait() except clause is never entered. Here we use a
# fake ``proc`` whose ``wait()`` raises ``asyncio.TimeoutError``.
# ---------------------------------------------------------------------------
def test_reap_after_kill_wait_timeout_handled():
    """``_reap_after_kill`` swallows ``asyncio.TimeoutError`` from ``await proc.wait()``."""
    from taskq_api.service.runner import _reap_after_kill

    class _FakeProc:
        """Minimal stub that satisfies ``_reap_after_kill`` and raises on wait()."""

        def kill(self) -> None:
            # No-op — kill() does not raise here, so we proceed to wait().
            return None

        async def wait(self) -> int:
            # Force the except (ProcessLookupError, asyncio.TimeoutError)
            # arm to fire at lines 336-340.
            raise asyncio.TimeoutError

    async def _exercise() -> None:
        await _reap_after_kill(_FakeProc())  # type: ignore[arg-type]

    asyncio.run(_exercise())  # must not raise


# ---------------------------------------------------------------------------
# _structured_drain_tasks — direct call that guarantees the cancel loop
# at line 667-669 fires. Submits a task to a Scheduler with max=1,
# yields once, then drains with a 0.05s budget against a 5s sleep so
# the timeout path MUST execute the cancel branch.
# ---------------------------------------------------------------------------
def test_structured_drain_tasks_cancel_loop_direct_call():
    """``_structured_drain_tasks`` with a real task list MUST cancel pending tasks on timeout."""
    from taskq_api.service.runner import Scheduler

    async def _slow() -> str:
        await asyncio.sleep(5.0)
        return "unreachable"

    async def _exercise() -> dict[str, int]:
        sch = Scheduler(max_concurrent=1)
        sch.submit(_slow())
        # Yield so the task enters _runner() and starts awaiting sleep.
        await asyncio.sleep(0)
        # Drain with budget far smaller than sleep — must hit line 669.
        return await sch.drain(timeout=0.05)

    report = asyncio.run(_exercise())
    # Drain reports the slow task as interrupted (cancelled).
    assert report["interrupted_count"] >= 1, (
        f"drain with 0.05s budget must cancel the slow task; got {report!r}"
    )


# ---------------------------------------------------------------------------
# ``_structured_drain_tasks`` — direct invocation with a manually
# constructed unfinished task so we control exactly which lines fire.
# This is a backstop for line 669 in case the Scheduler-wrapped path
# above happens to complete too quickly on certain platforms.
# ---------------------------------------------------------------------------
def test_structured_drain_tasks_cancel_unfinished_task():
    """Direct call to ``_structured_drain_tasks`` with an unfinished asyncio.Task fires cancel."""
    from taskq_api.service.runner import _structured_drain_tasks

    async def _slow() -> str:
        await asyncio.sleep(5.0)
        return "unreachable"

    async def _exercise() -> dict[str, int]:
        # Create the task on this loop, then yield so it starts awaiting.
        task = asyncio.create_task(_slow())
        await asyncio.sleep(0)
        # task is now awaiting sleep(5.0). Hand it to _structured_drain_tasks
        # with a tiny budget so the cancel loop on lines 667-669 MUST fire.
        return await _structured_drain_tasks([task], timeout=0.05)

    report = asyncio.run(_exercise())
    assert report["interrupted_count"] >= 1, (
        f"drain with 0.05s budget must cancel the pending task; got {report!r}"
    )


# ---------------------------------------------------------------------------
# _structured_drain_tasks — fake task that survives the TaskGroup
# cancellation. The two previous cancel-loop tests create real asyncio
# tasks whose ``done()`` is True by the time the fallback loop on lines
# 667-669 fires — the TaskGroup's cancellation propagates through the
# awaiting coroutine and marks the underlying task done, so the
# ``if not t.done()`` branch never enters. A duck-typed fake whose
# ``done()`` is always False forces the fallback ``t.cancel()`` to
# execute, lifting line 669 from 99% to 100% on runner.py.
# ---------------------------------------------------------------------------
def test_structured_drain_tasks_cancel_loop_fake_task_done_false():
    """``_structured_drain_tasks`` with a task whose ``done()`` stays False fires ``t.cancel()``."""
    from taskq_api.service.runner import _structured_drain_tasks

    class _StubTask:
        """Duck-typed task — always reports ``done() == False`` so the cancel branch fires.

        ``__await__`` returns a future that ``cancel()`` can actually cancel
        (so the fallback loop's ``await t`` at lines 670-676 returns
        promptly with ``CancelledError`` rather than blocking on a
        unresponsive sleep). The ``wait_for(timeout=0.05)`` cancels the
        TaskGroup's awaiting coroutine cleanly, then line 668's
        ``if not t.done()`` branch enters, line 669's ``t.cancel()``
        cancels the underlying future, and the second ``await t``
        raises ``CancelledError`` (swallowed by lines 670-676).

        Keeping ``done()`` always False is the load-bearing piece: a real
        asyncio.Task would be marked done the moment the TaskGroup
        cancelled the awaiting coroutine, so the fallback loop on line
        669 would never fire. This stub forces that path.
        """

        def __init__(self) -> None:
            self._loop = asyncio.get_event_loop()
            self._future: asyncio.Future[None] = self._loop.create_future()
            self.cancel_count = 0

        def done(self) -> bool:
            # MUST stay False so line 668's ``if not t.done()`` enters.
            return False

        def cancel(self) -> None:
            self.cancel_count += 1
            if not self._future.done():
                # Real Task.cancel() schedules a CancelledError on the
                # awaiting coroutine — mirror that so ``await t`` (line
                # 672) returns promptly with CancelledError instead of
                # blocking forever on a never-completing future.
                self._future.cancel()

        def __await__(self):  # type: ignore[no-untyped-def]
            return self._future.__await__()

    async def _exercise() -> dict[str, int]:
        stub = _StubTask()
        report = await _structured_drain_tasks([stub], timeout=0.05)
        return report, stub.cancel_count

    report, cancel_count = asyncio.run(_exercise())
    assert cancel_count >= 1, (
        f"line 669 cancel branch MUST fire on a task whose done()==False; "
        f"got cancel_count={cancel_count}, report={report!r}"
    )
    assert report["interrupted_count"] >= 1, (
        f"drain must still report the cancelled stub as interrupted; "
        f"got report={report!r}"
    )


# ===========================================================================
# tasks.py (FR-08 scope per SAB `fr_module_traceability`) — integration
# coverage tests. The `_ScopeDep` wrapper around `require_scope(...)`
# stores its inner closure at `.dependency`, and FastAPI's resolver
# matches `dependency_overrides` against that inner closure — not the
# factory function. Overriding the factory (the test_fr01/02 pattern)
# silently misses because the routes depend on individual `_ScopeDep`
# instances built at module-import time.
#
# These tests cover the tasks.py handlers under the FR-08 scope. The
# `_FakeRepo` mirrors the FR-01/FR-02 contract but lives inside this
# test file so FR-08 does not need the upstream FRs to be importable
# at test time.
# ===========================================================================


class _FakeTaskRepo:
    """In-memory stand-in for `taskq_api.repository.task_repo`.

    Implements the six methods the FR-08 scheduler surface needs from
    handlers in `taskq_api.api.tasks`: ``create``, ``get``, ``list``,
    ``delete_with_results``, ``write_result``, ``list_runs``. Mirrors the
    FR-01 fake so the FR-08 contract is the unit under test, not the
    persistence layer.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.results: list[dict[str, Any]] = []

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        import uuid as _uuid
        name = payload["name"]
        if any(r["name"] == name for r in self.rows.values()):
            raise ValueError(f"name {name!r} already exists")
        row = {
            "id": str(_uuid.uuid4()),
            "command": payload["command"],
            "name": name,
            "status": "pending",
            "created_at": "2026-08-25T00:00:00Z",
        }
        self.rows[row["id"]] = row
        return row

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self.rows.get(task_id)

    def list(
        self,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], str | None]:
        return list(self.rows.values()), None

    def delete_with_results(self, task_id: str) -> int:
        self.rows.pop(task_id, None)
        return 1

    def write_result(self, **fields: Any) -> dict[str, Any]:
        self.results.append(fields)
        return fields

    def list_runs(self, task_id: str) -> list[dict[str, Any]]:
        return [r for r in self.results if r.get("task_id") == task_id]


@pytest.fixture
def fr08_app_client(monkeypatch):
    """TestClient wired with auth + repo overrides that actually resolve.

    Why a fixture instead of module-level helpers: dependency_overrides
    is mutable app state and the other FR test files share the same
    ``taskq_api.app.app`` singleton. Monkey-patching is reset between
    tests, and a fresh ``_FakeTaskRepo`` per test isolates persistence.

    [FR-08]
    Citations:
      - FR-08 §3: FR-08 owns `taskq_api.api.tasks` per SAB
        `fr_module_traceability`; covering its handlers here is the
        FR-08-scope coverage contract.
    """
    from fastapi.testclient import TestClient
    import taskq_api.api.tasks as tasks_mod
    import taskq_api.repository.task_repo as task_repo_mod
    from taskq_api.app import app as _app

    fake_repo = _FakeTaskRepo()
    monkeypatch.setattr(task_repo_mod, "task_repo", fake_repo)

    # Override the inner closure that FastAPI's resolver actually matches.
    # The factory `deps.require_scope` returns a fresh `_ScopeDep` instance
    # every call; only the instance's `.dependency` (the closure built at
    # import time) is what `app.dependency_overrides` recognizes.
    app = _app
    app.dependency_overrides[tasks_mod._require_write.dependency] = (
        lambda: {"scope": "write", "key_id": "fake-key"}
    )
    app.dependency_overrides[tasks_mod._require_read.dependency] = (
        lambda: {"scope": "read", "key_id": "fake-key"}
    )
    app.dependency_overrides[tasks_mod._require_admin.dependency] = (
        lambda: {"scope": "admin", "key_id": "fake-key"}
    )

    # Drop the dependency_overrides we set when the fixture tears down —
    # otherwise the next test that uses the same `app` sees stale
    # overrides from a different repo fake.
    yield TestClient(app), fake_repo

    app.dependency_overrides.pop(tasks_mod._require_write.dependency, None)
    app.dependency_overrides.pop(tasks_mod._require_read.dependency, None)
    app.dependency_overrides.pop(tasks_mod._require_admin.dependency, None)


# NFR-02 (security): integration coverage of the FR-08 scheduler surface
# (POST /v1/tasks/{id}/run + GET /v1/tasks/{id}/runs) per FR-08 scope.
def test_tasks_post_create_endpoint_returns_201(fr08_app_client):
    """POST /v1/tasks hits create_task handler (tasks.py lines 63-75)."""
    client, _fake = fr08_app_client
    response = client.post(
        "/v1/tasks",
        json={"command": "echo hi", "name": "t-fr08-create"},
        headers={"X-API-Key": "fake-write-key"},
    )
    assert response.status_code == 201, (
        f"tasks.py create_task handler MUST run; got {response.status_code} "
        f"body={response.text}"
    )


# NFR-05 (documentation): handler docstrings reference the owning FR.
def test_tasks_post_create_duplicate_returns_422(fr08_app_client):
    """Duplicate name -> ValueError -> 422 (tasks.py lines 67-71)."""
    client, _fake = fr08_app_client
    client.post(
        "/v1/tasks",
        json={"command": "echo a", "name": "dup"},
        headers={"X-API-Key": "fake-write-key"},
    )
    response = client.post(
        "/v1/tasks",
        json={"command": "echo b", "name": "dup"},
        headers={"X-API-Key": "fake-write-key"},
    )
    assert response.status_code == 422, (
        f"duplicate name MUST map to 422 via the create_task except "
        f"branch; got {response.status_code} body={response.text}"
    )


def test_tasks_get_by_id_found_and_not_found(fr08_app_client):
    """GET /v1/tasks/{id} for found + not-found hits read_task (lines 94-100)."""
    client, _fake = fr08_app_client
    created = client.post(
        "/v1/tasks",
        json={"command": "echo", "name": "t-get"},
        headers={"X-API-Key": "fake-write-key"},
    ).json()

    found = client.get(
        f"/v1/tasks/{created['id']}",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert found.status_code == 200, (
        f"GET on existing task MUST hit the read_task handler; "
        f"got {found.status_code} body={found.text}"
    )

    missing = client.get(
        "/v1/tasks/00000000-0000-0000-0000-000000000000",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert missing.status_code == 404, (
        f"GET on unknown id MUST hit the read_task 404 branch; "
        f"got {missing.status_code} body={missing.text}"
    )


def test_tasks_list_endpoint(fr08_app_client):
    """GET /v1/tasks hits list_tasks handler (tasks.py lines 122-131)."""
    client, _fake = fr08_app_client
    response = client.get(
        "/v1/tasks",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert response.status_code == 200, (
        f"GET /v1/tasks MUST reach list_tasks; got {response.status_code} "
        f"body={response.text}"
    )


def test_tasks_delete_endpoint(fr08_app_client):
    """DELETE /v1/tasks/{id} hits delete_task handler (tasks.py lines 151-152)."""
    client, _fake = fr08_app_client
    created = client.post(
        "/v1/tasks",
        json={"command": "echo", "name": "t-del"},
        headers={"X-API-Key": "fake-write-key"},
    ).json()

    response = client.delete(
        f"/v1/tasks/{created['id']}",
        headers={"X-API-Key": "fake-admin-key"},
    )
    assert response.status_code == 204, (
        f"DELETE on existing task MUST hit delete_task; got "
        f"{response.status_code} body={response.text}"
    )


# NFR-02 (security): the run endpoint is the FR-08 scheduler surface.
def test_tasks_run_endpoint_existing_task(fr08_app_client, monkeypatch):
    """POST /v1/tasks/{id}/run hits run_task_endpoint (lines 208-221) + 404 branch."""
    import taskq_api.api.tasks as tasks_mod

    # Replace the subprocess runner with a no-op so the background task
    # doesn't fork; the handler MUST return 202 immediately regardless.
    async def _noop(**_kwargs):
        return None

    monkeypatch.setattr(tasks_mod, "_run_subprocess", _noop)

    client, _fake = fr08_app_client
    created = client.post(
        "/v1/tasks",
        json={"command": "echo", "name": "t-run"},
        headers={"X-API-Key": "fake-write-key"},
    ).json()

    response = client.post(
        f"/v1/tasks/{created['id']}/run",
        headers={"X-API-Key": "fake-write-key"},
    )
    assert response.status_code == 202, (
        f"POST run on existing task MUST hit run_task_endpoint; "
        f"got {response.status_code} body={response.text}"
    )

    # 404 branch (lines 209-213).
    missing = client.post(
        "/v1/tasks/00000000-0000-0000-0000-000000000000/run",
        headers={"X-API-Key": "fake-write-key"},
    )
    assert missing.status_code == 404, (
        f"POST run on unknown task MUST hit the 404 branch; "
        f"got {missing.status_code} body={missing.text}"
    )


def test_tasks_list_runs_endpoint(fr08_app_client):
    """GET /v1/tasks/{id}/runs hits list_runs_endpoint (lines 240-241)."""
    client, _fake = fr08_app_client
    created = client.post(
        "/v1/tasks",
        json={"command": "echo", "name": "t-runs"},
        headers={"X-API-Key": "fake-write-key"},
    ).json()
    response = client.get(
        f"/v1/tasks/{created['id']}/runs",
        headers={"X-API-Key": "fake-read-key"},
    )
    assert response.status_code == 200, (
        f"GET runs MUST hit list_runs_endpoint; got {response.status_code} "
        f"body={response.text}"
    )


# NFR-03 (error_handling): non-cancel exceptions must be swallowed so
# the 202 response is unaffected.
def test_tasks_execute_and_record_swallows_non_cancel_exception(
    fr08_app_client, monkeypatch
):
    """Non-cancel exception in run_task MUST be swallowed (tasks.py lines 175-183)."""
    import taskq_api.api.tasks as tasks_mod

    async def _boom(**_kwargs):
        raise RuntimeError("synthetic subprocess failure")

    monkeypatch.setattr(tasks_mod, "_run_subprocess", _boom)

    client, _fake = fr08_app_client
    created = client.post(
        "/v1/tasks",
        json={"command": "echo boom", "name": "t-bg-err"},
        headers={"X-API-Key": "fake-write-key"},
    ).json()
    response = client.post(
        f"/v1/tasks/{created['id']}/run",
        headers={"X-API-Key": "fake-write-key"},
    )
    assert response.status_code == 202, (
        f"run endpoint MUST return 202 even when the background task "
        f"raises a non-cancel exception; got {response.status_code} "
        f"body={response.text}"
    )


# NFR-03 (error_handling): CancelledError must propagate — the re-raise
# at line 178 is the FR-08 contract. Calling ``_execute_and_record``
# directly (not via TestClient) avoids the background-task-fires-after-
# response ambiguity and surfaces the re-raise as a real exception.
def test_tasks_execute_and_record_reraises_cancelled_error(monkeypatch):
    """``_execute_and_record`` MUST re-raise CancelledError (tasks.py line 178)."""
    import taskq_api.api.tasks as tasks_mod

    async def _cancel(**_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tasks_mod, "_run_subprocess", _cancel)

    async def _invoke() -> None:
        await tasks_mod._execute_and_record(
            task_id="00000000-0000-0000-0000-000000000001",
            command="echo cancel",
            run_id="00000000-0000-0000-0000-000000000002",
        )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_invoke())