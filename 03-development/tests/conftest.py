"""Pytest configuration for the taskq-api test suite.

Adds `03-development/src` to `sys.path` so test modules can `import
taskq_api...` without a packaged install. This mirrors the harness
fixture pattern (`harness/tests/fixtures/mutmut_bare_cfg/03-development/
tests/conftest.py`) and is the only place outside test code that
touches the import path.

[FR-01]
Citations:
  - FR-01: makes the FR-01 test contract's top-level import resolvable.
"""
from __future__ import annotations

import sys
from pathlib import Path


_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)