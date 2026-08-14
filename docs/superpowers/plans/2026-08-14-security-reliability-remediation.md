# Security and Reliability Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the verified API security, data-isolation, runtime, persistence, packaging, and CI defects without weakening the local CLI workflow.

**Architecture:** Centralize path validation and safe tool construction at application boundaries. Persist an owner on sessions and findings, enforce ownership in every query, and keep host command execution out of API agents. Retain the existing SQLite/FTS design while making migrations idempotent and testable.

**Tech Stack:** Python 3.9+, FastAPI, Pydantic v2, SQLite/FTS5, pytest, Docker, GitHub Actions.

---

## Files and Responsibilities

- `src/security/paths.py`: resolve and validate paths against a configured root.
- `src/api/server.py`: safe API tool set, authenticated owned-data routes, async dispatch.
- `src/storage/database.py`: ownership schema and owner-aware query methods.
- `src/harness/jwt_auth.py`: explicit administrator bootstrap.
- `src/memory/vector_store.py`: exactly-once batch insertion.
- `src/multi_agent/orchestrator.py`: correct executor security wiring.
- `src/tools/git_tools.py`, `src/app/cli.py`: explicit isolated clone destination.
- `tests/`: regression coverage for each corrected behavior.
- `requirements.txt`, `pyproject.toml`, `docker-compose.yml`, `.github/workflows/ci.yml`: reproducible deployment and verification.

### Task 1: Add Path Containment and API Tool Tests

**Files:**
- Create: `src/security/__init__.py`
- Create: `src/security/paths.py`
- Create: `tests/test_paths.py`
- Create: `tests/test_api_security.py`
- Modify: `src/api/server.py`

- [ ] **Step 1: Write failing path tests**

```python
def test_resolve_project_path_rejects_escape(tmp_path):
    from src.security.paths import resolve_under_root
    with pytest.raises(ValueError):
        resolve_under_root(tmp_path / "allowed", tmp_path / "outside")

def test_resolve_project_path_accepts_descendant(tmp_path):
    from src.security.paths import resolve_under_root
    root = tmp_path / "allowed"
    child = root / "repo"
    child.mkdir(parents=True)
    assert resolve_under_root(root, child) == child.resolve()
```

- [ ] **Step 2: Run the path tests and verify they fail because the module is absent**

Run: `py -m pytest tests/test_paths.py -v`

- [ ] **Step 3: Implement `resolve_under_root` with `Path.resolve()` and `relative_to()`**

```python
def resolve_under_root(root: Path | str, candidate: Path | str) -> Path:
    resolved_root = Path(root).resolve(strict=True)
    resolved_candidate = Path(candidate).resolve(strict=True)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("path is outside CODE_REVIEW_ALLOWED_ROOT") from exc
    return resolved_candidate
```

- [ ] **Step 4: Write a failing API tool registration test**

```python
def test_api_tools_do_not_include_host_command_execution():
    from src.api.server import TOOLS
    assert "run_command" not in {tool.name for tool in TOOLS}
```

- [ ] **Step 5: Replace API-global tools with a root-bound, read-only tool factory**

```python
def build_api_tools(project_root: Path) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="list_files",
            description="List files beneath the configured project root",
            parameters={"type": "object", "properties": {"pattern": {"type": "string"}}},
            fn=root_bound_list_files(project_root),
        ),
        ToolDefinition(
            name="read_file",
            description="Read a file beneath the configured project root",
            parameters={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
            fn=root_bound_read_file(project_root),
        ),
        ToolDefinition(
            name="grep_pattern",
            description="Search files beneath the configured project root",
            parameters={"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]},
            fn=root_bound_grep_pattern(project_root),
        ),
    ]
```

The wrappers must reject any requested path outside `project_root`; do not expose `run_command`.

- [ ] **Step 6: Run the focused security tests and commit**

Run: `py -m pytest tests/test_paths.py tests/test_api_security.py -v`

Commit: `git commit -am "fix: restrict API agent filesystem access"`

### Task 2: Add Ownership and Route Authorization

**Files:**
- Create: `tests/test_api_authorization.py`
- Modify: `src/storage/database.py`
- Modify: `src/api/server.py`

- [ ] **Step 1: Write failing ownership tests**

```python
def test_get_session_requires_matching_owner(database):
    session_id = database.create_session("review", "single", owner_username="alice")
    assert database.get_session(session_id, owner_username="bob") is None
    assert database.get_session(session_id, owner_username="alice")["id"] == session_id
```

- [ ] **Step 2: Run the ownership test and verify it fails on the current database API**

Run: `py -m pytest tests/test_api_authorization.py::test_get_session_requires_matching_owner -v`

- [ ] **Step 3: Add idempotent schema migrations and owner-aware database methods**

```python
conn.execute("ALTER TABLE sessions ADD COLUMN owner_username TEXT DEFAULT ''")
conn.execute("ALTER TABLE findings ADD COLUMN owner_username TEXT DEFAULT ''")
conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_username)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_findings_owner ON findings(owner_username)")
```

Require `owner_username` for create, get, list, and search operations. Ensure findings copy their session owner.

- [ ] **Step 4: Add `Depends(get_current_user)` to every persisted-data route**

```python
@app.get("/findings")
async def list_findings(current_user: User = Depends(get_current_user)):
    return db.get_findings(owner_username=current_user.username)
```

Return `404` for another user's session and preserve public health-only behavior.

- [ ] **Step 5: Run authorization regressions and commit**

Run: `py -m pytest tests/test_api_authorization.py tests/test_database.py -v`

Commit: `git commit -am "fix: isolate review data by owner"`

### Task 3: Repair Authentication, Orchestration, and FTS Semantics

**Files:**
- Create: `tests/test_auth_bootstrap.py`
- Modify: `tests/test_vector_store.py`
- Modify: `tests/test_agent_harness.py`
- Modify: `src/harness/jwt_auth.py`
- Modify: `src/memory/vector_store.py`
- Modify: `src/multi_agent/orchestrator.py`

- [ ] **Step 1: Write failing regressions**

```python
def test_missing_admin_password_does_not_create_default_admin(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        UserStore(str(tmp_path))

def test_add_batch_inserts_each_document_once(tmp_path):
    store = VectorStore(str(tmp_path / "search.db"))
    store.add_batch([FindingDocument(description_en="one")])
    assert store.count() == 1
```

- [ ] **Step 2: Run the two regressions and verify they fail**

Run: `py -m pytest tests/test_auth_bootstrap.py tests/test_vector_store.py -v`

- [ ] **Step 3: Require explicit admin bootstrap and preserve existing user stores**

```python
if "admin" not in self._users:
    password = os.getenv("ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("ADMIN_PASSWORD is required for first-time initialization")
    self.create_user("admin", password, role="admin", email="admin@code-review.local")
```

- [ ] **Step 4: Implement exactly-once FTS insertion and correct executor wiring**

```python
def add_batch(self, docs):
    ids = []
    for doc in docs:
        inserted = self.add_finding(doc)
        if inserted:
            ids.append(inserted)
    return ids
```

Attach sandbox, HITL, and memory to `self._executor_agent` immediately after it is created.

- [ ] **Step 5: Run focused regressions and commit**

Run: `py -m pytest tests/test_auth_bootstrap.py tests/test_vector_store.py tests/test_agent_harness.py -v`

Commit: `git commit -am "fix: harden bootstrap and agent runtime"`

### Task 4: Isolate CLI Clones and Prevent API Event-Loop Blocking

**Files:**
- Create: `tests/test_cli_isolation.py`
- Modify: `src/tools/git_tools.py`
- Modify: `src/app/cli.py`
- Modify: `src/api/server.py`

- [ ] **Step 1: Write failing clone destination and async dispatch tests**

```python
def test_clone_repo_requires_empty_destination(tmp_path):
    with pytest.raises(ValueError):
        clone_repo("https://github.com/acme/repo", destination=str(tmp_path / "missing"))

async def test_analyze_dispatches_sync_work_to_thread(monkeypatch):
    monkeypatch.setattr(server.asyncio, "to_thread", fake_to_thread)
    await server.analyze(request, current_user)
    assert fake_to_thread.called
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `py -m pytest tests/test_cli_isolation.py tests/test_api_security.py -v`

- [ ] **Step 3: Make clone destination explicit and ephemeral**

```python
def clone_repo(repo_url: str, branch: str = "main", destination: str = "") -> str:
    target = Path(destination).resolve()
    if not destination or target.exists() and any(target.iterdir()):
        raise ValueError("destination must be a new empty review directory")
```

Create this directory with `TemporaryDirectory` in the CLI and pass it to each Git operation.

- [ ] **Step 4: Move synchronous API analysis to `asyncio.to_thread`**

Keep database persistence on the request side after the worker returns. The worker receives only immutable request values and the root-bound tool set.

- [ ] **Step 5: Run regressions and commit**

Run: `py -m pytest tests/test_cli_isolation.py tests/test_api_security.py -v`

Commit: `git commit -am "fix: isolate CLI checkouts and API execution"`

### Task 5: Align Deployment and CI

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Modify: `docker-compose.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

- [ ] **Step 1: Write failing configuration assertions**

```python
def test_runtime_requirements_include_bcrypt():
    assert "bcrypt" in Path("requirements.txt").read_text(encoding="utf-8")

def test_ci_generates_uploaded_coverage_file():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--cov-report=xml" in workflow
    assert "|| true" not in workflow
```

- [ ] **Step 2: Run configuration tests and verify they fail**

Run: `py -m pytest tests/test_configuration.py -v`

- [ ] **Step 3: Declare direct dependencies and required compose configuration**

Add direct `bcrypt` to requirements, remove unused `passlib`, keep project metadata aligned, and pass `JWT_SECRET_KEY`, `ADMIN_PASSWORD`, and `CODE_REVIEW_ALLOWED_ROOT` through Compose. Run the container as a non-root user where image ownership permits it.

- [ ] **Step 4: Make CI enforce lint/type checks and write XML coverage**

```yaml
- name: Lint
  run: ruff check src/ tests/ --ignore E501,F841
- name: Type check
  run: mypy src/ --ignore-missing-imports
- name: Test with coverage
  run: pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-report=xml
```

- [ ] **Step 5: Run the full verification suite and commit**

Run: `py -m pytest tests/ -v --tb=short`

Run: `py -m compileall -q src tests`

Commit: `git commit -am "build: enforce reproducible security checks"`

## Final Verification

- [ ] Re-run the complete test suite, Ruff, mypy, and compileall from a clean worktree.
- [ ] Inspect `git diff --check` and confirm only planned files changed.
- [ ] Update the README startup instructions to require the three security environment variables.
