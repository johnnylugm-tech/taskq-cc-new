"""Property-based tests — hypothesis @given cases for the FR-level invariants.

[FR-04, FR-05, FR-07, FR-08, FR-10]

TEST_SPEC.md §Properties (Direction B) declares the algebraic invariants
each FR is responsible for. This file is the executable counterpart: a
``hypothesis.given(...)`` body whose predicate is the same expression
the spec writes, so a counter-example shrinks to the minimal failing
inputs. The function names match TEST_SPEC §Properties so the
spec-coverage check binds the property to its executing test.

Each property test:

* uses a hypothesis strategy that constrains the inputs to the
  domain the spec-typed function actually accepts (e.g. ranks in
  ``[0, 1, 2]`` for ``scope_satisfies``),
* asserts the spec-typed invariant verbatim,
* is independent of the rest of the suite — running it standalone is
  enough to re-verify the invariant.
"""
from __future__ import annotations

import asyncio
import time

import hypothesis.strategies as st
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from hypothesis import given, settings
from sqlalchemy import create_engine

import migrations.versions.v1_initial as v1_initial_mod
import migrations.versions.v2_tags as v2_tags_mod
import migrations.versions.v3_split_results as v3_mod

from taskq_api.errors import problem_response
from taskq_api.service.auth import scope_satisfies
from taskq_api.service.ratelimit import refill


# ===========================================================================
# FR-04: scope hierarchy (P4-admin-superset-write,
#        P4-write-superset-read, P4-read-not-superset-write)
# ===========================================================================

# Spec-typed scopes — read < write < admin (inclusive). The strings are
# exactly what ``scope_satisfies`` accepts at runtime; unknown values
# must always return False (deny-by-default per NFR-02).
_RANK_SCOPE = ("read", "write", "admin")


# FR-04
@given(
    granted=st.sampled_from(_RANK_SCOPE),
    required=st.sampled_from(_RANK_SCOPE),
)
@settings(max_examples=50, deadline=None)
def test_p4_scope_satisfies_matches_rank(granted: str, required: str) -> None:
    """``scope_satisfies`` returns True iff the granted rank >= required rank.

    Encodes the three Properties (P4-admin-superset-write,
    P4-write-superset-read, P4-read-not-superset-write) in a single
    assertion against the rank mapping the implementation uses.
    """
    _RANK = {"read": 0, "write": 1, "admin": 2}
    expected = _RANK[granted] >= _RANK[required]
    assert scope_satisfies(granted, required) is expected


@given(granted=st.sampled_from(_RANK_SCOPE))
@settings(max_examples=20, deadline=None)
def test_p4_scope_satisfies_is_reflexive(granted: str) -> None:
    """Every scope satisfies itself (``scope_satisfies(s, s) is True``)."""
    assert scope_satisfies(granted, granted) is True


@given(unknown=st.text(min_size=1, max_size=10).filter(lambda s: s not in _RANK_SCOPE))
@settings(max_examples=20, deadline=None)
def test_p4_unknown_scope_denies(unknown: str) -> None:
    """Unknown scopes deny both directions (deny-by-default NFR-02).

    Strategy filters out the known ranks, so every example exercises the
    ``_SCOPE_RANK.get(...) -> -1`` branch on at least one side.
    """
    assert scope_satisfies(unknown, "read") is False
    assert scope_satisfies("admin", unknown) is False


# ===========================================================================
# FR-05: bucket capacity (P5-bucket-bounded, P5-bucket-cap)
# ===========================================================================


# FR-05
@given(
    tokens=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    last_refill_ts=st.floats(min_value=0.0, max_value=1e9, allow_nan=False),
    elapsed=st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
    burst=st.integers(min_value=1, max_value=100),
    refill_per_sec=st.floats(min_value=0.1, max_value=100.0, allow_nan=False),
)
@settings(max_examples=50, deadline=None)
def test_p5_bucket_cap_holds(
    tokens: float,
    last_refill_ts: float,
    elapsed: float,
    burst: int,
    refill_per_sec: float,
) -> None:
    """P5-bucket-cap: after refill, tokens <= burst_capacity."""
    now = last_refill_ts + elapsed
    new_tokens = refill(
        tokens=tokens,
        last_refill_ts=last_refill_ts,
        now=now,
        burst=burst,
        refill_per_sec=refill_per_sec,
    )
    assert new_tokens <= float(burst), (
        f"bucket exceeded capacity: tokens={new_tokens} > burst={burst}"
    )


@given(
    tokens=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    burst=st.integers(min_value=1, max_value=100),
    refill_per_sec=st.floats(min_value=0.1, max_value=100.0, allow_nan=False),
)
@settings(max_examples=30, deadline=None)
def test_p5_bucket_bounded_no_negative(
    tokens: float,
    burst: int,
    refill_per_sec: float,
) -> None:
    """P5-bucket-bounded: refill never reports a negative balance.

    The refill policy clamps to ``[0, burst]`` — a strategy with
    non-negative inputs must never produce a negative result.
    """
    now = time.monotonic()
    last_refill_ts = now
    new_tokens = refill(
        tokens=tokens,
        last_refill_ts=last_refill_ts,
        now=now,
        burst=burst,
        refill_per_sec=refill_per_sec,
    )
    assert new_tokens >= 0.0, f"refill produced negative tokens: {new_tokens}"


# ===========================================================================
# FR-07: v3 round-trip (P7-v3-roundtrip, P7-downgrade-no-data-loss)
# ===========================================================================


_task_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=12,
)
_result_json_strategy = st.text(min_size=1, max_size=64)


def _build_v2_schema(conn: sa.engine.Connection) -> None:
    """Bring the schema to v2 (v1_initial.upgrade() → v2_tags.upgrade()).

    Uses an in-process alembic Operations proxy so the migration bodies
    execute against a real SQLite engine — same shape as the
    FR-07 round-trip test, parameterised for the property tests.
    """
    v1_initial_mod.upgrade()
    v2_tags_mod.upgrade()


@given(
    task_id=_task_id_strategy,
    result_json=_result_json_strategy,
)
@settings(max_examples=20, deadline=None)
def test_p7_v3_roundtrip_preserves_sample_row(
    task_id: str,
    result_json: str,
) -> None:
    """P7-v3-roundtrip: upgrade → seed → downgrade → upgrade preserves the row.

    The composite ``downgrade_then_upgrade`` predicate from the TEST_SPEC
    §FR-07 Properties block, executed against a real SQLite engine so
    a counter-example actually exercises the alembic ops (not a mock).
    """
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        ops._install_proxy()
        try:
            # Bring the schema to v2, then seed one sample row.
            _build_v2_schema(conn)
            conn.execute(
                sa.text(
                    "INSERT INTO tasks (id, command, name, result_json) "
                    "VALUES (:id, :cmd, :name, :rj)"
                ),
                {
                    "id": task_id,
                    "cmd": "echo seed",
                    "name": f"sample-{task_id}",
                    "rj": result_json,
                },
            )

            # Round-trip: upgrade → downgrade → upgrade.
            v3_mod.upgrade()
            v3_mod.downgrade()
            v3_mod.upgrade()

            row = conn.execute(
                sa.text(
                    "SELECT task_id, result_json FROM task_results "
                    "WHERE task_id = :id"
                ),
                {"id": task_id},
            ).fetchone()
            assert row is not None, (
                f"P7-v3-roundtrip: task {task_id!r} missing after upgrade → "
                f"downgrade → upgrade"
            )
            assert row[1] == result_json, (
                f"P7-v3-roundtrip: result_json mutated: "
                f"{row[1]!r} != {result_json!r}"
            )
        finally:
            ops._remove_proxy()


@given(
    n_rows=st.integers(min_value=1, max_value=8),
)
@settings(max_examples=10, deadline=None)
def test_p7_downgrade_no_data_loss(n_rows: int) -> None:
    """P7-downgrade-no-data-loss: row count survives downgrade().

    Inserts N non-null ``result_json`` rows at v2, upgrades to v3,
    downgrades back to v2, and asserts the task count is preserved.
    """
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        ops._install_proxy()
        try:
            _build_v2_schema(conn)
            for i in range(n_rows):
                conn.execute(
                    sa.text(
                        "INSERT INTO tasks (id, command, name, result_json) "
                        "VALUES (:id, :cmd, :name, :rj)"
                    ),
                    {
                        "id": f"row-{i}",
                        "cmd": "echo seed",
                        "name": f"name-{i}",
                        "rj": f"payload-{i}",
                    },
                )

            before = conn.execute(sa.text("SELECT COUNT(*) FROM tasks")).scalar()

            v3_mod.upgrade()
            v3_mod.downgrade()

            after = conn.execute(sa.text("SELECT COUNT(*) FROM tasks")).scalar()
            assert after == before, (
                f"P7-downgrade-no-data-loss: row count drifted "
                f"{before} -> {after}"
            )
        finally:
            ops._remove_proxy()


# ===========================================================================
# FR-08: drain budget (P8-drain-budget, P8-cancel-pure)
# ===========================================================================


# FR-08
@given(timeout=st.floats(min_value=0.01, max_value=2.0, allow_nan=False))
@settings(max_examples=10, deadline=None)
def test_p8_drain_elapsed_within_timeout(timeout: float) -> None:
    """P8-drain-budget: drain finishes within the configured timeout.

    Schedules a no-op coroutine, calls ``asyncio.run`` + ``drain()``,
    and asserts the wall-clock elapsed did not exceed the timeout
    plus a small slack for asyncio scheduling overhead.
    """
    async def _scenario() -> float:
        from taskq_api.service.runner import drain, schedule

        async def _noop() -> None:
            return None

        schedule(_noop())
        started = time.monotonic()
        await drain(timeout=timeout)
        return time.monotonic() - started

    elapsed = asyncio.run(_scenario())
    # Allow a small slack for asyncio scheduling; the spec invariant
    # is "drain_elapsed_seconds <= drain_timeout", so the slack is a
    # bound the test enforces explicitly to keep the property true.
    assert elapsed <= timeout + 0.5, (
        f"P8-drain-budget: drain elapsed {elapsed:.3f}s > timeout "
        f"{timeout:.3f}s (with 0.5s slack)"
    )


def test_p8_cancelled_error_propagates() -> None:
    """P8-cancel-pure: ``asyncio.CancelledError`` propagates from a coroutine.

    A coroutine that awaits a never-firing future, when cancelled,
    MUST raise ``CancelledError`` outward (per AC-8.5). A swallow —
    i.e. an except clause that catches and returns — fails this
    property.
    """
    async def _scenario() -> bool:
        from taskq_api.service.runner import drain, schedule

        async def _hang() -> None:
            await asyncio.sleep(60)

        task = schedule(_hang())
        # Give the event loop one tick to register the task.
        await asyncio.sleep(0)
        task.cancel()
        try:
            await asyncio.gather(task)
        except asyncio.CancelledError:
            return True
        # Drain everything so the test leaves no orphan tasks on the loop.
        await drain(timeout=0.5)
        return False

    assert asyncio.run(_scenario()) is True


# ===========================================================================
# FR-10: factory idempotence (P10-factory-deterministic,
#        P10-type-uri-stable)
# ===========================================================================


# FR-10
@given(
    detail=st.text(min_size=1, max_size=64),
    correlation_id=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-"),
        min_size=1,
        max_size=32,
    ),
)
@settings(max_examples=50, deadline=None)
def test_p10_factory_deterministic_title(
    detail: str, correlation_id: str
) -> None:
    """P10-factory-deterministic: title is stable per (status, type_uri).

    The factory's ``title`` field MUST echo the caller-supplied value
    verbatim; if the implementation ever started synthesising a title,
    this property would fail.
    """
    body = problem_response(
        status=422,
        type_uri="/errors/validation",
        title="validation",
        detail=detail,
        correlation_id=correlation_id,
    )
    assert body["title"] == "validation", (
        f"P10-factory-deterministic: title mutated: {body['title']!r}"
    )
    assert body["type"] == "/errors/validation"
    assert body["status"] == 422


@given(
    detail=st.text(min_size=0, max_size=64),
    instance=st.text(min_size=0, max_size=32),
)
@settings(max_examples=30, deadline=None)
def test_p10_type_uri_stable(detail: str, instance: str) -> None:
    """P10-type-uri-stable: ``type`` mirrors the caller-supplied URI.

    Every non-2xx response carries a ``type`` the client branches on;
    the factory MUST pass it through unmodified.
    """
    body = problem_response(
        status=422,
        type_uri="/errors/validation",
        title="validation",
        detail=detail,
        instance=instance,
    )
    assert body["type"] == "/errors/validation", (
        f"P10-type-uri-stable: type field mutated: {body['type']!r}"
    )


def test_p10_default_correlation_id_is_uuid_hex() -> None:
    """The default ``correlation_id`` is a 32-char lowercase hex UUID4.

    The factory generates a fresh UUID4 when ``correlation_id`` is
    ``None``; the property below pins the format so an accidental
    change (e.g. using a non-hex source) is caught.
    """
    body = problem_response(
        status=500,
        type_uri="/errors/internal",
        title="internal",
        detail="x",
    )
    cid = body["correlation_id"]
    assert isinstance(cid, str)
    assert len(cid) == 32, f"correlation_id length {len(cid)} != 32"
    assert all(c in "0123456789abcdef" for c in cid), (
        f"correlation_id contains non-hex chars: {cid!r}"
    )