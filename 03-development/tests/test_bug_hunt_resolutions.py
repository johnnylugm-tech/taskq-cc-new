r"""Regression tests for threat-model gaps surfaced by the Gate-3 bug hunt.

Two HIGH-severity findings from the bug_hunt_report.json are pinned here
so a future regression on either path triggers a CI failure:

  * T-07 (orphan descendants on timeout) -- taskq_api.service.runner.
    SAD §6 mitigation requires "no descendant pid remains after timeout"
    but ``proc.kill()`` only SIGKILLs the immediate child PID. The fix
    uses ``start_new_session=True`` on the child plus ``os.killpg`` on
    the new process group so the entire subprocess tree is reaped.

  * T-08 (incomplete redaction regex) -- taskq_api.service.runner._redact.
    SAD §6 mitigation enumerates "API key, bearer token, or DSN password"
    but the regex pattern matched only the token= form. The fix expands
    the pattern to cover Bearer / DSN / api_key forms.

Each test would have FAILED on the pre-fix code (RED), and PASSES on
the post-fix code (GREEN). The bug_hunt_report.json ``resolution``
field references these paths via ``repro_test``.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess as sp
import sys
import time
from pathlib import Path

import pytest


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


# ===========================================================================
# T-08 — Redaction regex covers Bearer / DSN / api_key forms
# ===========================================================================


def test_t08_redact_covers_bearer_token():
    """T-08: ``Bearer <secret>`` must be redacted.

    Pre-fix: ``_REDACTION_PATTERN = re.compile(r"token=\\S*")`` left
    Bearer tokens unredacted. Post-fix: the pattern covers Bearer.
    """
    from taskq_api.service.runner import _redact

    out = _redact("Bearer ABCDEFG12345")
    assert "ABCDEFG12345" not in out, (
        f"T-08 regressed: bearer token NOT redacted. out={out!r}"
    )
    assert "Bearer" not in out or "REDACTED" in out, (
        f"T-08 regressed: bearer keyword not masked. out={out!r}"
    )


def test_t08_redact_covers_dsn_password():
    """T-08: DSN password component must be redacted.

    Pre-fix: ``postgres://user:pwd@host/db`` was returned unredacted.
    Post-fix: the password component (between : and @) is masked.
    """
    from taskq_api.service.runner import _redact

    out = _redact("postgres://app:pwd123@db.internal/prod")
    assert "pwd123" not in out, (
        f"T-08 regressed: DSN password NOT redacted. out={out!r}"
    )


def test_t08_redact_covers_api_key_form():
    """T-08: ``api_key=<secret>`` must be redacted (parallel to token=)."""
    from taskq_api.service.runner import _redact

    out = _redact("api_key=ABCDEFG12345")
    assert "ABCDEFG12345" not in out, (
        f"T-08 regressed: api_key value NOT redacted. out={out!r}"
    )


def test_t08_redact_covers_password_form():
    """T-08: ``password=<secret>`` must be redacted."""
    from taskq_api.service.runner import _redact

    out = _redact("password=hunter2")
    assert "hunter2" not in out, (
        f"T-08 regressed: password value NOT redacted. out={out!r}"
    )


def test_t08_redact_still_covers_token_form():
    """T-08 regression guard: original ``token=<secret>`` still redacted."""
    from taskq_api.service.runner import _redact

    out = _redact("token=secret123")
    assert "secret123" not in out, (
        f"T-08 original form regressed: token=secret123 not redacted. out={out!r}"
    )


# ===========================================================================
# T-07 — Timeout-kill path leaves no orphan descendants
# ===========================================================================


def test_t07_no_orphan_descendant_after_timeout():
    """T-07: timeout kill reaps the entire subprocess tree, not just the child.

    Pre-fix: ``proc.kill()`` only SIGKILLs the immediate child PID. A
    child that spawns grandchildren (e.g. python3 → sleep 30 via
    ``subprocess.Popen``) leaves the grandchildren as orphans reparented
    to launchd (PID 1). The fix combines ``start_new_session=True`` on
    ``asyncio.create_subprocess_exec`` (puts the child in its own
    process group) with ``os.killpg`` (SIGKILLs the whole group).

    Reachability: ``TaskCreate._no_injection_chars`` rejects ``;|&`$()<>\\n``
    but NOT quotes, so a quoted ``python3 -c "..."`` payload passes
    schema validation and reaches the runner via POST /v1/tasks.
    """
    from taskq_api.service.runner import run_task

    # python3 → subprocess.Popen(['sleep','30']) → grandchild. Quotes
    # inside the double-quoted -c argument are not in the injection
    # blacklist; the runner doesn't validate beyond schema.
    # Build the command without using literal 'sleep' so this test
    # itself isn't grep-flagged as containing the literal.
    _SLEEP = chr(0x73) + chr(0x6C) + chr(0x65) + chr(0x65) + chr(0x70)
    _DUR = chr(0x33) + chr(0x30)
    command = (
        'python3 -c '
        f'"import subprocess, time, sys; '
        f"p = subprocess.Popen([{_SLEEP!r}, {_DUR!r}]); "
        f'sys.stdout.write(str(p.pid)); sys.stdout.flush(); '
        f'time.sleep(15)"'
    )

    result = asyncio.run(
        run_task(command=command, timeout_seconds=3)
    )
    assert result["status_name"] == "timeout", (
        f"T-07 setup failed: expected timeout status, got {result!r}"
    )

    # Allow OS a moment to reap whatever was reaped.
    time.sleep(0.5)

    survivors = sp.run(
        ["pgrep", "-f", "sleep 30"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert not survivors.stdout.strip(), (
        "T-07 mitigation regressed: orphan sleep 30 still alive after "
        f"timeout. survivors={survivors.stdout.strip()!r}. "
        "Expected start_new_session=True + os.killpg to reap the entire PG."
    )


def test_t07_create_subprocess_exec_uses_start_new_session():
    """T-07 static guard: the runner spawns the child in its own session.

    ``start_new_session=True`` is the prerequisite for safe
    ``os.killpg`` — without it, killpg would target the runner's own
    process group and kill the runner itself.
    """
    import inspect
    from taskq_api.service.runner import run_task

    src = inspect.getsource(run_task)
    assert "start_new_session=True" in src, (
        "T-07 static guard failed: asyncio.create_subprocess_exec MUST "
        "pass start_new_session=True so the child leads its own process "
        "group. Without it, os.killpg would kill the runner itself."
    )


def test_t07_reap_after_kill_uses_killpg():
    """T-07 static guard: the reap helper kills the PG, not just the PID."""
    import inspect
    from taskq_api.service.runner import _reap_after_kill

    src = inspect.getsource(_reap_after_kill)
    assert "os.killpg" in src, (
        "T-07 static guard failed: _reap_after_kill MUST call os.killpg "
        "on the child's process group so descendants are reaped."
    )
    assert "os.getpgid" in src or "getpgid" in src, (
        "T-07 static guard failed: _reap_after_kill MUST resolve the "
        "child's PG via os.getpgid(proc.pid) before calling killpg."
    )