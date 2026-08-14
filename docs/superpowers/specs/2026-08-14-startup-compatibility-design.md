# Startup Compatibility Remediation Design

## Goal

Restore API startup for an installation that uses the documented `.env` file
and an existing SQLite database created before owner isolation was introduced.

## Scope

- Load dotenv configuration before API-level authentication singletons are
  created, without weakening the required-secret checks.
- Make SQLite schema upgrades safe for existing databases by adding new columns
  before creating indexes that depend on them.
- Add regression tests for both startup paths and verify a real Uvicorn start.

## Design

`src.api.server` will import the configuration loader before it initializes
`JWTAuth`. The loader retains its current precedence: real process environment
variables win, while values absent from the process are populated from `.env`.
JWT authentication continues to fail fast if no secret exists after loading.

`Database` will initialize tables and then apply idempotent column migrations
before creating ownership indexes. A freshly created database already contains
the columns; an existing database receives them through `ALTER TABLE`. Index
creation occurs only after both table layouts are compatible. The migration does
not delete or rewrite existing sessions or findings; their owner value remains
the explicit empty-string default until application policy assigns ownership.

## Verification

- Regression-test importing the API with secrets defined only in `.env`.
- Regression-test opening a database with the pre-owner schema and asserting
  the columns and indexes are installed.
- Start Uvicorn against the existing project database and call `/health`.
- Run the complete `tests` suite.

## Non-goals

- No password reset or modification of existing users.
- No migration framework replacement or database format change.
- No external LLM request as part of the startup verification.
