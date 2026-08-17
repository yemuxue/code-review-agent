# 当前项目整改清单

> 审计日期：2026-08-14  
> 范围：代码审查、已执行的真实 LangGraph 修复流程、当前工作区状态。  
> 说明：本清单只包含已验证的问题；未把尚未验证的生产化设想列为缺陷。

## 整改原则

- 新增或修改的复杂逻辑必须使用简洁中文注释，说明设计原因、边界或风险；不为显然代码逐行注释。
- 新增和更新文档使用 UTF-8 编码，中文为主。API 标识符、命令、许可证、第三方错误保持原文并用中文解释。
- 修复必须有对应测试。涉及写入、权限、用户隔离或命令执行的变更，必须覆盖失败路径。
- 未经明确批准，不提交或覆盖用户已有的未提交文件。

## 本轮已完成（2026-08-17）

- **修复落盘证据**：LangGraph 只认可当前 `run()` 产生的写入 receipt（目标路径、写前/写后 SHA-256、备份路径、时间）。历史 `.bak` 不再能把未写入的结果误判为成功。
- **状态与行为验证**：修复先标为 `APPLIED`，只有项目根目录内、受限 `pytest` 命令通过后才标为 `VERIFIED`。没有目标测试、命令被拒绝或测试失败时均不会升级状态。
- **入口统一**：CLI、API、Streamlit 的多代理调用都经由 `create_langgraph_orchestrator` 创建同一个 LangGraph 编排器。CLI 新增默认关闭的 `--auto-fix`；API 维持只读工具集与默认仅审查。
- **回归测试**：新增 receipt 假阳性、行为验证、命令白名单、入口统一与报告完整性的测试，不触发真实模型网络请求。

## P0：立即整改

### 1. 三个入口使用不同的执行编排

**状态：已完成（2026-08-17）**。三个正式入口已统一到 LangGraph 工厂；旧 `MultiAgentOrchestrator` 仅保留为未迁移外部调用的兼容实现。

**现状**：Streamlit 使用包含 `plan -> execute_one -> review -> fix_one -> verify_fix` 的 `LangGraphOrchestrator`；CLI 和 API 仍使用只含 Planner、Executor、Reviewer 的旧 `MultiAgentOrchestrator`。

**风险**：同一请求从不同入口进入会得到不同能力与安全行为，CLI/API 不会真正走 Fixer 和修复验证。

**整改**：抽取统一的审查服务接口，让 CLI、API、前端共享同一编排；保留旧编排前必须明确标注只读兼容模式。增加三入口一致性集成测试。

**涉及文件**：`src/app/cli_multi.py`、`src/api/server.py`、`src/app/streamlit_app.py`、`src/multi_agent/langgraph_orchestrator.py`。

### 2. `write_file` 缺少项目根目录限制

**现状**：写入工具接受任意绝对路径。

**风险**：模型或恶意输入可能修改项目范围外的文件。

**整改**：在任务创建时保存并校验允许写入的项目根目录；对目标路径执行规范化并拒绝根目录外、符号链接逃逸和不在已批准文件集合内的写入。写入前展示 diff。

**涉及文件**：`src/tools/git_tools.py`、`src/harness/agent.py`、`src/multi_agent/langgraph_orchestrator.py`。

### 3. 自动批准会导致“只审查”也修改文件

**现状**：前端创建 `HumanInTheLoop(auto_approve_safe=True)`，`write_file` 被划为 MODERATE，默认可能自动批准。

**风险**：用户仅请求代码审查时，流程仍可能写文件。

**整改**：引入明确的 `review-only` 与 `auto-fix` 模式；默认只审查。自动修复应在任务开始时获得一次明确授权，并在每次实际写入前记录 diff、目标路径和审计事件。

**涉及文件**：`src/harness/auth.py`、`src/app/streamlit_app.py`、`src/multi_agent/agents.py`。

### 4. Streamlit 绕过用户所有者隔离

**现状**：API 为会话和 finding 传递 `owner_username`；Streamlit 读取会话和消息、创建会话、写入 findings 时未完整传递该字段。向量索引也没有所有者字段。

**风险**：不同用户可能看到、检索到或污染彼此数据。

**整改**：把当前登录用户贯穿 Session、Message、Finding 与向量索引的创建、读取、搜索和删除；为历史空所有者数据制定迁移和访问策略。

**涉及文件**：`src/app/streamlit_app.py`、`src/storage/database.py`、`src/memory/vector_store.py`、`src/api/server.py`。

### 5. 修复落盘验证可能产生假阳性

**状态：已完成（2026-08-17）**。验证节点只检查本次运行的 receipt 与当前文件哈希，不再以 `.bak` 是否存在判断写入成功。

**现状**：验证节点以 `.bak` 文件存在作为 `write_file` 本次成功的证据。旧备份也会满足条件。

**风险**：流程会将未实际写入的 finding 标为 `FIXED`。

**整改**：写入工具返回本次操作 ID、原内容哈希、新内容哈希、备份路径和写入时间；验证节点只接受与本次操作匹配的证据。明确备份版本化或覆盖策略。

**涉及文件**：`src/tools/git_tools.py`、`src/multi_agent/langgraph_orchestrator.py`。

### 6. 修复验证不足以证明业务正确性

**状态：已完成（2026-08-17）**。状态已区分 `APPLIED` 与 `VERIFIED`；只有受控的目标 `pytest` 测试通过才会标记为 `VERIFIED`。

**现状**：`verify_fix` 主要检查语法和文件完整性。

**风险**：例如把 `eval` 替换为 `ast.literal_eval` 虽更安全，但会改变对算术表达式的支持范围；仅能编译不能证明需求仍成立。

**整改**：区分 `APPLIED` 和 `VERIFIED`；Fixer 必须生成或选择目标行为测试，并在受控环境执行。测试不能运行时，状态保持 `APPLIED`，不能标为 `VERIFIED`。

**涉及文件**：`src/multi_agent/langgraph_orchestrator.py`、`src/multi_agent/agents.py`、`tests/`。

### 7. 公开文档与 UI 仍宣传失效默认管理员密码

**现状**：README 与前端登录页显示 `admin/admin123`，而当前启动逻辑已要求通过环境变量提供管理员密码。

**风险**：误导部署者并鼓励弱凭据。

**整改**：删除默认账号提示，说明首次启动所需的 `ADMIN_PASSWORD` 与 `JWT_SECRET_KEY`，并校验强度与缺失场景。

**涉及文件**：`README.md`、`src/app/streamlit_app.py`、`.env.example`（待新增）。

## P1：本迭代整改

### 8. 文档乱码、过期且缺少可复制配置示例

**现状**：现有 `docs/industrial-gaps.md` 已乱码；README 中部分架构和能力描述过期；仓库没有 `.env.example`。

**整改**：统一 UTF-8，重写启动、配置、三入口能力差异和修复安全策略；新增不包含真实密钥的 `.env.example`。

### 9. 全量静态类型检查未通过

**现状**：`py -m mypy src --ignore-missing-imports --explicit-package-bases` 有 33 个错误，集中在 `llm_client.py`、`model_router.py`、`api/server.py`、`langgraph_orchestrator.py`、`streamlit_app.py`。

**整改**：逐模块补齐类型并消除重定义和可空值错误；CI 由局部检查升级为全 `src` 检查。

### 10. 前端和持久化失败被静默吞掉

**现状**：Streamlit 等位置使用 `except Exception: pass`，消息、会话和索引失败不会通知用户。

**整改**：按异常类型记录日志，向用户显示可操作的失败信息；会话、消息和索引使用事务与幂等消息 ID，不能按内容文本去重。

**涉及文件**：`src/app/streamlit_app.py`、`src/storage/database.py`、`src/memory/vector_store.py`。

### 11. VectorStore 的并发与异常处理不足

**现状**：对象长期持有单个 SQLite 连接，写入失败可能返回空字符串，检索未按所有者过滤。

**整改**：使用明确的连接生命周期和并发策略；让失败可观测；完成所有者字段迁移和 owner-aware 查询。

### 12. CLI 工具、安全提示和实际能力不一致

**现状**：CLI 暴露 `run_command`，旧编排提示 Executor 运行测试，但编排和工具绑定行为不完整，也未接入 Sandbox、HITL、ContextMemory 和完整 LangGraph。

**整改**：统一到新编排或移除误导接口。若保留命令执行，必须实现项目根约束、命令白名单、超时、审计和真正隔离。

### 13. Sandbox 不是生产级安全边界

**现状**：代码已说明当前 Sandbox 不是真正隔离，Docker 隔离也不是生产路径的硬前提。

**整改**：生产模式强制容器隔离；无法隔离时禁用 `run_command` 并 fail-closed。UI 和文档必须如实说明限制。

### 14. 依赖声明可能漂移

**现状**：`requirements.txt`、`pyproject.toml`、README 和 CI 分别维护安装说明。

**整改**：确定单一依赖来源，并让 CI、开发环境和文档从同一约束生成或校验。

## P2：维护性与测试债务

### 15. 清理高风险路径中的宽泛异常捕获

优先处理 `streamlit_app.py`、`sandbox.py`、`vector_store.py` 和 `database.py` 的裸 `except` 或吞异常逻辑；每处明确恢复策略和中文原因注释。

### 16. 明确测试样本的仓库语义

`tests/bug_injection_sample.py` 已在真实 Fixer 流程中被修改，但尚未提交。应明确它是“漏洞注入基线”还是“修复后样本”；建议保留基线并创建独立的期望输出 fixture，避免测试输入被流程副作用改变。

### 17. 建立覆盖完整工作流的测试矩阵

**状态：部分完成（2026-08-17）**。已覆盖 receipt、行为验证、命令白名单及三入口编排一致性；所有者隔离、持久化和完整端到端模型调用仍待后续整改。

增加审查、审批、拒绝写入、路径越界、修复、行为验证、持久化、用户隔离、CLI/API/前端入口一致性的测试，并在 CI 显示对应结果。

## 推荐执行顺序与验收

1. 先完成 P0 的入口统一、写入边界、审批、所有者隔离和修复证据，阻断越权与误修复。
2. 再完成 P1 的文档配置、类型检查、错误可观测性、向量存储与 Sandbox。
3. 最后处理 P2 的异常清理、测试样本语义和工作流测试矩阵。

每项整改的验收条件是：相应自动化测试通过、中文注释与中文说明完整、未出现新的跨入口行为差异或用户数据越权。
