# taskq-api — top-level Makefile.
#
# NFR-12 (SPEC §4): the harness always calls `make verify-system` at every
# exit gate (2, 3, 4). A non-zero exit fails the gate. Stdout must contain
# `verify-system: PASS` for the gate to pass.
#
# The target exercises the real `taskq_api` entry point — `alembic upgrade
# head` against a real SQLite file, the full pytest suite (including the
# httpx integration suite), a uvicorn boot + `/healthz` + `/readyz` smoke,
# and a downgrade→upgrade round-trip proving FR-07 reversibility.

PYTHON     ?= python3
PYTHON_MIN := 3.11

# Point coverage at the whole source tree, not the bare "." default that
# pytest-cov would measure (its default includes test files and any
# fixtures the test run creates).
COV_TARGET ?= 03-development/src/taskq_api

# `taskq_api` lives at ``03-development/src`` (per SPEC §3). Every
# recipe that imports it (uvicorn smoke, alembic env.py target_metadata)
# needs the source tree on PYTHONPATH. Tests' own conftest.py sets
# this; the smoke target does not, so we hoist it here.
SRC_DIR := 03-development/src

# `alembic upgrade head` reads ``sqlalchemy.url`` from ``alembic.ini``
# (which is intentionally empty — the env var is the override knob)
# or ``TASKQ_DB_URL`` (consumed by
# ``03-development/src/migrations/env.py``). ``verify-system`` runs
# ``make migrate`` from a fresh shell, so the URL must have a sensible
# default; otherwise alembic errors out before any migration runs.
TASKQ_DB_URL ?= sqlite:///$(shell pwd)/.sessi-work/verify_system.sqlite
export TASKQ_DB_URL
export PYTHONPATH := $(SRC_DIR):$(PYTHONPATH)

.PHONY: help install lock migrate verify-system test test-unit test-integration \
        lint type-check security mutation coverage verify-cleanup downgrade-upgrade \
        smoke boot check-python

help:
	@echo "Targets:"
	@echo "  install          Install runtime deps from requirements.lock"
	@echo "  lock             Regenerate requirements.lock from requirements.txt"
	@echo "  migrate          Run alembic upgrade head against TASKQ_DB_URL"
	@echo "  test             Run full pytest suite (unit + integration)"
	@echo "  test-unit        Run only unit tests (no DB round-trip)"
	@echo "  test-integration Run integration tests (httpx ASGITransport)"
	@echo "  lint             ruff check 03-development/src/"
	@echo "  type-check       pyright on 03-development/src/"
	@echo "  security         bandit + grep gate (shell=True|eval(|exec()"
	@echo "  mutation         mutmut run on service + repository layers"
	@echo "  coverage         pytest with --cov=03-development/src"
	@echo "  verify-system    NFR-12: full e2e verification (gates 2/3/4)"
	@echo "  smoke            Boot uvicorn + curl /healthz and /readyz"
	@echo ""
	@echo "Override interpreter: make PYTHON=/path/to/python3.11 verify-system"

check-python:
	@$(PYTHON) -c "\
import sys; \
v = sys.version_info; \
ok = v >= (3, 11); \
status = 'OK' if ok else 'FAIL — 3.11+ required'; \
print(f'Python {v.major}.{v.minor}.{v.micro}  [{sys.executable}]  {status}'); \
sys.exit(0 if ok else 1)" || { \
	echo ""; \
	echo "Fix: ensure 'python3' resolves to 3.11+ (PEP 3.11 minimum)."; \
	exit 1; \
}

install: check-python
	$(PYTHON) -m pip install -r requirements.lock

lock: check-python
	$(PYTHON) -m piptools compile --output-file=requirements.lock requirements.txt

migrate: check-python
	@mkdir -p .sessi-work
	$(PYTHON) -m alembic upgrade head

test: check-python
	$(PYTHON) -m pytest 03-development/tests -q --tb=short --cov=$(COV_TARGET) --cov-report=term-missing

test-unit: check-python
	$(PYTHON) -m pytest 03-development/tests/unit -q --tb=short

test-integration: check-python
	$(PYTHON) -m pytest 03-development/tests/integration -q --tb=short --cov=$(COV_TARGET) --cov-report=term-missing

lint: check-python
	$(PYTHON) -m ruff check 03-development/src/ --extend-ignore RUF001,RUF002,RUF003

type-check: check-python
	$(PYTHON) -m pyright 03-development/src/

security: check-python
	@echo "== bandit =="
	$(PYTHON) -m bandit -r 03-development/src/ -q
	@echo "== grep shell=True|eval(|exec( =="
	@! grep -rE 'shell=True|eval\(|exec\(' 03-development/src/ --include='*.py'
	@echo "shell/eval/exec grep: 0 hits"

mutation: check-python
	$(PYTHON) -m mutmut run --paths-to-mutate 03-development/src/taskq_api/service,03-development/src/taskq_api/repository

coverage: check-python
	$(PYTHON) -m pytest 03-development/tests --cov=$(COV_TARGET) --cov-report=term-missing --cov-report=json:.sessi-work/coverage.json -q

# NFR-12 — system verification target. Steps are ordered so the gate fails
# on the cheapest signal first (lint + type + security), then the unit
# suite, then the integration suite (which spins up an httpx ASGI client),
# then a real alembic round-trip, then a uvicorn boot + health smoke.
# The `verify-system: PASS` line is REQUIRED — the harness grep-checks it.
verify-system: lint type-check test test-integration migrate smoke downgrade-upgrade
	@echo ""
	@echo "All verification steps passed."
	@echo "verify-system: PASS"

# Used inside verify-system — proves FR-07 reversibility (downgrade → upgrade
# round-trip preserves row-by-row sample data).
downgrade-upgrade: check-python
	$(PYTHON) -m alembic downgrade base
	$(PYTHON) -m alembic upgrade head
	@echo "downgrade → upgrade round-trip: PASS"

# Used inside verify-system — boot uvicorn in the background, curl the
# health endpoints, kill it. Non-zero exit on any smoke failure.
smoke: check-python
	@if [ ! -f .env ]; then cp .env.example .env; fi
	$(PYTHON) -m uvicorn taskq_api.app:app --host 127.0.0.1 --port 8765 --log-level warning &
	@UVICORN_PID=$$!; \
	sleep 2; \
	curl -fsS http://127.0.0.1:8765/healthz || { kill $$UVICORN_PID; exit 1; }; \
	curl -fsS http://127.0.0.1:8765/readyz || { kill $$UVICORN_PID; exit 1; }; \
	kill $$UVICORN_PID; \
	wait $$UVICORN_PID 2>/dev/null; \
	echo "smoke: PASS"

# Used inside verify-system — alias for clarity.
boot: smoke
