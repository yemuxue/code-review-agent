# Code Review Agent — 企业级差异分析与改进计划

> 2026-07-21 | 从原型到生产

---

## 一、数据库与存储

| 当前 | 生产级 | 差距 |
|------|--------|------|
| SQLite 单文件 | PostgreSQL 集群 | 并发、高可用、备份恢复 |
| FTS5 关键词匹配 | Elasticsearch | 分词、同义词、高亮、相关度排序 |
| 无向量 | pgvector / Milvus / ChromaDB | 真正的语义向量检索 |
| 表结构简单，无迁移 | Alembic 迁移 | 版本化管理 Schema |

## 二、API 与后端

> 下表"当前"列是 **2026-07-21 原型期** 的状态快照，保留用于展示演进过程。
> 认证、限流、指标、异步化等项的**最新状态见文末"改进进度"**，两处结论不再互相矛盾。

| 当前（原型期） | 生产级 | 差距 |
|------|--------|------|
| FastAPI 单进程 | FastAPI + Gunicorn 多 worker | 并发处理能力 |
| 无认证 | JWT / API Key / OAuth2 | 任意人可调 API |
| 无速率限制 | Rate Limiter | 被刷爆无防护 |
| 同步阻塞 Agent | 异步任务队列（Celery + Redis） | 长任务阻塞其他请求 |

## 三、前端

| 当前 | 生产级 | 差距 |
|------|--------|------|
| Streamlit（原型级） | React / Next.js | 性能、灵活度、组件生态 |
| 无状态管理 | Redux / Zustand | 复杂交互难维护 |
| 无移动端 | PWA / 独立 App | 访问渠道 |

## 四、Agent 架构

| 当前 | 生产级 | 差距 |
|------|--------|------|
| 硬编码 3 Agent | 可编排 Agent 图（LangGraph） | 复杂流程难修改 |
| 无 Agent 间消息队列 | Event-driven Agent Bus | Agent 间耦合 |
| 单 LLM 提供商 | 多模型路由 | 成本、可用性 |
| 无缓存 | LLM 响应缓存（Redis） | 重复查询浪费 Token |
| 无 Human-in-the-loop | 审批工作流 | 危险操作无人工确认 |

## 五、安全

| 当前 | 生产级 | 差距 |
|------|--------|------|
| API key 在 .env 明文 | Vault / AWS Secrets Manager | 密钥管理 |
| 无审计日志 | 不可篡改审计日志 | 安全合规 |
| 沙箱仅限进程隔离 | 容器级隔离（gVisor） | 代码执行安全 |

## 六、测试

| 当前 | 生产级 | 差距 |
|------|--------|------|
| 6 个单元测试 | 几百个覆盖多维度 | 覆盖率 |
| 无集成测试 | CI 自动跑集成测试 | 回归保护 |
| 无 LLM eval | BLEU / ROUGE / Human eval | Agent 效果评估 |
| 无性能测试 | 压测（Locust / k6） | 容量规划 |

## 七、DevOps

| 当前 | 生产级 | 差距 |
|------|--------|------|
| 手动启动 .bat 脚本 | systemd / supervisor / K8s | 进程守护 |
| 无 CI/CD | GitHub Actions / GitLab CI | 自动构建测试部署 |
| 环境不一致 | Docker / K8s 标准化 | 可复现性 |

## 八、监控与可观测性

| 当前 | 生产级 | 差距 |
|------|--------|------|
| JSONL 文件日志 | ELK / Loki + Grafana | 日志聚合检索 |
| 无 metrics | Prometheus + Grafana | QPS、延迟、错误率 |
| 无 tracing | OpenTelemetry / Jaeger | 链路追踪 |
| 无告警 | AlertManager / PagerDuty | 故障通知 |

## 九、性能与扩展

| 当前 | 生产级 | 差距 |
|------|--------|------|
| 单机单进程 | 水平扩展（K8s HPA） | 容量瓶颈 |
| 同步 LLM 调用 | 流式 + 批处理 | 延迟 |
| 无分页 | 游标分页 | 大数据集 |

## 十、代码工程

| 当前 | 生产级 | 差距 |
|------|--------|------|
| 裸 Python 文件 | 模块化 monorepo | 可维护性 |
| 无类型检查 | mypy strict mode | 类型安全 |
| 无 CHANGELOG | Conventional Commits | 版本管理 |

---

## 优先改进计划

| # | 改进 | 工作量 | 展示价值 |
|---|------|--------|---------|
| 1 | Prometheus metrics + Grafana | 30 行代码 | 极高 |
| 2 | JWT 认证 + Rate Limit | 50 行 | 极高 |
| 3 | GitHub Actions CI | 1 个 yml | 高 |
| 4 | Alembic migration | 1 个文件 | 中 |
| 5 | pytest 覆盖率 80%+ | 1 天 | 高 |
| 6 | LangGraph 替换硬编码 Agent | 1 天 | 极高 |

---

## 改进进度

- [x] 1. Prometheus metrics — `/metrics`（需 JWT 访问）+ `Instrumentator`
- [x] 2. JWT 认证 + Rate Limit — `slowapi` 限流（全局 100/min，登录 10/min）+ `HTTPBearer` 认证
- [x] 3. GitHub Actions CI — `.github/workflows/ci.yml`（ruff → **全量 mypy** → pytest+coverage → 离线评测门禁）
- [x] 4. Migration 脚本 — `src/storage/migrate.py`（替代 Alembic，避免 C++ 依赖）
- [x] 5. pytest 通过 + pytest-cov 集成 — 当前 **199 passed / 1 skipped**，
      覆盖率 52%（`streamlit_app.py` 为入口脚本、`cli*.py` 未被单测覆盖，是主要缺口）
- [x] 6. LangGraph — `langgraph_orchestrator.py`（三个入口统一经 `factory.create_langgraph_orchestrator`）
- [x] 7. 用户数据隔离 — 会话/消息/日志/finding/向量索引按 `owner_username` 过滤，历史空归属 fail-closed
- [x] 8. 命令执行 fail-closed — 无沙箱即拒绝 `run_command`；子进程环境白名单，不透传宿主密钥
- [x] 9. HITL fail-closed — 无审批人时拒绝写操作，默认仅审查
- [x] 10. 全量类型检查 — `mypy src/` 0 error（42 个源文件），规则固化在 `pyproject.toml`

### 仍未解决（下一轮）

- [ ] 覆盖率提升到 80%：需要可测试化 `streamlit_app.py`（把会话/持久化逻辑抽成不依赖 Streamlit 的模块）
- [ ] 容器级隔离成为生产默认：`DockerSandbox` 已实现但未被任何入口实例化
- [ ] 异步任务队列：长分析仍会占用请求线程
- [ ] `run_command` 白名单与审计（当前仅靠"无沙箱即拒绝"兜底）
