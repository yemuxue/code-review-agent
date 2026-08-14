# Startup Compatibility Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow the API to start from the documented `.env` configuration and upgrade existing SQLite databases without data loss.

**Architecture:** Import the existing configuration module before API authentication singletons so its existing dotenv loader populates missing process variables. Keep table creation independent of indexes that require post-release columns; apply those columns through the current idempotent migration, then create the indexes.

**Tech Stack:** Python 3.9+, FastAPI/Uvicorn, SQLite, pytest, python-dotenv.

---

### Task 1: Cover configuration loading before JWT initialization

**Files:**
- Modify: `tests/test_api_security.py`
- Modify: `src/api/server.py:38-44`

- [x] **Step 1: Write the failing import-order regression test**

```python
def test_server_loads_dotenv_before_auth(monkeypatch, tmp_path):
    import dotenv

    monkeypatch.setattr(dotenv, "dotenv_values", lambda _: {
        "JWT_SECRET_KEY": "dotenv-only-secret-that-is-long-enough",
        "ADMIN_PASSWORD": "dotenv-only-admin-password",
    })
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("src.config", None)
    sys.modules.pop("src.harness.jwt_auth", None)
    sys.modules.pop("src.api.server", None)

    server = importlib.import_module("src.api.server")

    assert server.jwt_auth.secret_key == "dotenv-only-secret-that-is-long-enough"
```

- [x] **Step 2: Run the test and verify it fails**

Run: `py -m pytest tests/test_api_security.py::test_server_loads_dotenv_before_auth -q`

Expected: FAIL with the missing `JWT_SECRET_KEY` runtime error.

- [x] **Step 3: Load the shared configuration before authentication imports**

In `src/api/server.py`, place this import before `src.harness.jwt_auth`:

```python
import src.config  # Load documented .env values before JWT singleton creation.
```

Do not call `init_config()`: API health and authentication startup should not
validate or contact the LLM provider.

- [x] **Step 4: Run the focused test and API security tests**

Run: `py -m pytest tests/test_api_security.py -q`

Expected: PASS; the module imports with values provided only through dotenv and
the API tool set still excludes host command execution.

- [x] **Step 5: Commit the configuration fix**

```bash
git add src/api/server.py tests/test_api_security.py
git commit -m "fix: load dotenv before API authentication"
```

### Task 2: Upgrade legacy SQLite ownership schema safely

**Files:**
- Modify: `tests/test_database.py`
- Modify: `src/storage/database.py:61-67,124-152`

- [x] **Step 1: Write the failing legacy-database migration test**

```python
def test_migrates_legacy_database_before_owner_indexes(self, tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT 'New Chat',
                mode TEXT NOT NULL DEFAULT 'Single', project_path TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                message_count INTEGER DEFAULT 0, finding_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0, total_duration_ms INTEGER DEFAULT 0
            );
            CREATE TABLE findings (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                file_path TEXT NOT NULL DEFAULT '', line INTEGER DEFAULT 0,
                category TEXT NOT NULL DEFAULT 'BUG', severity TEXT NOT NULL DEFAULT 'Medium',
                description_en TEXT DEFAULT '', description_cn TEXT DEFAULT '',
                suggestion TEXT DEFAULT '', verdict TEXT DEFAULT 'PENDING',
                verified INTEGER DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now')),
                embedding_id TEXT DEFAULT ''
            );
        """)

    Database(str(db_path))

    with sqlite3.connect(db_path) as conn:
        session_columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        finding_columns = {row[1] for row in conn.execute("PRAGMA table_info(findings)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(sessions)")}
    assert "owner_username" in session_columns
    assert "owner_username" in finding_columns
    assert "idx_sessions_owner" in indexes
```

- [x] **Step 2: Run the test and verify it fails**

Run: `py -m pytest tests/test_database.py::TestDatabase::test_migrates_legacy_database_before_owner_indexes -q`

Expected: FAIL with `sqlite3.OperationalError: no such column: owner_username`.

- [x] **Step 3: Defer ownership indexes until after the column migration**

Remove these two statements from `SCHEMA`:

```sql
CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_username);
CREATE INDEX IF NOT EXISTS idx_findings_owner ON findings(owner_username);
```

Retain the existing `ALTER TABLE ... ADD COLUMN owner_username` statements and
the two index creation calls at the end of `_run_migrations()`.

- [x] **Step 4: Run focused database tests**

Run: `py -m pytest tests/test_database.py -q`

Expected: PASS; both new and legacy databases expose owner columns and owner
indexes without removing any existing records.

- [x] **Step 5: Commit the migration fix**

```bash
git add src/storage/database.py tests/test_database.py
git commit -m "fix: migrate ownership columns before indexes"
```

### Task 3: Verify the production startup path

**Files:**
- Verify only: `src/api/server.py`, `src/storage/database.py`, `data/code_review.db`

- [x] **Step 1: Start the API against the existing database with the documented `.env` secret**

Run:

```powershell
py -m uvicorn src.api.server:app --host 127.0.0.1 --port 8010
```

Expected: Uvicorn reports `Application startup complete`.

- [x] **Step 2: Call the health endpoint from a separate shell**

Run: `Invoke-RestMethod http://127.0.0.1:8010/health`

Expected: JSON contains `status` equal to `healthy`.

- [x] **Step 3: Stop the foreground Uvicorn process**

Send `Ctrl+C` to the running process and verify it exits with a normal shutdown message.

- [x] **Step 4: Run the complete test suite**

Run: `py -m pytest tests -q`

Expected: PASS with no failures.

- [x] **Step 5: Inspect the final diff and commit verification**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors and only the intended source/test changes before
their task commits.
