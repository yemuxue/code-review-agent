# Security and Reliability Remediation Design

## Scope

This design addresses the verified defects from the project review: unsafe API
tool execution, missing API authentication and authorization, unsafe default
credentials, ineffective CLI isolation, duplicated FTS writes, incorrect
orchestrator security wiring, deployment dependency drift, blocking API work,
and ineffective CI checks.

## Security Boundary

The HTTP API will operate on a configured `CODE_REVIEW_ALLOWED_ROOT` directory.
Every requested project and file path must resolve beneath that root before it
is passed to an agent tool. API agents will receive read-only tools only; host
`run_command` will not be registered. Test execution will be an explicit,
allowlisted DockerSandbox operation with no network and a per-request work
directory. CLI PR reviews will clone into a new temporary review directory and
will never clone into the caller's current directory.

## Identity and Data Isolation

The first administrator will be created only when `ADMIN_PASSWORD` is set.
There will be no fallback credential. Sessions and findings will store an
`owner_username`; all routes that return or search persisted review data will
require a bearer token and filter by that owner. Administrative aggregate
statistics will remain administrator-only. Existing local records without an
owner will be treated as inaccessible rather than silently exposed.

## Runtime Behaviour

API analysis will use a worker thread for synchronous LLM and orchestration
calls so it does not block FastAPI's event loop. Both the legacy orchestrator
and LangGraph factory will receive security components through explicit
constructor arguments. The legacy executor will receive its own sandbox, HITL,
and memory settings rather than accidentally modifying the planner.

## Persistence and Packaging

`VectorStore.add_batch` will insert each document exactly once. The database
schema migration will add ownership fields and indexes idempotently. Runtime
dependencies will be declared consistently in `pyproject.toml` and
`requirements.txt`; Docker Compose will pass the required JWT and administrator
configuration. CI will fail on lint/type-check failures and produce the XML
coverage artifact it uploads.

## Compatibility and Error Handling

Requests outside the configured root will return a validation error. Requests
for another user's session will return `404` to avoid resource enumeration.
Deployments without `ADMIN_PASSWORD` and an existing user database will fail at
startup. Local CLI review remains supported, but its checkout directory becomes
ephemeral and is removed after the review.

## Test Strategy

Tests will be added before each implementation change. They will cover path
containment, API tool registration, unauthenticated and cross-user access,
administrator initialization, single FTS batch insertion, executor security
wiring, non-blocking analysis dispatch, isolated CLI checkout selection,
database migrations, and CI/package configuration assertions where practical.

## Acceptance Criteria

- No API path can invoke host command execution or access files outside the
  configured root.
- Every persisted session and finding is visible only to its owner, except
  administrator statistics.
- The service cannot create a predictable default administrator account.
- A batch containing one finding creates exactly one FTS row.
- The executor receives the same sandbox/HITL/memory configuration as the
  planner and reviewer.
- Docker and CI use complete, consistent dependency and verification settings.
