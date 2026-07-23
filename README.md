# Code Review Agent

> **多 Agent 代码分析系统** — 从零实现的 Agent Harness + LangGraph 编排，具备生产级基础设施
>
> Multi-Agent code analysis with hand-written runtime, LangGraph orchestration, and production infrastructure.

[![CI](https://github.com/yemuxue/code-review-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/yemuxue/code-review-agent/actions)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Tests](https://img.shields.io/badge/tests-12%20passed-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

[English](#english) | [中文](#中文)

---

## 📋 目录

- [架构概览](#架构概览)
- [核心特性](#核心特性)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [使用方式](#使用方式)
- [评估数据](#评估数据)
- [技术栈](#技术栈)
- [部署](#部署)

---

## 架构概览

```
用户输入
  │
  ├─ Web UI (Streamlit :8501)  ← 🔐 JWT 登录页 → bcrypt 验密
  ├─ CLI  (python -m src.app.cli)
  └─ API  (FastAPI :8000)      ← 🔐 JWT Bearer 三层验证
        │
        ▼
  ┌─────────────────────────────────────┐
  │         Model Router                │  ← 按任务选 cheap/strong 模型
  │         LLM Cache                   │  ← 相同查询不重复调 API
  ├─────────────────────────────────────┤
  │     Multi-Agent Orchestrator        │
  │  ┌─────────────────────────────┐   │
  │  │ LangGraph:  plan → execute  │   │
  │  │              → review → END │   │
  │  │    (无发现) → END           │   │
  │  └─────────────────────────────┘   │
  ├─────────────────────────────────────┤
  │         Agent Harness               │  ← 八大组件
  │  ┌──────────┐ ┌──────────┐         │
  │  │Execution │ │Streaming │         │
  │  │  Loop    │ │ Parser   │         │
  │  ├──────────┤ ├──────────┤         │
  │  │ Sandbox  │ │Telemetry │         │
  │  ├──────────┤ ├──────────┤         │
  │  │LLM Cache │ │HITL Guard│         │
  │  ├──────────┤ ├──────────┤         │
  │  │  Context │ │  JWT     │         │
  │  │  Memory  │ │  Auth    │         │
  │  └──────────┘ └──────────┘         │
  ├─────────────────────────────────────┤
  │        Tools (7 个工具)             │
  │  read_file | list_files | grep     │
  │  run_command | clone | diff | search│
  ├─────────────────────────────────────┤
  │        Data Layer                   │
  │  ┌────────────┐ ┌────────────┐     │
  │  │SQLite WAL  │ │FTS5 Search │     │
  │  │4 tables    │ │paragraphs  │     │
  │  └────────────┘ └────────────┘     │
  └─────────────────────────────────────┘
```

[完整 Mermaid 架构图 →](docs/architecture.md)

---

## 核心特性

### Agent Harness（从零实现，不依赖 Agent 框架）

| 组件 | 功能 | 代码位置 |
|------|------|---------|
| **Execution Loop** | `while turn < max_turns: LLM → Tool → Observe` | [`harness/agent.py`](src/harness/agent.py) |
| **Streaming Parser** | 状态机解析流式 tool call JSON 片段 | [`harness/streaming.py`](src/harness/streaming.py) |
| **Sandbox** | 进程级隔离，临时目录 + 命令白名单 | [`harness/sandbox.py`](src/harness/sandbox.py) |
| **Telemetry** | JSON Lines 结构化日志 + 数据库同步 | [`harness/telemetry.py`](src/harness/telemetry.py) |
| **LLM Cache** | LRU + TTL 内存缓存，相同查询秒返 | [`harness/llm_cache.py`](src/harness/llm_cache.py) |
| **HITL Guard** | 工具调用分级：SAFE/MODERATE/DANGEROUS | [`harness/auth.py`](src/harness/auth.py) |
| **Context Memory** | 滑动窗口 / LLM 摘要 / 混合压缩三种策略 | [`harness/memory.py`](src/harness/memory.py) |
| **JWT Auth** | HS256 签名 + Access/Refresh 双 token + bcrypt | [`harness/jwt_auth.py`](src/harness/jwt_auth.py) |

### Multi-Agent System

- 🧠 **Planner** / 规划——读取代码，识别所有潜在问题
- 🔍 **Executor** / 执行——逐条验证，标 CONFIRMED/FALSE_POSITIVE
- 📝 **Reviewer** / 审核——去重合并，输出中英双语报告
- 🕸️ **LangGraph**——图编排替代硬编码，支持条件路由和循环

### 基础设施

- 📈 **Prometheus `/metrics`**——70+ HTTP 指标自动采集
- 🔐 **JWT 认证系统**——HS256 签名 + Access 15min + Refresh 7d 双 token + bcrypt 密码哈希 + jti 吊销列表
- 🛡️ **Rate Limit**——全局 100 req/min，登录接口 10/min 防暴力破解
- 🚀 **GitHub Actions CI**——lint → type-check → test → coverage
- 🗄️ **SQLite WAL + FTS5**——崩溃安全 + 中英文全文搜索
- 🔄 **自动迁移**——`Database()` 初始化时自动创建/升级表结构
- 🎨 **Claude Code 风格 UI**——暗色主题 + JWT 登录页 + 文件上传 + 会话历史
- 📊 **Eval Dataset**——33 条手工标注样本，量化 Agent 准确率

---

## 快速开始

### 环境要求

- Python 3.9+
- Git（可选）

### 安装

```bash
# 克隆项目
git clone https://github.com/yemuxue/code-review-agent.git
cd code-review-agent

# 安装（二选一）
pip install -e .                    # 生产依赖
pip install -e ".[dev]"             # 含测试工具

# 配置 API
cp .env.example .env
# 编辑 .env，填入你的 ANTHROPIC_AUTH_TOKEN
```

### 启动

```bash
# 方式 1: Streamlit Web UI（推荐）
streamlit run src/app/streamlit_app.py

# 方式 2: CLI 单 Agent
python -m src.app.cli analyze ./src

# 方式 3: CLI Multi-Agent
python -m src.app.cli_multi analyze ./src

# 方式 4: FastAPI 服务
python -m uvicorn src.api.server:app --port 8000
```

启动后访问：
- Streamlit: http://localhost:8501
- FastAPI: http://localhost:8000/docs
- Metrics: http://localhost:8000/metrics

### Docker

```bash
docker compose up -d    # 一键启动全部服务
```

---

## 项目结构

```
code-review-agent/
├── src/
│   ├── harness/              ← Agent 运行时（8 组件）
│   │   ├── agent.py              Execution Loop（同步 + 异步流式）
│   │   ├── streaming.py          Streaming Parser 状态机
│   │   ├── sandbox.py            进程隔离沙箱
│   │   ├── telemetry.py          JSON Lines 日志
│   │   ├── llm_cache.py          LRU+TTL 缓存
│   │   ├── auth.py               HITL 审批（工具风险分级）
│   │   ├── memory.py             Context Memory（3 种压缩策略）
│   │   └── jwt_auth.py           JWT 认证（签发/验证/刷新/吊销）
│   ├── multi_agent/          ← Multi-Agent 编排
│   │   ├── agents.py             Planner/Executor/Reviewer Prompt
│   │   ├── orchestrator.py       硬编码管线
│   │   └── langgraph_orchestrator.py  LangGraph 图编排
│   ├── tools/                ← Agent 工具集（7 个）
│   │   └── git_tools.py          clone/diff/read/list/search/grep/run
│   ├── memory/               ← 向量搜索
│   │   └── vector_store.py       SQLite FTS5 全文索引
│   ├── storage/              ← 持久化
│   │   ├── database.py           SQLite WAL（4 表 + 级联删除）
│   │   └── migrate.py           自动迁移脚本
│   ├── api/                  ← REST 接口
│   │   └── server.py             FastAPI + Prometheus + JWT + Rate Limit
│   ├── app/                  ← 前端 + CLI
│   │   ├── streamlit_app.py      Web UI（Claude Code 风格）
│   │   ├── cli.py                命令行单 Agent
│   │   └── cli_multi.py          命令行 Multi-Agent
│   ├── llm_client.py         ← Anthropic API 适配（含 XML 解析）
│   ├── model_router.py       ← 多模型路由
│   └── config.py              ← 配置加载
├── tests/                    ← 测试（12 项 + Eval）
│   ├── test_database.py          7 项 DB 集成测试
│   ├── test_vector_store.py      5 项 FTS5 测试
│   └── eval_dataset.py           33 条标注样本
├── docs/                     ← 文档
│   ├── architecture.md           架构图
│   └── industrial-gaps.md        企业级差距分析
├── .github/workflows/ci.yml  ← CI 流水线
├── Dockerfile                ← Docker 构建
├── docker-compose.yml        ← 三服务编排
├── pyproject.toml            ← 项目配置
├── push.bat                  ← 一键推送（测试→提交→推送）
└── README.md
```

---

## 使用方式

### Streamlit（Web UI）

```
打开 http://localhost:8501
├─ 🔐 登录页面 → 用户名/密码 → bcrypt 验证 → JWT 签发
├─ 侧栏显示用户信息 + 🚪 Logout 按钮
├─ 侧栏选择 Single / Multi-Agent 模式
├─ 📄 上传本地文件 → chip 显示
├─ 输入分析任务 → 实时流式显示结果
├─ 侧栏 History 查看/加载/删除历史会话
├─ 侧栏 Search Findings 全文搜索分析结果
└─ 侧栏 Logs 查看执行日志
```

> 默认账号：`admin` / `admin123`（首次启动自动创建）

### CLI

```bash
# 单 Agent：快速分析单个文件
python -m src.app.cli analyze X:/path/to/file.py

# Multi-Agent：深度分析整个目录
python -m src.app.cli_multi analyze X:/path/to/project
```

### API

```bash
# ─── 认证 ───
# 登录获取 token
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 返回: { "access_token": "eyJ...", "refresh_token": "eyJ...", "user": {...} }

# 刷新 token
curl -X POST http://localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"eyJ..."}'

# 当前用户信息
curl http://localhost:8000/auth/me \
  -H "Authorization: Bearer $TOKEN"

# 登出（吊销 token）
curl -X POST http://localhost:8000/auth/logout \
  -H "Authorization: Bearer $TOKEN"

# ─── 业务 API ───
# 健康检查（无需认证）
curl http://localhost:8000/health

# 运行分析（需认证）
curl -X POST http://localhost:8000/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "Find bugs in harness/", "mode": "single", "project_path": "./src/harness"}'

# 查看所有会话
curl http://localhost:8000/sessions \
  -H "Authorization: Bearer $TOKEN"

# 搜索发现
curl "http://localhost:8000/findings/keyword?q=injection" \
  -H "Authorization: Bearer $TOKEN"

# 向量搜索
curl -X POST http://localhost:8000/findings/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "SQL injection", "n_results": 5}'
```

---

## 评估数据

### Agent 真实检测能力（10 bugs × 6 files）

| 文件 | 行 | Bug | 发现 |
|------|-----|-----|------|
| sandbox.py | 34 | `except: pass` 裸异常吞 KeyboardInterrupt | ✅ |
| sandbox.py | 42 | `env or os.environ.copy()` 空 dict 当 falsy | ❌ |
| sandbox.py | 49 | 线程竞态条件 | ✅ |
| agent.py | 39 | `model.chat()` 无异常处理 | ✅ |
| agent.py | 84 | `ToolCall(**tc)` 格式错误崩溃 | ✅ |
| streaming.py | 72 | JSON 解析失败丢失部分数据 | ✅ |
| telemetry.py | 132 | `TimedToolCall` 重复记录 start 事件 | ❌ |
| config.py | 16 | 模块级 `for` 循环修改 `os.environ` | ❌ |
| llm_client.py | 19 | `b[type]` 直接索引遇未知块崩溃 | ❌ |
| git_tools.py | 7 | 源码硬编码代理地址 | ✅ |

**Recall: 6/10 = 60% | Precision: 100%（无误报）**

> Agent 强项：明显的代码缺陷（裸 except、竞态、缺少异常处理）  
> Agent 弱项：语义层面的问题（逻辑陷阱、环境变量副作用），需要更强的模型

### Eval 数据集

`tests/eval_dataset.py` 包含 33 条手工标注样本：

| 类别 | 数量 | 
|------|------|
| 🐛 BUG | 22 |
| 🔒 SECURITY | 5 |
| ⚡ PERFORMANCE | 5 |
| 📝 STYLE | 1 |

运行完整评估：`python tests/eval_dataset.py`

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.9+ |
| Agent 运行时 | 手写 Execution Loop（零框架依赖） |
| Multi-Agent | LangGraph StateGraph |
| LLM | DeepSeek（Anthropic 兼容 API） |
| 数据库 | SQLite WAL + FTS5 全文搜索 |
| API | FastAPI + Swagger 文档 |
| 监控 | Prometheus `/metrics` |
| 认证 | python-jose HS256 JWT + bcrypt 密码哈希 + jti 吊销列表 |
| 限流 | slowapi（全局 100/min，登录 10/min） |
| 前端 | Streamlit（Claude Code 暗色主题 + JWT 登录页） |
| CI | GitHub Actions（lint/type/test/coverage） |
| 部署 | Docker Compose |

---

## 部署

### 本地开发

```bash
pip install -e ".[dev]"
pytest tests/ -v
streamlit run src/app/streamlit_app.py
```

### 生产服务器

```bash
# 直接运行
nohup python3 -m uvicorn src.api.server:app --host 0.0.0.0 --port 8000 &
nohup python3 -m streamlit run src/app/streamlit_app.py --server.port 8501 --server.address 0.0.0.0 &

# 或 Docker（推荐）
docker compose up -d
```

### 当前部署

| 服务 | 地址 | 状态 |
|------|------|------|
| Streamlit | `http://10.112.216.82:8501` | ✅ |
| FastAPI | `http://10.112.216.82:8000` | ✅ |

---

## License

MIT
