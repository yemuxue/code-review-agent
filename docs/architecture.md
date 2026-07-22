# Architecture / 架构

## System Overview

```mermaid
graph TD
    subgraph Frontend["Frontend Layer"]
        Web["🌐 Streamlit UI<br/>:8501"]
        CLI["💻 CLI<br/>python -m src.app.cli"]
        API["🔌 FastAPI REST<br/>:8000 + Swagger"]
    end

    subgraph Orchestrator["Orchestration Layer"]
        LG["LangGraph<br/>plan → execute → review"]
        HC["Hardcoded Pipeline<br/>Planner → Executor → Reviewer"]
    end

    subgraph Agent["Agent Harness Layer"]
        EL["Execution Loop<br/>while turn &lt; max_turns"]
        SP["Streaming Parser<br/>state machine for JSON"]
        SB["Sandbox<br/>process isolation"]
        TL["Telemetry<br/>JSON Lines logger"]
    end

    subgraph Tools["Tools Layer"]
        T1["read_file"]
        T2["list_files"]
        T3["grep_pattern"]
        T4["run_command"]
        T5["clone_repo"]
        T6["get_diff"]
        T7["search_code"]
    end

    subgraph Data["Data Layer"]
        DB["SQLite WAL<br/>sessions/messages/findings/logs"]
        VS["FTS5 Search<br/>paragraph-indexed"]
    end

    subgraph Infra["Infrastructure"]
        PM["Prometheus /metrics"]
        JW["JWT Auth + Rate Limit"]
        CI["GitHub Actions CI"]
        MC["LLM Cache + Model Router"]
        HL["HITL Guard"]
    end

    Web --> LG
    Web --> HC
    CLI --> LG
    CLI --> HC
    API --> LG
    API --> HC
    LG --> EL
    HC --> EL
    EL --> SP
    EL --> SB
    EL --> TL
    EL --> T1
    EL --> T2
    EL --> T3
    EL --> T4
    EL --> T5
    EL --> T6
    EL --> T7
    EL --> DB
    EL --> VS
    API --> PM
    API --> JW
    API --> CI
    EL --> MC
    EL --> HL
```

## Data Flow

```
User Input → Router (select model)
           → Cache Check (return cached if hit)
           → Agent Harness (Execution Loop)
              → Tool Call → HITL Check → Execute → Result
           → FTS5 Index (paragraph-level)
           → SQLite Persist (sessions/messages/findings/logs)
           → Stream Response to UI
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Runtime | Python 3.9+, hand-written Execution Loop |
| Multi-Agent | LangGraph StateGraph + Hardcoded Pipeline |
| LLM | DeepSeek (Anthropic-compatible API) |
| Database | SQLite WAL + FTS5 Full-Text Search |
| API | FastAPI + Swagger + Prometheus + JWT + Rate Limit |
| UI | Streamlit (Claude Code style) |
| DevOps | Docker + GitHub Actions CI |
| Testing | pytest 12 tests, pytest-cov |
