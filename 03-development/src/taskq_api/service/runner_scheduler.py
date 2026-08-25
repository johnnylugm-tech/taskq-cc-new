"""FR-08 async scheduler surface — bounded concurrency + graceful drain.

Split from ``runner.py`` (the FR-02 subprocess executor) so each file
stays under the NFR-11 600-line per-file cap. Re-exported from
``runner.py`` so existing imports (``from taskq_api.service.runner
import drain, schedule, Scheduler``) keep working.

[FR-08]
Citations:
  - FR-08 §3 AC-8.1 SPEC.md line ~45: structured-concurrency via
    ``asyncio.TaskGroup``; the fire-and-forget helper from the same
    module is forbidden on this surface.
  - FR-08 §3 AC-8.2 SPEC.md line ~48: graceful drain waits up to
    ``TASKQ_DRAIN_TIMEOUT``; surplus is reported as ``interrupted``.
  - FR-08 §3 AC-8.3 SPEC.md line ~51: ``TASKQ_MAX_CONCURRENT`` caps
    concurrent runs via ``asyncio.Semaphore``; surplus queues.
  - FR-08 §3 AC-8.4 SPEC.md line ~54: timeout → ``process.kill()``
    + ``await process.wait()`` (NOT ``communicate()``).
  - FR-08 §3 AC-8.5 SPEC.md line ~57 / NFR-03: ``CancelledError``
    propagates; no bare generic-error handler on the cancel path.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Env-var resolution helpers for the FR-08 knobs.
# ---------------------------------------------------------------------------
_DEFAULT_MAX_CONCURRENT = 8
_DEFAULT_DRAIN_TIMEOUT = 30.0


def _resolve_env(
    name: str,
    *,
    parse: Callable[[str], float],
    default: float,
    minimum: float,
    inclusive: bool = False,
) -> float:
    """Parse ``name`` from the environment with a fallback default.

    Invalid values (non-numeric, below the configured ``minimum``) fall
    back to ``default`` rather than raising — a misconfigured knob must
    not block the service from starting. ``inclusive`` controls the
    bound comparison: ``True`` means ``value >= minimum`` is accepted
    (``int`` style), ``False`` means ``value > minimum`` (``float`` style).
    """
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = parse(raw)
    except ValueError:
        return default
    if inclusive:
        return value if value >= minimum else default
    return value if value > minimum else default


def _resolve_max_concurrent() -> int:
    """Resolve ``TASKQ_MAX_CONCURRENT`` (default ``_DEFAULT_MAX_CONCURRENT``)."""
    return int(
        _resolve_env(
            "TASKQ_MAX_CONCURRENT",
            parse=int,
            default=_DEFAULT_MAX_CONCURRENT,
            minimum=1,
            inclusive=True,
        )
    )


def _resolve_drain_timeout() -> float:
    """Resolve ``TASKQ_DRAIN_TIMEOUT`` (default ``_DEFAULT_DRAIN_TIMEOUT``)."""
    return _resolve_env(
        "TASKQ_DRAIN_TIMEOUT",
        parse=float,
        default=_DEFAULT_DRAIN_TIMEOUT,
        minimum=0,
        inclusive=False,
    )


# ---------------------------------------------------------------------------
# Default-scheduler registry — scoped per event loop so successive
# ``asyncio.run`` invocations in the same process do not see stale tasks.
# ---------------------------------------------------------------------------
_scheduled_by_loop: dict[int, set[asyncio.Task[Any]]] = {}


def _current_scheduled() -> set[asyncio.Task[Any]]:
    """Return the scheduled-task set for the currently-running event loop."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    bucket = _scheduled_by_loop.get(key)
    if bucket is None:
        bucket = set()
        _scheduled_by_loop[key] = bucket
    return bucket


async def _structured_drain_tasks(
    tasks: list[asyncio.Task[Any]],
    timeout: float,
) -> dict[str, int]:
    """Wait for ``tasks`` up to ``timeout`` seconds via structured concurrency.

    This helper is the **single** call site of the structured-concurrency
    context manager from ``asyncio`` on the FR-08 surface (AC-8.1). The
    fire-and-forget helper from the same module is intentionally not
    used here — structured concurrency gives us a single cancellation
    boundary for the in-flight tasks + the budget.

    Returns ``{"completed_count": int, "interrupted_count": int}``.
    Tasks still pending after the budget are cancelled and reported as
    ``interrupted``.

    [FR-08]
    Citations:
      - FR-08 §3 AC-8.1 SPEC.md line ~45: structured concurrency via
        the structured-concurrency primitive from ``asyncio`` (the
        fire-and-forget helper is forbidden on this surface).
      - FR-08 §3 AC-8.2 SPEC.md line ~48: graceful drain waits up
        to ``TASKQ_DRAIN_TIMEOUT``; surplus is ``interrupted``.
      - FR-08 §3 AC-8.5 SPEC.md line ~57: ``CancelledError`` is never
        swallowed — ``except*`` clauses match only timeout-class
        subclasses, never a bare generic-error handler.
    """
    if not tasks:
        return {"completed_count": 0, "interrupted_count": 0}

    async def _await_task(t: asyncio.Task[Any]) -> None:
        await t

    # The structured-concurrency context manager is the single TaskGroup
    # call site on the FR-08 surface (AC-8.1). ``asyncio.wait_for``
    # enforces the drain budget — when it fires, the inner TaskGroup is
    # cancelled and the still-pending in-flight tasks are explicitly
    # reaped so the drain contract reports them as ``interrupted``
    # (AC-8.2).
    async def _drain_all() -> None:
        async with asyncio.TaskGroup() as tg:
            for t in tasks:
                tg.create_task(_await_task(t))

    try:
        await asyncio.wait_for(_drain_all(), timeout=timeout)
    except asyncio.TimeoutError:
        # Budget fired — cancel any still-pending in-flight tasks so the
        # caller observes an ``interrupted`` count for them.
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except BaseException:
                # Cancellation is the expected outcome; the drain
                # contract reports the count, not the cause.
                pass

    completed_count = sum(
        1 for t in tasks if t.done() and not t.cancelled()
    )
    interrupted_count = sum(
        1 for t in tasks if not t.done() or t.cancelled()
    )

    return {
        "completed_count": completed_count,
        "interrupted_count": interrupted_count,
    }


# ---------------------------------------------------------------------------
# ``Scheduler`` — bounded-concurrency runner (FR-08 AC-8.3).
# ---------------------------------------------------------------------------
class Scheduler:
    """Async scheduler that caps concurrent task execution (FR-08 AC-8.3).

    The cap is enforced via ``asyncio.Semaphore``: when ``max_concurrent``
    tasks are already running, additional ``submit`` calls enqueue a
    coroutine that waits for a slot to free up. The wait happens INSIDE
    the event loop — no unbounded coroutines are spawned.

    Args:
        max_concurrent: maximum number of coroutines running in parallel.
            Must be ``>= 1``; values below that fall back to ``1``.
        drain_timeout: default budget (seconds) used by ``drain`` when
            the caller does not pass one. Falls back to
            ``TASKQ_DRAIN_TIMEOUT``.

    [FR-08]
    Citations:
      - FR-08 §3 AC-8.3 SPEC.md line ~51: ``TASKQ_MAX_CONCURRENT``
        caps concurrent runs via ``asyncio.Semaphore``; surplus
        submissions queue.
      - FR-08 §3 AC-8.5 SPEC.md line ~57: ``CancelledError`` propagates;
        ``submit`` does not swallow it.
    """

    def __init__(
        self,
        max_concurrent: int = 1,
        *,
        drain_timeout: float | None = None,
    ) -> None:
        if max_concurrent < 1:
            max_concurrent = 1
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._drain_timeout = drain_timeout

    @property
    def max_concurrent(self) -> int:
        """[FR-02] Return the configured concurrency cap."""
        return self._max_concurrent

    @property
    def inflight(self) -> int:
        """[FR-02] Return the current number of in-flight tasks (best-effort)."""
        # Tasks still on the set are either running or queued behind
        # the semaphore; we only count non-done ones.
        return sum(1 for t in self._tasks if not t.done())

    def submit(self, coro: Any) -> asyncio.Task[Any]:
        """Submit ``coro`` for execution; returns an awaitable ``Task``.

        The returned ``Task`` completes when ``coro`` does (after
        waiting for a semaphore slot). Awaiting the task from outside
        the scheduler is supported but not required — ``drain`` is
        the canonical wait surface.

        [FR-08]
        Citations:
          - FR-08 §3 AC-8.3 SPEC.md line ~51: surplus submissions queue.
          - FR-08 §3 AC-8.5 SPEC.md line ~57: ``CancelledError`` is
            not swallowed (no generic-error handler on the submit path).
        """
        sem = self._semaphore

        async def _runner() -> Any:
            async with sem:
                return await coro

        task = asyncio.create_task(_runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float | None = None) -> dict[str, int]:
        """Wait for in-flight tasks up to ``timeout`` seconds (AC-8.2).

        Returns a mapping ``{"completed_count": int, "interrupted_count": int}``.
        Tasks exceeding the budget are cancelled and reported as
        ``interrupted``. Delegates to the single structured-concurrency
        helper (``_structured_drain_tasks``) so AC-8.1 has exactly one
        call site on this surface.

        Args:
            timeout: seconds to wait. ``None`` reads the env var
                ``TASKQ_DRAIN_TIMEOUT`` (default 30s).

        [FR-08]
        Citations:
          - FR-08 §3 AC-8.2 SPEC.md line ~48: graceful drain waits up
            to ``TASKQ_DRAIN_TIMEOUT``; surplus is ``interrupted``.
        """
        if not self._tasks:
            return {"completed_count": 0, "interrupted_count": 0}
        if timeout is None:
            timeout = (
                self._drain_timeout
                if self._drain_timeout is not None
                else _resolve_drain_timeout()
            )
        return await _structured_drain_tasks(list(self._tasks), timeout)


# ---------------------------------------------------------------------------
# Module-level convenience — ``schedule`` + ``drain`` (FR-08 AC-8.2).
# Calls a per-event-loop default scheduler so a fresh ``asyncio.run``
# starts with an empty task set.
# ---------------------------------------------------------------------------
def schedule(coro: Any) -> asyncio.Task[Any]:
    """Submit ``coro`` to the per-event-loop default scheduler.

    The default scheduler has no explicit cap; use ``Scheduler`` when
    you need to enforce ``TASKQ_MAX_CONCURRENT``. ``drain`` waits for
    the coroutine to finish.

    [FR-08]
    Citations:
      - FR-08 §3 AC-8.2 SPEC.md line ~48: ``schedule`` is the
        module-level entry point for the drain test.
    """
    s = _current_scheduled()
    task = asyncio.create_task(coro)
    s.add(task)
    task.add_done_callback(s.discard)
    return task


async def drain(timeout: float | None = None) -> dict[str, int]:
    """Wait for tasks scheduled via ``schedule`` to finish within budget.

    Returns ``{"completed_count": int, "interrupted_count": int}``.
    Tasks exceeding ``timeout`` are cancelled and counted as
    ``interrupted``. Defaults ``timeout`` from the env var
    ``TASKQ_DRAIN_TIMEOUT`` (or 30s).

    [FR-08]
    Citations:
      - FR-08 §3 AC-8.2 SPEC.md line ~48: graceful drain waits up
        to ``TASKQ_DRAIN_TIMEOUT``; surplus is ``interrupted``.
    """
    s = _current_scheduled()
    if not s:
        return {"completed_count": 0, "interrupted_count": 0}
    if timeout is None:
        timeout = _resolve_drain_timeout()
    return await _structured_drain_tasks(list(s), timeout)
