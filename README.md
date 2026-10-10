# Code Review Agent

> **多 Agent 代码分析系统** — 从零实现的 Agent Harness + LangGraph 编排，包含认证、隔离、审计与评测等工程化能力
>
> Multi-Agent code analysis with a hand-written runtime, LangGraph orchestration, and security, audit, and evaluation capabilities.

[![CI](https://github.com/yemuxue/code-review-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/yemuxue/code-review-agent/actions)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Tests](https://img.shields.io/badge/tests-363%20collected-blue)
![Lint](https://img.shields.io/badge/ruff-clean-green)
![Types](https://img.shields.io/badge/mypy-src%20clean-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

[English](#english) | [中文](#中文)

---

## 📋 目录

- [架构概览](#架构概览)
- [核心特性](#核心特性)
- [Agent Skills](#agent-skills)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [使用方式](#使用方式)
- [评估数据](#评估数据)
- [产品经理作品集材料](#产品经理作品集材料)
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
  │  │   (并行 Send) → review →   │   │
  │  │   fix (分组串行) → verify   │   │
  │  │    (无发现) → END           │   │
  │  └─────────────────────────────┘   │
  │     Agent Skills                    │  ← 按角色和任务关键词注入
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
  │        Tools (8 个工具)             │
  │ read | list | grep | search        │
  │ run | clone | diff | fetch_pr     │
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
- 🔍 **Executor** / 执行——Send API 并行逐条验证，标 CONFIRMED/FALSE_POSITIVE
- 📝 **Reviewer** / 审核——去重合并，输出中英双语报告
- 🔧 **Fixer** / 修复——按文件分组串行，write_file 自动修复（写前备份 .bak）
- ✅ **Verify** / 审核——修复后语法检查，防止 Agent 修坏代码
- 🕸️ **LangGraph**——图编排替代硬编码，支持条件路由、Send 并行和循环

### Agent Skills

Multi-Agent 请求会从仓库根目录的 `skills/*/SKILL.md` 加载只读技能包。每个技能用
frontmatter 声明适用 `roles` 和 `triggers`；运行时仅把命中任务关键词且适用于当前角色的
技能正文追加到对应 Agent 的 system prompt。技能不会改变既有 `FINDING`、`VERDICT` 等机器
可读输出格式。

- 默认目录：`<仓库根>/skills`
- 覆盖目录：CLI 使用 `--skills-dir DIR`，或为 CLI、API、Streamlit 设置 `SKILLS_DIR`
- 当前示例：`security-review-rules`（安全审查）与 `fix-encoding-safety`（修复阶段编码安全）
- 详细格式、选择规则和新增技能步骤见 [skills 使用说明](docs/skills.md)

### 基础设施

- 📈 **Prometheus `/metrics`**——70+ HTTP 指标自动采集（需 JWT 访问）
- 🔐 **JWT 认证系统**——HS256 签名 + Access 15min + Refresh 7d 双 token + bcrypt 密码哈希 + jti 吊销列表
- 🛡️ **Rate Limit**——`SlowAPIMiddleware` 全局 100 req/min（按用户名分桶，回退 IP），登录接口 10/min 防暴力破解
- 👤 **用户数据隔离**——会话、消息、日志、finding 与向量索引全部按 `owner_username` 过滤；
  历史无归属数据 fail-closed（不授权任何登录用户）
- 🔒 **命令执行 fail-closed**——未配置沙箱时 `run_command` 直接拒绝，绝不以宿主权限执行；
  子进程环境走白名单，不透传 API key / JWT 密钥
- ✍️ **默认仅审查**——写操作需要显式开启自动修复；无审批人时 HITL 一律拒绝写操作
- 🚀 **GitHub Actions CI**——ruff → 全量 mypy → pytest+coverage 地板 → 容器配置校验 → 离线评测门禁
- 📊 **评测闭环**——指标目标值 + **基线回归比对**（指标变差/失败增加/采集退化都判回归），
  详见 [指标可达性与数据管道](docs/product-manager/08_指标可达性与数据管道.md)
- 🩺 **降级可识别**——模型故障 / turn 预算耗尽 / 空回答不再伪装成成功：
  响应体带 `degraded` + `error_code`，遥测记录降级原因，并作为"降级率"进入 CI 门禁
- 🧾 **审计闭环**——请求 ID 贯穿日志/遥测/数据库；登录成败、分析开始/完成/降级、
  越权访问全部落审计（`actor` / `request_id` / `file_path` / `outcome`），
  可回答"谁在什么时候对哪个项目做了什么"；错误响应带稳定 `error_code`
- 📋 **标注集门禁**——两份标注集有 `dataset_id` + `version`，`preflight()` 校验
  文件/行号/枚举/计数一致性与 basename 歧义；跳过与判分异常显式记账
  （`healthy=False` 时 `--gate` 直接失败），杜绝"静默跳过 16/124"
- ⏱️ **异步分析任务**——`POST /analyze/async` 返回 202 + `job_id`，
  `GET /jobs/{id}` 查进度/结果、`DELETE /jobs/{id}` 取消；并发闸门
  （`ANALYZE_MAX_CONCURRENT`）、单任务超时（`ANALYZE_TIMEOUT_SECONDS`）、
  `Idempotency-Key` 幂等去重；同步端点保留兼容
- 📦 **交付面合规**——`.dockerignore` 排除密钥与运行数据；镜像多阶段构建 + 非 root + HEALTHCHECK
- 🗄️ **SQLite WAL + FTS5**——崩溃安全 + 中英文全文搜索
- 🔄 **自动迁移**——`Database()` 初始化时自动创建/升级表结构（幂等）
- 🎨 **Claude Code 风格 UI**——暗色主题 + JWT 登录页 + 文件上传 + 会话历史
- 📊 **Eval Dataset**——124 条手工标注样本（含自评估 + 第三方项目评估）

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
# 编辑 .env，至少填入 ANTHROPIC_AUTH_TOKEN、JWT_SECRET_KEY 和 ADMIN_PASSWORD
```

> ⚠️ **没有默认口令。** 首次启动会以 `.env` 中的 `ADMIN_PASSWORD` 创建 `admin`
> 账号；未设置该变量时启动直接失败（fail-fast），而不是退回到某个弱密码。
> 仓库任何位置都不会展示可用口令，请用足够随机的值，并妥善保存。

### 启动

```bash
# Windows：同时启动 FastAPI 和 Streamlit（推荐，脚本会配置本地代理）
start_services.bat

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

`start_services.bat` 默认使用 `C:\tools\anaconda3\envs\pytorch\python.exe`，不可用时回退到
`python`；若未继承代理环境变量，会使用 `http://127.0.0.1:7897`。两个服务独立运行，启动日志
写入 `logs/fastapi-service.*.log` 和 `logs/streamlit-service.*.log`。如本机未运行该代理，请在
启动前设置正确的 `HTTP_PROXY`、`HTTPS_PROXY` 和 `ALL_PROXY`。

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
│   │   ├── factory.py            三入口共用的编排工厂
│   │   ├── finding_parser.py     FINDING 行解析（各入口唯一实现）
│   │   ├── orchestrator.py       只读兼容管线（未迁移的外部调用）
│   │   └── langgraph_orchestrator.py  LangGraph 图编排
│   ├── tools/                ← Agent 工具集
│   │   └── git_tools.py          clone/diff/read/list/search/grep/run/write
│   ├── memory/               ← 全文检索
│   │   └── vector_store.py       SQLite FTS5 索引（owner-aware）
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
├── skills/                    ← 可版本控制的 Agent Skill 包
│   └── <skill-name>/SKILL.md  ← frontmatter + 只读指令正文
├── src/skills/                ← Skill 加载、筛选和 prompt 拼装
├── tests/                    ← 363 项测试已收集（执行结果以 CI 为准）+ Eval
│   ├── test_owner_isolation.py       所有者隔离（DAO + 向量库）
│   ├── test_api_user_isolation.py    API 端点级跨用户访问
│   ├── test_hitl_guard.py            HITL 风险分级与 fail-closed 审批
│   ├── test_fix_safety.py            写入截断守卫/备份回滚/完整性校验
│   ├── test_agent_harness.py         Harness 循环与沙箱 fail-closed
│   ├── test_bug_injection_baseline.py 漏洞基线样本不被流程改写
│   ├── fixtures/                     期望发现清单（评测用 fixture）
│   ├── eval_dataset.py               33 条标注样本（本项目自评）
│   └── eval_llm_agent_qa.py          124 条标注样本（第三方项目评估）
├── docs/                     ← 文档
│   ├── architecture.md           架构图
│   ├── current-remediation-plan.md 整改清单（含完成状态）
│   └── industrial-gaps.md        企业级差距分析
├── .github/workflows/ci.yml  ← CI 流水线（lint + 全量 mypy + 测试 + 离线评测门禁）
├── Dockerfile                ← Docker 构建
├── docker-compose.yml        ← FastAPI + Streamlit 编排
├── pyproject.toml            ← 项目配置（依赖与 ruff/mypy/pytest 规则的唯一来源）
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

> 首次启动会以 `.env` 的 `ADMIN_PASSWORD` 创建 `admin` 账号；请勿依赖或提交固定默认密码。

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
# 登录获取 token（用户名 admin，密码为 .env 里 ADMIN_PASSWORD 的值）
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"$ADMIN_PASSWORD"}'

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
# 健康检查（无需认证；只返回存活性，不返回任何业务统计）
curl http://localhost:8000/health

# Prometheus 指标（需认证：匿名暴露会泄露请求量与错误率）
curl http://localhost:8000/metrics \
  -H "Authorization: Bearer $TOKEN"

# 运行分析（需认证，同步；分钟级任务建议用下面的异步接口）
curl -X POST http://localhost:8000/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "Find bugs in harness/", "mode": "single", "project_path": "./src/harness"}'

# ─── 异步分析（推荐用于长任务）───
# 提交后立即返回 202 + job_id；Idempotency-Key 可防止重复提交消耗 token
curl -X POST http://localhost:8000/analyze/async \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: pr-1234-review" \
  -H "Content-Type: application/json" \
  -d '{"query": "Find bugs in harness/", "mode": "multi", "project_path": "./src/harness"}'

# 轮询任务状态（terminal=true 时 result 才有内容）
curl http://localhost:8000/jobs/$JOB_ID -H "Authorization: Bearer $TOKEN"

# 取消任务
curl -X DELETE http://localhost:8000/jobs/$JOB_ID -H "Authorization: Bearer $TOKEN"

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

### Eval 数据集 — llm-agent-qa-system 项目评估（124 条标注样本）

对第三方项目 [llm-agent-qa-system](https://github.com/yemuxue/llm-agent-qa-system) 进行完整代码审查，标注 124 条样本覆盖 23 个源文件：

| 模块 | 文件 | 样本数 | 主要问题 |
|------|------|--------|---------|
| Agent 核心 | `react_agent.py` | 12 | 格式解析失败暴露原文、流式非真正流式、max_iterations 无异常处理 |
| Prompt | `prompt.py` | 6 | XML 格式严苛容错差、死代码、字符串 += 拼接 |
| 工具系统 | `tools/base.py` | 5 | 类级别可变默认参数共享、execute 无 schema 校验 |
| | `tools/calculator.py` | 5 | eval() DoS 攻击面、安全正则未锚定、inf/NaN 未处理 |
| | `tools/web_search.py` | 5 | 双重 import 失败无帮助、连接泄漏、LLM 无法控制结果数 |
| | `tools/knowledge_base.py` | 4 | retrieve 空结果仍调 reranker、相似度分数尺度不一致 |
| LLM 适配 | `llm/base.py` | 5 | chat 不支持 tools 参数、懒初始化非线程安全、流式未检查 delta |
| | `llm/deepseek_adapter.py` | 3 | Mixin MRO 问题、provider_name 不含模型版本 |
| | `llm/qwen_adapter.py` | 2 | 与 DeepSeek 相同 MRO 问题、provider_name 含中文 |
| 记忆系统 | `memory/sliding_window.py` | 5 | **deque maxlen 假设 2 msg/turn 但 observation 挤占窗口** |
| RAG 流水线 | `rag/vector_store.py` | 7 | **`hash()` 跨进程不稳定导致重复文档**、裸 except、距离公式局限 |
| | `rag/retriever.py` | 4 | 空查询无感知、double processing |
| | `rag/reranker.py` | 4 | 三次浮点转换浪费、max_length=512 静默截断 |
| | `rag/embedding.py` | 4 | 首次加载阻塞无进度、batch_size 硬编码、空字符串无验证 |
| | `rag/document_loader.py` | 6 | UTF-8 硬编码、声称支持 PDF 未实现、HTML 正则贪婪 |
| | `rag/text_splitter.py` | 5 | 滑动窗口从字符切分非语义边界、中文分句遗漏顿号冒号 |
| 配置 | `config/settings.py` | 5 | 模块级单例阻碍测试、int() 包裹环境变量非数字崩溃、load_dotenv 副作用 |
| 前端 | `app/streamlit_app.py` | 7 | Agent 就绪消息重复显示、fallback 消息误追加、tools_rendered 无限增长 |
| | `app/chat_ui.py` | 3 | 截断在 CJK 中间切断、死代码 |
| | `app/sidebar.py` | 4 | 直接修改全局 settings 单例、相对路径依赖 CWD |
| 工具 | `utils/helpers.py` | 5 | JSON 提取正则贪婪跨块、嵌套标签无处理、token 估算偏低 |
| | `utils/logger.py` | 2 | 模块级 dict 非线程安全、FileHandler 静默失败 |

**数据集构成**：108 真实问题（BUG 75 / STYLE 20 / PERF 12 / SECURITY 1）+ 16 假问题（FP 检测）

**真实评估结果**（逐样本 LLM 判断，全量 124 条；不同运行批次不宜直接作为严格模型排名）：

| 模型 | 运行批次 | Precision | Recall | F1 | TP/FP/FN/TN |
|------|----------|-----------|--------|-----|-------------|
| **gpt-6-sol** | **2026-10-07，全量** | **94.0%** | **43.5%** | **0.59** | **47/3/61/13** |
| deepseek-v4-flash | 历史全量 | 98.0% | 44.4% | 0.61 | 48/1/60/15 |

> 评估方法：对每条标注读取目标行 ±15 行上下文，由 LLM 独立判断是否存在 bug；上述两轮均完成 124/124 条判分，跳过 0、异常 0。
> 当前最新的 `gpt-6-sol` 结果误报较少（FP=3，Precision=94.0%），但漏报较多（FN=61，Recall=43.5%）。
> 该结果是逐样本判分基线，不是完整 Agent 工作流的端到端质量结论；高风险代码仍需人工复核，并持续提升召回率。

运行评估：

```bash
python tests/eval_llm_agent_qa.py     # 124 条标注样本评估
python -m tests.eval.runner --suite offline --repeat 2 --gate   # 离线评测门禁（CI 同款）
```

> `tests/eval_dataset.py` 是数据集模块（只定义样本与评分函数，没有
> `__main__` 入口），不作为命令行脚本运行；它由上面的离线评测与
> `tests/test_eval_metrics.py` 消费。

---

## 产品经理作品集材料

如果你希望从产品经理视角了解本项目，参阅 [AI 代码审查 Agent 产品经理作品集](docs/product-manager/README.md)。其中包含产品定位与需求分析、用户调研报告、竞品分析、MVP PRD、指标与实验方案，以及项目决策记录。文档明确区分了项目已有事实和仍需通过真实用户验证的产品假设。

项目交互产物： [可点击原型源码](可信代码审查Agent/原型/可信代码审查Agent-prototype.html) · [可用性测试报告](可信代码审查Agent/需求挖掘/可信代码审查Agent-可用性测试报告.html) · [原型截图](可信代码审查Agent/原型截图/)

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

### 部署状态

本项目提供本地运行和 Docker Compose 部署方式；README 不再固定记录局域网 IP 或在线状态，实际可访问地址以部署环境为准。当前仓库未声明稳定的公开演示服务。

---

## License

MIT
