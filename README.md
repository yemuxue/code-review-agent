# Code Review Agent

> **Production-grade Multi-Agent code analysis system with LangGraph orchestration**
> 
> 生产级多 Agent 代码分析系统 — 从零实现的 Agent Harness + LangGraph 编排

[![CI](https://github.com/yemuxue/code-review-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/yemuxue/code-review-agent/actions)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Tests](https://img.shields.io/badge/tests-12%20passed-green)
![Eval F1](https://img.shields.io/badge/eval--f1-0.91-brightgreen)

---

## Architecture

```
User → [Router: cheap/strong model] → [Cache] → Agent Harness
                                                    ├─ Execution Loop
                                                    ├─ Streaming Parser (state machine)
                                                    ├─ Human-in-the-Loop Guard
                                                    └─ Tools (7 tools)
                                                          ↓
                                               LangGraph Orchestrator
                                               plan → execute → review
                                                          ↓
                                               SQLite + FTS5 Search
```

[Full architecture diagram →](docs/architecture.md)

---

## Quick Start

```bash
# 1. One-click deploy (requires Docker)
docker compose up -d

# 2. Or run directly
pip install -e .
python -m src.app.cli analyze ./src

# 3. Or use Streamlit UI
streamlit run src/app/streamlit_app.py

# 4. Or call the API
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"query":"Find bugs in harness/","mode":"single"}'
```

---

## Features

### Agent Harness (Built from Scratch)
- 🔄 **Execution Loop** — `while turn < max_turns: LLM → Tool → Observe`
- 📡 **Streaming Parser** — state machine for real-time tool call JSON parsing
- 🛡️ **Sandbox** — process-level isolation for tool execution
- 📊 **Telemetry** — JSON Lines structured logging with DB sync
- 🧠 **LLM Cache** — LRU + TTL cache, same queries don't call API
- 🚦 **HITL Guard** — SAFE auto-approve, DANGEROUS requires confirmation

### Multi-Agent System
- 🧠 **Planner** — reads code, identifies all potential issues
- 🔍 **Executor** — verifies each finding with evidence
- 📝 **Reviewer** — deduplicates, produces bilingual (EN+CN) report
- 🕸️ **LangGraph** — graph-based orchestration (plan→execute→review)

### Production Infrastructure
- 📈 **Prometheus** `/metrics` — 70+ HTTP metrics
- 🔐 **JWT Auth + Rate Limit** — 100 req/min per IP
- 🚀 **GitHub Actions CI** — lint → type-check → test → coverage
- 🗄️ **SQLite WAL + FTS5** — crash-safe, EN+CN full-text search
- 🔄 **Auto-Migration** — schema evolves without manual intervention
- 🐳 **Docker** — one-command deploy with auto-restart

---

## Evaluation

| Metric | Score |
|--------|-------|
| Precision | **100.0%** |
| Recall | **83.3%** |
| F1 Score | **0.91** |
| Dataset | 33 samples (30 real bugs + 3 false positives) |
| Categories | BUG(22), SECURITY(5), PERF(5), STYLE(1) |

---

## Project Structure

```
src/
├── harness/          Agent Runtime (5 components)
│   ├── agent.py         Execution Loop
│   ├── streaming.py     Streaming Parser (state machine)
│   ├── sandbox.py       Process isolation
│   ├── telemetry.py     Structured logging
│   ├── llm_cache.py     LRU+TTL cache
│   └── auth.py          HITL guard
├── multi_agent/      Multi-Agent Orchestration
│   ├── orchestrator.py       Hardcoded pipeline
│   ├── langgraph_orchestrator.py  LangGraph graph
│   └── agents.py             System prompts
├── tools/            Agent Tools (7)
├── memory/           FTS5 Vector Search
├── storage/          SQLite Persistence + Migration
├── api/              FastAPI REST Server
├── app/              Streamlit UI + CLI
├── llm_client.py     Anthropic API adapter
├── model_router.py   Auto-select cheap/strong model
└── config.py         Environment config
```

---

## Commands

```bash
# Web UI
streamlit run src/app/streamlit_app.py

# CLI - single agent
python -m src.app.cli review <PR_URL>

# CLI - multi agent
python -m src.app.cli_multi analyze <PROJECT_PATH>

# API
uvicorn src.api.server:app --port 8000

# Tests
pytest tests/ -v --cov=src

# Database migration
python src/storage/migrate.py

# Docker
docker compose up -d
```

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Runtime | Python 3.9+ |
| Agent Framework | Hand-written + LangGraph |
| LLM | DeepSeek (Anthropic API) |
| Database | SQLite WAL + FTS5 |
| API | FastAPI + Prometheus |
| UI | Streamlit |
| CI | GitHub Actions |
| Deploy | Docker Compose |

---

## License

MIT
