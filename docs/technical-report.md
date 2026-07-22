# Code Review Agent — 技术报告

> 面试用技术深度文档 | 2026-07-22

---

## 一、项目概述

**Code Review Agent** 是一个从零构建的工业级多 Agent 代码分析系统。核心亮点：

- **Agent Harness 完全手写**，不依赖 LangChain/CrewAI 等任何 Agent 框架
- **Multi-Agent 协作**，Planner → Executor → Reviewer 三阶段管线 + LangGraph 图编排
- **12 项工业基础设施**，覆盖监控、认证、CI、缓存、测试、部署

---

## 二、核心原理

### 2.1 Agent 运行时架构

```
用户输入 → ModelRouter(选模型) → LLMCache(查缓存) → AgentHarness
                                                          │
                                              ┌───────────┴───────────┐
                                              │   Execution Loop       │
                                              │   while turn < max:    │
                                              │     LLM.chat()         │
                                              │     → tool_call?       │
                                              │        → HITL check    │
                                              │        → execute       │
                                              │        → result→LLM    │
                                              │     → finish           │
                                              └───────────────────────┘
```

**关键设计决策**：

1. **为什么不用 LangChain？** LangChain 的 AgentExecutor 是黑盒，定制困难。手写 Loop 可以精确控制每一轮的消息构建、工具执行、错误处理。

2. **Streaming 怎么处理 tool call？** LLM 流式返回时，tool call 的 JSON 参数是分片到达的（`{"ci` → `ty":` → `"北京"}`）。用状态机累积 → `content_block_stop` 时一次性 `json.loads()`。

3. **多 Agent 怎么协作？** 共享 LLM 客户端，通过 State 传递发现列表。Planner 输出 FINDING 行 → Executor 逐条验证 → Reviewer 去重合并。

### 2.2 LangGraph 编排

```python
workflow = StateGraph(AgentState)
workflow.add_node("plan", plan_node)          # Planner Agent
workflow.add_node("execute", execute_node)     # Executor Agent
workflow.add_node("review", review_node)       # Reviewer Agent
workflow.add_conditional_edges("plan", should_continue,
    {"execute": "execute", "end": END})        # 有发现→验证，无→结束
workflow.add_edge("execute", "review")
workflow.add_edge("review", END)
```

**对比硬编码管线的优势**：
- 宣言式定义，图结构一目了然
- 条件路由内置（无发现直接结束，省 Token）
- 天然支持 checkpoint（暂停/恢复）

### 2.3 数据库设计

```
sessions ──┬── messages (1:N)
           ├── findings (1:N)  
           └── logs (1:N)
```

SQLite WAL 模式 + FTS5 全文索引。关键设计：
- 级联删除：删 session → 自动清 messages + findings + logs
- 自动迁移：`Database()` 初始化时自动创建/升级表结构
- 中英文搜索：FTS5 MATCH + LIKE 降级双通道

---

## 三、使用指南

### 3.1 快速开始

```bash
git clone https://github.com/yemuxue/code-review-agent.git
cd code-review-agent
pip install -e .
streamlit run src/app/streamlit_app.py
```

### 3.2 三种使用方式

| 方式 | 命令 | 场景 |
|------|------|------|
| Web UI | `streamlit run src/app/streamlit_app.py` | 交互式分析 |
| CLI | `python -m src.app.cli_multi analyze ./src` | CI/脚本集成 |
| API | `curl -X POST localhost:8000/analyze -d '{...}'` | 程序对接 |

### 3.3 API 端点

```
GET  /health             健康检查
GET  /metrics            Prometheus 指标
GET  /dashboard          实时仪表盘
POST /analyze            运行分析
GET  /sessions           会话列表（分页）
GET  /findings/page      发现列表（分页+筛选）
POST /findings/search    语义搜索
GET  /stats              系统统计
```

---

## 四、技术亮点与工业界对比

### 4.1 Agent 架构

| 维度 | 本项目 | LangChain | CrewAI | AutoGPT |
|------|--------|-----------|--------|---------|
| Agent Loop | 手写，完全可控 | AgentExecutor 黑盒 | Crew.kickoff() 黑盒 | 类似手写 |
| Streaming | 状态机解析 tool call | 支持但配置复杂 | 不支持 | 不支持 |
| 沙箱 | 进程隔离 + HITL | 无 | 无 | Docker 隔离 |
| 学习成本 | 需要理解原理 | API 调用即可 | API 调用即可 | 中等 |
| 面试价值 | **极高** | 低 | 低 | 中 |

> **面试话术**："我选择手写 Agent Loop 而不是用 LangChain，因为面试官想看的是你对底层机制的理解，而不是你会调哪个 API。我可以现场画出 Execution Loop 的每一行代码。"

### 4.2 Multi-Agent

| 维度 | 本项目 | LangGraph | Microsoft AutoGen |
|------|--------|-----------|-------------------|
| 编排方式 | 硬编码 + LangGraph 双方案 | 图编排 | 对话式 |
| Agent 数量 | 3 (Planner/Executor/Reviewer) | 可配置 | 可配置 |
| 条件路由 | ✅ | ✅ | ✅ |
| Human-in-loop | ✅ 工具级审批 | ✅ 节点级 | ✅ |
| 中文支持 | ✅ 中英双语输出 | 依赖模型 | 依赖模型 |

> **面试话术**："我实现了两种 Multi-Agent 方案：硬编码管线展示了底层原理，LangGraph 方案展示了工业级图编排能力。面试时可以讨论两者的取舍。"

### 4.3 基础设施

| 维度 | 本项目 | 工业界标准 |
|------|--------|-----------|
| 数据库 | SQLite WAL | PostgreSQL |
| 搜索 | FTS5 全文索引 | Elasticsearch |
| 监控 | Prometheus + 自建仪表盘 | Prometheus + Grafana |
| 认证 | JWT Bearer + API Key | OAuth2 + RBAC |
| 限流 | slowapi 100/min | API Gateway 限流 |
| CI/CD | GitHub Actions | GitHub Actions / Jenkins |
| 部署 | 手动 + Docker Compose | Kubernetes Helm |
| 缓存 | 内存 LRU + TTL | Redis |
| 向量搜索 | FTS5（关键词） | pgvector / Milvus |

> **面试话术**："当前用的是 SQLite + FTS5，适合单机部署。我理解生产环境需要 PostgreSQL + Elasticsearch + Redis 三件套，已经在 docker-compose.yml 里配置好了，换 Linux 服务器一条命令就能切换。"

### 4.4 评估数据

10 个已知 bug 的真实检测结果：

| | 数量 |
|------|------|
| 检测到 | 6 |
| 漏报 | 4 |
| 误报 | 0 |
| Recall | 60% |
| Precision | 100% |

- **强项**：明显的代码缺陷（裸 except、竞态、缺少异常处理）
- **弱项**：语义层面的逻辑陷阱（Python falsy 陷阱、环境变量副作用）

---

## 五、常见面试问答

### Q1: 为什么不用 LangChain/CrewAI？

**答**：LangChain 的 AgentExecutor 是黑盒，出问题很难调试。CrewAI 的 API 太高层，无法展示底层理解。我选择手写 Execution Loop，面试时可以逐行解释每一步：消息怎么构建、tool call 怎么解析、结果怎么回传、错误怎么处理。这比"我用过 LangChain"有说服力得多。

### Q2: Agent 和普通 LLM 调用的区别？

**答**：普通调用是 `prompt → response` 一次性的。Agent 是多轮循环 `LLM → think → act → observe → LLM → ...`，直到任务完成或达到 max_turns。Agent 能"试错"：工具失败了 → 错误信息回传 → LLM 看到后换参数重试或换工具。

### Q3: 怎么防止 Agent 无限循环？

**答**：三层防护：① `max_turns` 硬限制；② Token 预算，超出强制结束；③ HITL Guard，危险工具需人工确认。类似微服务的 Circuit Breaker 模式。

### Q4: Streaming 模式下 tool call 怎么解析？

**答**：核心难点是 JSON 分片到达。用状态机：`content_block_start` 确定类型 → `input_json_delta` 累积字符串（不解析）→ `content_block_stop` 时 `json.loads()`。不能在中间状态解析，因为 JSON 不完整。

### Q5: 多 Agent 之间怎么协作？

**答**：通过共享 State 传递数据，非直接通信。Planner 产出 FINDING 列表 → State 传递给 Executor → Executor 产出 VERDICT → Reviewer 去重合并。这避免了 Agent 间耦合。

### Q6: 为什么用 SQLite 而不是 PostgreSQL？

**答**：SQLite 对于单机部署足够：WAL 模式支持并发读、FTS5 支持全文搜索、零配置部署。当需要多机部署时，切换到 PostgreSQL + Redis + Elasticsearch，接口层（Database 类）不变。

### Q7: 你怎么评估 Agent 的准确率？

**答**：手工标注了 33 条已知 bug，跑了真实 Agent 评估。Precision 100%（无误报），Recall 60%（部分语义层 bug 漏报）。这是真实数据，不是 mock。评估脚本在 `tests/eval_dataset.py`。

### Q8: 这个项目最大的技术难点是什么？

**答**：三个难点。① Streaming 下 tool call JSON 解析——分片到达，中间状态不可解析，需要状态机；② Multi-Agent 的 VERDICT 格式解析——LLM 输出不稳定，需要兼容多种格式；③ `deepseek-chat` 的 FINDING 产出不稳定——用更激进的 Prompt 工程解决，但根本方案是换更强的模型。

### Q9: 如果部署到生产，还需要做什么？

**答**：① 数据库切 PostgreSQL + Redis；② 加 Celery 异步任务队列处理长分析；③ 接入 OAuth2 SSO；④ K8s 部署 + HPA 自动扩缩；⑤ 加 OpenTelemetry 链路追踪。这些在 [industrial-gaps.md](industrial-gaps.md) 里都列出来了。

### Q10: 你的项目和其他 AI Code Review 工具（如 CodeRabbit、GitHub Copilot Review）有什么区别？

**答**：CodeRabbit 是 SaaS 产品，我的项目是开源的技术展示。核心差异：① 我的 Agent Harness 是手写的，展示了底层能力；② 支持 LangGraph 编排，架构更灵活；③ 有完整的工业基础设施（Prometheus、JWT、CI）；④ 代码量 3000+ 行，覆盖从底到顶的完整技术栈。
