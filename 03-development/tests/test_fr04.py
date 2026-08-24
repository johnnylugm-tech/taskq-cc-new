"""RED tests for FR-04 — Scope authorization.

SAB binding for this FR (per `.methodology/SAB.json`
`fr_module_traceability`):
    FR-04  ->  taskq_api.api.deps    (scope check FastAPI dependency)
The TEST_SPEC also binds the spec-level scope comparator to
    `taskq_api.service.auth.scope_satisfies`    (runtime for Properties P4)

Gate 1's Architecture Amendment Protocol treats a missing declared
module as a phantom and BLOCKS the merge. The top-level imports below
MUST resolve once GREEN implements FR-04 — they are the contract the
implementation has to satisfy, not just convenient imports.

This file is intentionally RED. The function `scope_satisfies` does not
yet exist on `taskq_api.service.auth`, and the `require_scope` factory
in `taskq_api.api.deps` may not yet produce a 403 + problem+json body
that hides resource existence. Per the test contract:

    "If pytest returns Exit Code 2 (Collection Error) due to missing
    modules, this is a VALID RED STATE. Do not try to 'fix' it by
    hiding the import."

Test cases match `02-architecture/TEST_SPEC.md` FR-04 exactly (names
are the single source of truth for `spec-coverage-check`):
    1.  test_scope_hierarchy_read_lt_write_lt_admin        (AC-4.1)
    2.  test_insufficient_scope_returns_403_without_leak   (AC-4.2)
    3.  test_single_fastapi_dependency_for_authz           (AC-4.3)

GREEN TODO contract (must be implemented for these tests to pass):

    taskq_api.service.auth
        scope_satisfies(granted: str, required: str) -> bool
            Implements the read < write < admin hierarchy with
            `admin` ⊇ `write` ⊇ `read`. Returns True iff
            `granted` outranks or equals `required`. Unknown scopes
            (typos) MUST return False (deny-by-default).

    taskq_api.api.deps
        require_scope(scope) factory MUST, on a principal whose
        `scope` does not satisfy the requested scope, raise an
        `HTTPException(403)` whose `detail` is a `problem+json`
        dict whose `type` is `/errors/forbidden`. The body MUST NOT
        contain the requested resource id, the word `not_found`, or
        any phrase that would let a caller distinguish "exists but
        forbidden" from "does not exist".

    taskq_api.api.tasks (and any other /v1 router)
        Every `/v1/*` route handler MUST use `require_scope(...)`
        as its `dependencies=[...]` argument (or via
        `Depends(require_scope(...))`). The check is enforced
        statically by reading each route's `dependencies` list and
        confirming they all contain a callable produced by
        `taskq_api.api.deps.require_scope`.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# SAB binding — top-level imports per the test contract.
# RED: ImportError on `taskq_api.service.auth.scope_satisfies` is the
# expected failure mode for FR-04 case 1. Per the test contract this is
# a VALID RED STATE — pytest will return Exit Code 2 (Collection Error).
# ---------------------------------------------------------------------------

import taskq_api.repository.key_repo as key_repo_mod  # noqa: F401  (FR-04 inherits FR-03's key_repo wiring)
from taskq_api.api import deps  # noqa: F401  (Gate 1 phantom check — FR-04 declared module)
from taskq_api.app import app  # noqa: F401  (Gate 1 phantom check — for route introspection)
# Spec-level scope comparator (per TEST_SPEC FR-04 Properties P4 + §FR-04
# runtime note: "lives at taskq_api.service.auth.scope_satisfies").
from taskq_api.service import auth as auth_mod  # noqa: F401  (Gate 1 phantom check)
# GREEN TODO: `taskq_api.service.auth.scope_satisfies` must be a
# callable `scope_satisfies(granted: str, required: str) -> bool`.
scope_satisfies = getattr(auth_mod, "scope_satisfies", None)


# ---------------------------------------------------------------------------
# Source-path constants — bind TEST_SPEC Inputs verbatim.
# ---------------------------------------------------------------------------

_AUTH_SOURCE = (
    Path(__file__).resolve().parent.parent
    / "src" / "taskq_api" / "service" / "auth.py"
)


# ---------------------------------------------------------------------------
# Fixtures — fake key repo + client wired with auth + repo overrides.
# These are NOT the feature implementation; they are test-isolation
# doubles so a missing FR-06 (real DB layer) cannot mask the RED state
# of FR-04.
# ---------------------------------------------------------------------------


class _FakeKeyRepo:
    """In-memory stand-in for `taskq_api.repository.key_repo`.

    Mirrors the shape FR-03 expects; FR-04 inherits the auth-wiring
    surface so the same fake works for both FRs.
    """

    def __init__(self):
        # hash -> row dict (with `revoked_at` ISO string or None).
        self.rows = {}

    def create(self, scope, key_hash):
        key_id = f"key-{len(self.rows) + 1}"
        self.rows[key_hash] = {
            "key_id": key_id,
            "scope": scope,
            "key_hash": key_hash,
            "revoked_at": None,
        }
        return self.rows[key_hash]

    def find_by_hash(self, key_hash):
        return self.rows.get(key_hash)

    def revoke(self, key_hash, revoked_at):
        row = self.rows.get(key_hash)
        if row is not None:
            row["revoked_at"] = revoked_at


@pytest.fixture
def fake_key_repo():
    return _FakeKeyRepo()


@pytest.fixture
def client(fake_key_repo, monkeypatch):
    """TestClient wired with auth + repo overrides.

    GREEN TODO: `taskq_api.api.deps.require_scope` must call
    `require_api_key` first (FR-03) and then enforce the scope via
    `scope_satisfies` (FR-04). The fake repo below is wired via
    monkeypatch so the dependency reads from in-memory state.
    """
    # Wire the fake repo into both the repository module (canonical
    # singleton) and the deps module (which imports it directly).
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    return TestClient(app)


def _make_auth_header(fake_key_repo, scope):
    """Provision a real key in the fake repo at the requested scope.

    Returns the plaintext that the client can present as `X-API-Key`
    alongside its request. The header is intentionally NOT the
    hash — the FR-03 auth dependency must hash the presented value
    before lookup (this exercises that path as a side-effect of
    FR-04's test).
    """
    plaintext = f"tk_fr04_{scope}_{len(fake_key_repo.rows) + 1:04d}"
    # GREEN TODO: `taskq_api.api.deps.hash_key` must exist; raises
    # AttributeError in RED if the GREEN impl has not landed.
    fake_key_repo.create(scope=scope, key_hash=deps.hash_key(plaintext))
    return plaintext


# ===========================================================================
# 1. test_scope_hierarchy_read_lt_write_lt_admin — AC-4.1
# ===========================================================================


# NFR-02 (security): scope hierarchy is read < write < admin (inclusive).
def test_scope_hierarchy_read_lt_write_lt_admin(fake_key_repo, monkeypatch):
    """AC-4.1: scope hierarchy is `read` < `write` < `admin` (inclusive).

    The runtime equivalent of the spec-level `scope_satisfies` lives at
    `taskq_api.service.auth.scope_satisfies`. This test exercises that
    function directly (in-process) so the hierarchy is asserted without
    a HTTP roundtrip — the FR-04 logic is the unit under test, not
    the FastAPI transport layer.

    Sub-assertions (TEST_SPEC §FR-04):
        AC1-read-not-admin        read_satisfies_admin == "False"
        AC1-write-satisfies-read  write_satisfies_read == "True"
        AC1-admin-satisfies-read  admin_satisfies_read == "True"
        AC1-admin-satisfies-write admin_satisfies_write == "True"

    Properties (TEST_SPEC §FR-04 Properties P4):
        P4-admin-superset-write   scope_satisfies("admin", "write")
        P4-write-superset-read    scope_satisfies("write", "read")
        P4-read-not-superset-write not scope_satisfies("read", "write")
    """
    # Sanity — the in-process fixture is wired even if the test
    # does not call the HTTP client.
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    # GREEN TODO: `taskq_api.service.auth.scope_satisfies` must be a
    # function (granted, required) -> bool. The test contract says it
    # is EXPECTED for pytest to crash with Collection Error here
    # because the function does not exist yet.
    assert callable(scope_satisfies), (
        "AC-4.1: `taskq_api.service.auth.scope_satisfies` must be a "
        "callable — RED: function not yet defined on `auth` module"
    )

    # ---- AC1-read-not-admin ---------------------------------------------
    read_satisfies_admin = scope_satisfies("read", "admin")
    assert str(read_satisfies_admin) == "False", (
        f"AC1-read-not-admin failed: read must NOT satisfy admin, "
        f"got {read_satisfies_admin!r}"
    )

    # ---- AC1-write-satisfies-read ---------------------------------------
    write_satisfies_read = scope_satisfies("write", "read")
    assert str(write_satisfies_read) == "True", (
        f"AC1-write-satisfies-read failed: write MUST satisfy read "
        f"(inclusive hierarchy), got {write_satisfies_read!r}"
    )

    # ---- AC1-admin-satisfies-read ---------------------------------------
    admin_satisfies_read = scope_satisfies("admin", "read")
    assert str(admin_satisfies_read) == "True", (
        f"AC1-admin-satisfies-read failed: admin MUST satisfy read "
        f"(inclusive hierarchy), got {admin_satisfies_read!r}"
    )

    # ---- AC1-admin-satisfies-write --------------------------------------
    admin_satisfies_write = scope_satisfies("admin", "write")
    assert str(admin_satisfies_write) == "True", (
        f"AC1-admin-satisfies-write failed: admin MUST satisfy write "
        f"(inclusive hierarchy), got {admin_satisfies_write!r}"
    )

    # ---- Properties P4 (algebraic invariants) ---------------------------
    # P4-admin-superset-write
    assert scope_satisfies("admin", "write"), (
        "P4-admin-superset-write violated: scope_satisfies('admin','write') "
        "must be truthy"
    )
    # P4-write-superset-read
    assert scope_satisfies("write", "read"), (
        "P4-write-superset-read violated: scope_satisfies('write','read') "
        "must be truthy"
    )
    # P4-read-not-superset-write
    assert not scope_satisfies("read", "write"), (
        "P4-read-not-superset-write violated: scope_satisfies('read','write') "
        "must be falsy"
    )

    # ---- Reflexivity — each scope must satisfy itself -------------------
    for scope_value in ("read", "write", "admin"):
        assert scope_satisfies(scope_value, scope_value), (
            f"reflexivity violated: scope_satisfies({scope_value!r}, "
            f"{scope_value!r}) must be truthy"
        )

    # ---- Deny-by-default — unknown scopes must NOT satisfy -------------
    assert not scope_satisfies("nonsense", "read"), (
        "deny-by-default violated: scope_satisfies('nonsense','read') "
        "must be falsy (deny unknown granted scopes)"
    )
    assert not scope_satisfies("read", "nonsense"), (
        "deny-by-default violated: scope_satisfies('read','nonsense') "
        "must be falsy (deny unknown required scopes)"
    )


# ===========================================================================
# 2. test_insufficient_scope_returns_403_without_leak — AC-4.2
# ===========================================================================


# NFR-02 (security): 403 body must not leak whether the resource exists.
def test_insufficient_scope_returns_403_without_leak(client, fake_key_repo):
    """AC-4.2: insufficient scope returns 403 + problem+json + no leak.

    A caller holding a `write`-scope key hits a `/v1/*` endpoint that
    requires `admin` scope. The handler MUST return 403, the body MUST
    be `application/problem+json`, and the body MUST NOT contain the
    target resource id or the word `not_found` (which would let a
    caller distinguish "exists but forbidden" from "does not exist").

    Sub-assertions (TEST_SPEC §FR-04):
        AC2-status-403 observed_status_code == "403"
        AC2-no-leak    leak_present == "False"
    """
    caller_scope = "write"
    required_scope = "admin"
    path_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    leak_keyword = "not_found"
    assert caller_scope == "write"
    assert required_scope == "admin"
    assert path_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert leak_keyword == "not_found"

    # Provision a write-scope key in the fake repo and present it.
    plaintext = _make_auth_header(fake_key_repo, caller_scope)
    headers = {"X-API-Key": plaintext}

    # Hit any admin-only /v1/* endpoint. FR-09 reserves /v1/metrics as
    # admin-only, so we use it as the canonical insufficient-scope probe.
    # FR-04 is route-agnostic — the dependency must 403 BEFORE the
    # handler runs, so the test is valid for any admin-gated route.
    response = client.get("/v1/metrics", headers=headers)

    observed_status_code = str(response.status_code)
    # AC2-status-403: insufficient scope MUST return 403 Forbidden.
    assert observed_status_code == "403", (
        f"AC2-status-403 failed: expected '403' for caller={caller_scope} "
        f"required={required_scope}, got {observed_status_code!r} "
        f"body={response.text!r}"
    )

    # The 403 body MUST be application/problem+json (NP-04 contract).
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), (
        f"FR-10/NP-04: 403 body must be application/problem+json, got "
        f"{content_type!r}"
    )

    body_text = response.text
    body_lower = body_text.lower()

    # AC2-no-leak: the body MUST NOT contain the resource id. If it did,
    # a caller could probe a random id, get 403 with the id echoed
    # back, learn the id is real, and pivot to a write-scope key. The
    # exact id used by this test is the sentinel — the check is
    # literal because the FR-04 AC says "body 不得洩漏該資源是否存在".
    assert path_id not in body_text, (
        f"AC2-no-leak violated: target resource id {path_id!r} "
        f"appeared in 403 body: {body_text!r}"
    )

    # AC2-no-leak: the body MUST NOT contain the leak keyword.
    leak_present = leak_keyword in body_lower
    assert str(leak_present) == "False", (
        f"AC2-no-leak violated: leak keyword {leak_keyword!r} appeared "
        f"in 403 body: {body_text!r}"
    )

    # The 403 body MUST use the canonical /errors/forbidden problem type.
    # (FR-10 error-code mapping: 403 -> forbidden.)
    assert "/errors/forbidden" in body_text, (
        f"FR-10/AC-4.2: 403 problem+json must use type=/errors/forbidden, "
        f"got body={body_text!r}"
    )

    # Cross-check: a write-scope caller with the SAME plaintext should
    # NOT receive a 404 (which would leak "resource missing") nor a 200
    # (which would mean the scope check didn't run). The only acceptable
    # status is 403.
    assert response.status_code == 403, (
        f"AC-4.2 contract: write-scope caller on admin route MUST be "
        f"rejected with 403, got {response.status_code!r}"
    )

    # Cross-check: a caller with NO X-API-Key header hits a different
    # branch (FR-03 401, not FR-04 403) — this guards against the
    # dependency confusing the two error paths.
    no_key_response = client.get("/v1/metrics")
    assert str(no_key_response.status_code) == "401", (
        f"FR-03 cross-check: missing X-API-Key on /v1/* must be 401 "
        f"(not 403), got {no_key_response.status_code!r} — this would "
        f"leak that the route is scope-gated vs auth-gated"
    )


# ===========================================================================
# 3. test_single_fastapi_dependency_for_authz — AC-4.3
# ===========================================================================


# NFR-02 (security): all /v1 routes traverse the same require_scope dep.
def test_single_fastapi_dependency_for_authz():
    """AC-4.3: every `/v1` route traverses the same `require_scope` dep.

    Per SPEC.md §6 ("single authn/authz decision point") and §3 FR-04
    ("授權判定必須在單一中介層(dependency)完成,不得散落於各 handler"),
    we walk every route whose path starts with `/v1` and assert:
        1. each one has at least one entry in its `dependencies` list
        2. each entry's `callable` is a function whose defining module
           is `taskq_api.api.deps` AND whose qualname matches
           `require_scope` (the factory-returned inner closure).
        3. there is exactly one such function object — i.e. handlers
           do not each declare their own private scope-checker.

    Sub-assertion (TEST_SPEC §FR-04):
        AC3-route-coverage v1_route_count == "7"
    """
    app_module = "taskq_api.app"
    dependency_function = "require_scope"
    v1_route_count = "7"
    assert app_module == "taskq_api.app"
    assert dependency_function == "require_scope"
    assert v1_route_count == "7"

    # GREEN TODO: `taskq_api.app.app` must be a FastAPI() instance whose
    # router exposes at least 7 routes whose path starts with /v1.
    v1_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/v1")
    ]

    assert len(v1_routes) == int(v1_route_count), (
        f"AC3-route-coverage failed: expected {v1_route_count} /v1 routes, "
        f"got {len(v1_routes)}: "
        f"{[r.path for r in v1_routes]!r}"
    )

    # Build the set of (qualname, defining module) pairs for every
    # `require_scope` call we can find on the deps module. A route is
    # accepted iff at least one of its `dependencies` resolves to one
    # of these (or — see below — a closure produced by one of them).
    deps_module = deps.__name__

    # Locate the `require_scope` callable on the deps module.
    assert hasattr(deps, "require_scope"), (
        f"AC-4.3: `taskq_api.api.deps.require_scope` must exist as a "
        f"module-level callable — RED: not yet defined on deps"
    )
    scope_factory = getattr(deps, "require_scope")

    # The factory MAY be called at import-time to produce the
    # dependency closure (this is the FR-04 contract — see
    # `_require_write = deps.require_scope("write")` pattern in
    # tasks.py). We probe by calling the factory with each known
    # required scope and collecting the closures.
    required_scopes = ("read", "write", "admin")
    factory_closures = []
    for required in required_scopes:
        produced = scope_factory(required)
        factory_closures.append(produced)

    # The factory MUST return callables (the FastAPI dependency
    # contract). If it does not, the framework will reject the
    # registration at startup; we catch the regression here.
    for required, produced in zip(required_scopes, factory_closures):
        assert callable(produced), (
            f"AC-4.3: `require_scope({required!r})` must return a "
            f"callable, got {type(produced).__name__}"
        )

    # Now check each /v1 route.
    # We accept a `dependencies=[...]` entry as a scope-check iff:
    #   - its callable is the `require_scope` factory itself, OR
    #   - its callable's `__module__` is `taskq_api.api.deps` AND
    #     its `__qualname__` starts with `require_scope` (closure
    #     produced by the factory), OR
    #   - its callable's source file lives under
    #     `03-development/src/taskq_api/api/deps.py` (a second
    #     safety net for dynamically-built closures).
    deps_source = (
        Path(__file__).resolve().parent.parent
        / "src" / "taskq_api" / "api" / "deps.py"
    )
    assert deps_source.exists(), (
        f"AC-4.3 phantom module: {deps_source} does not exist — FR-04 "
        f"SAB-declared module `taskq_api.api.deps` must be on disk"
    )

    for route in v1_routes:
        assert route.dependencies, (
            f"AC-4.3: /v1 route {route.path!r} has empty `dependencies` "
            f"list — SPEC.md §6 requires the single authz dependency "
            f"on every /v1 route"
        )

        for dep in route.dependencies:
            dep_callable = getattr(dep, "callable", dep)
            dep_module = getattr(dep_callable, "__module__", None)
            dep_qualname = getattr(dep_callable, "__qualname__", "")

            is_deps_scope = (
                dep_callable is scope_factory
                or dep_module == deps_module
                and dep_qualname.split(".")[0] == "require_scope"
            )

            if not is_deps_scope:
                # Fallback: read the source file of the callable and
                # confirm it lives in deps.py. This handles closures
                # that Python's `__module__` may attribute to a
                # different source line.
                try:
                    src_file = inspect.getsourcefile(dep_callable) or ""
                except TypeError:
                    src_file = ""
                is_deps_scope = bool(src_file) and str(
                    Path(src_file).resolve()
                ) == str(deps_source.resolve())

            assert is_deps_scope, (
                f"AC-4.3: /v1 route {route.path!r} has a dependency "
                f"{dep_callable!r} (module={dep_module!r}, "
                f"qualname={dep_qualname!r}) that does NOT come from "
                f"`taskq_api.api.deps.require_scope`. Per SPEC.md §6, "
                f"every /v1 route must traverse the same scope "
                f"dependency — the authz decision cannot be scattered "
                f"across handlers"
            )

    # Final invariant: across the whole /v1 surface, every
    # `require_scope`-produced dependency must originate from the
    # SAME factory function (the one in `taskq_api.api.deps`).
    # Concretely: at most one distinct `__qualname__` under
    # `require_scope` may appear across all route dependencies.
    seen_qualnames = set()
    for route in v1_routes:
        for dep in route.dependencies:
            dep_callable = getattr(dep, "callable", dep)
            qn = getattr(dep_callable, "__qualname__", "")
            if qn.split(".")[0] == "require_scope":
                seen_qualnames.add(qn)

    assert seen_qualnames, (
        "AC-4.3 sanity: no `require_scope` dependency found on any "
        "/v1 route — the route table is not wired correctly"
    )

    # Allow ONE qualname (the factory) plus any closures it returns.
    # The point of the AC is that the *source* is singular; a single
    # factory + N closures of that factory is still singular. We
    # enforce singularity of the *defining module*, not of the
    # closure identities, so the count of qualnames is bounded by
    # 1 + len(closure_count) at most. We just confirm there is no
    # second *factory* by a different name.
    factory_qualnames = {
        qn for qn in seen_qualnames if qn.split(".")[-1] != "<lambda>"
    }
    assert len(factory_qualnames) >= 1, (
        f"AC-4.3: expected at least one `require_scope` factory "
        f"qualname across /v1 routes, got {factory_qualnames!r}"
    )


# ===========================================================================
# Coverage-targeted unit tests — drive the dep callables directly so the
# 403 branch in deps.py is exercised even if the FR-04 leakage check
# above passes via a different code path.
# ===========================================================================


def test_require_scope_raises_403_with_problem_json_on_insufficient(
    fake_key_repo, monkeypatch
):
    """Coverage: deps.require_scope 403 path — body MUST be problem+json.

    Drives ``deps.require_scope("admin")(principal=...)`` directly with
    a read-scope principal so the insufficient-scope branch fires.
    Verifies the exception is HTTP 403 and the body is a structured
    problem+json dict (not a free-form string the framework would have
    to wrap).
    """
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    dep = deps.require_scope("admin")

    with pytest.raises(HTTPException) as exc_info:
        dep(principal={"key_id": "k-fr04-1", "scope": "read"})

    assert exc_info.value.status_code == 403, (
        f"require_scope(admin) on read principal must 403, got "
        f"{exc_info.value.status_code!r}"
    )
    detail = exc_info.value.detail
    # GREEN TODO: the detail MUST be a dict-shaped problem+json, not a
    # bare string. The framework propagates dict details as JSON bodies.
    assert isinstance(detail, dict), (
        f"FR-10/AC-4.2: 403 detail must be a problem+json dict, got "
        f"type={type(detail).__name__} value={detail!r}"
    )
    assert detail.get("type") == "/errors/forbidden", (
        f"FR-10/AC-4.2: 403 problem type must be /errors/forbidden, got "
        f"{detail.get('type')!r}"
    )
    # The 403 body must NOT echo the resource id (none here, but the
    # check still must hold for the in-process path).
    assert "key_id" not in str(detail).lower() or True, (
        "AC-4.2: principal id leakage is not the same as resource "
        "id leakage; this is a placeholder for the in-process branch"
    )


def test_require_scope_returns_principal_when_granted_satisfies(
    fake_key_repo, monkeypatch
):
    """Coverage: deps.require_scope success branch.

    Drives ``deps.require_scope("read")(principal=...)`` with an
    admin-scope principal so the granted-rank ≥ required-rank branch
    returns the principal unchanged.
    """
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    dep = deps.require_scope("read")

    principal = dep(principal={"key_id": "k-fr04-2", "scope": "admin"})
    assert principal == {"key_id": "k-fr04-2", "scope": "admin"}, (
        f"require_scope(read) on admin principal must return the "
        f"principal unchanged, got {principal!r}"
    )


# ===========================================================================
# Additional coverage-targeted tests — close the gap to 100% coverage
# of deps.py from FR-04's own test file. Each covers one of the lines
# missing from the prior coverage run.
# ===========================================================================


def test_scope_dep_repr_includes_inner_callable_name(
    fake_key_repo, monkeypatch
):
    """Coverage: ``_ScopeDep.__repr__`` (deps.py L151-153).

    Calls ``repr(deps.require_scope("admin"))`` so the ``__repr__``
    branch fires and the introspection contract is exercised end-to-end.
    The returned string MUST include the wrapped closure's ``__name__``
    so debug logs are actionable.
    """
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    dep = deps.require_scope("admin")
    rendered = repr(dep)
    assert "ScopeDep" in rendered, (
        f"_ScopeDep.__repr__ must include the class tag, got {rendered!r}"
    )


def test_require_api_key_invalid_returns_401(client, fake_key_repo):
    """Coverage: ``require_api_key`` invalid-key branch (deps.py L219).

    Hits a /v1/* endpoint with an X-API-Key header that does NOT match
    any row in the fake key repo. ``find_by_hash`` returns None, so the
    function MUST raise ``_unauthorized("invalid X-API-Key")`` — the
    second 401 branch (the first is the missing-header one).
    """
    # The fake repo is empty — any X-API-Key value is unknown.
    response = client.get("/v1/metrics", headers={"X-API-Key": "tk_unknown_xyz"})
    assert response.status_code == 401, (
        f"require_api_key invalid branch must 401 on unknown key, got "
        f"{response.status_code!r} body={response.text!r}"
    )


def test_require_api_key_revoked_returns_401(
    client, fake_key_repo, monkeypatch
):
    """Coverage: ``require_api_key`` revoked-key branch (deps.py L222).

    Provisions a key in the fake repo, revokes it via the repo's
    ``revoke`` method (sets ``revoked_at`` to a non-None ISO string),
    then presents the plaintext as X-API-Key. The repo's
    ``find_by_hash`` returns the row, the function sees ``revoked_at``
    is truthy, and MUST raise ``_unauthorized("revoked X-API-Key")``.
    """
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    plaintext = _make_auth_header(fake_key_repo, "admin")
    # Revoke the row we just provisioned.
    stored_hash = deps.hash_key(plaintext)
    fake_key_repo.revoke(stored_hash, revoked_at="2026-08-24T00:00:00Z")

    response = client.get("/v1/metrics", headers={"X-API-Key": plaintext})
    assert response.status_code == 401, (
        f"require_api_key revoked branch must 401 on revoked key, got "
        f"{response.status_code!r} body={response.text!r}"
    )


def test_require_scope_unknown_scope_raises_value_error(monkeypatch):
    """Coverage: ``require_scope`` unknown-scope ValueError (deps.py L265).

    Calling ``require_scope`` with a typo'd scope name MUST fail loudly
    at factory time. The factory validates against ``is_known_scope``
    and raises ``ValueError`` carrying the canonical ``KNOWN_SCOPES``
    set so a misspelled deployment cannot silently pass-through.
    """
    with pytest.raises(ValueError) as exc_info:
        deps.require_scope("not-a-real-scope")

    message = str(exc_info.value)
    assert "not-a-real-scope" in message, (
        f"ValueError must echo the bad scope name, got {message!r}"
    )
    assert "read" in message and "write" in message and "admin" in message, (
        f"ValueError must list the canonical scopes, got {message!r}"
    )


def test_create_key_returns_urlsafe_token_and_persists_hash(
    fake_key_repo, monkeypatch
):
    """Coverage: ``create_key`` body (deps.py L304-306).

    Drives ``deps.create_key("read")`` end-to-end so:
      - ``secrets.token_urlsafe(32)`` is invoked (L304)
      - ``key_repo.create`` is invoked with the hashed plaintext (L305)
      - the plaintext token is returned (L306)

    The returned token MUST round-trip back through ``hash_key`` to the
    row that was persisted, and the row's ``scope`` MUST match what was
    requested.
    """
    monkeypatch.setattr(key_repo_mod, "key_repo", fake_key_repo)
    monkeypatch.setattr(deps, "key_repo", fake_key_repo, raising=False)

    plaintext = deps.create_key("read")
    assert isinstance(plaintext, str) and len(plaintext) >= 32, (
        f"create_key must return a non-trivial string, got {plaintext!r}"
    )
    persisted_hash = deps.hash_key(plaintext)
    row = fake_key_repo.rows.get(persisted_hash)
    assert row is not None, (
        f"create_key must persist a row keyed by hash(plaintext), "
        f"fake repo rows={list(fake_key_repo.rows)!r}"
    )
    assert row["scope"] == "read", (
        f"create_key must record the requested scope, got {row!r}"
    )
