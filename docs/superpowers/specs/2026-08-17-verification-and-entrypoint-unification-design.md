# 修复证据与入口统一设计

## 目标

在不扩大既有功能范围的前提下，消除修复流程把未写入的结果标为 `FIXED` 的风险；让修复状态能表达“已写入”和“已通过行为验证”的区别；并让 CLI、API、Streamlit 的多代理请求共享 LangGraph 编排。

## 范围与约束

- 只处理此前批准的第 2、3、4、8 项：写入证据、行为验证、入口统一及其测试维护。
- 不改变单代理 API 模式，不迁移历史数据，不接入新的外部服务。
- 默认模式仍是 `review-only`；只有显式 `auto_fix=True` 才会触发写入。
- 新增文档与非显然逻辑使用中文说明。

## 方案比较

### 方案 A：在现有 `write_file` 返回值中解析文字证据

不新增结构化数据，只从工具返回的字符串中判断是否写入。改动小，但模型输出、工具错误信息或已有备份均可能导致误判，无法可靠关联到一次运行。

### 方案 B：在 LangGraph 编排器中记录本次运行的写入 receipt（采用）

为 Fixer 注入的 scoped `write_file` 闭包记录成功写入的绝对路径、写前和写后 SHA-256、备份路径与时间。验证节点只读取当前 `run()` 创建的 receipt，且要求路径与新内容哈希仍一致。实现局部、无需改变工具公共返回协议，能彻底移除 `.bak` 作为成功证据的依赖。

### 方案 C：修改所有工具为强制返回 JSON receipt

证据最完整，但会改变 CLI、AgentHarness 和可能的第三方调用约定，超出本次最小改动范围。

## 设计

### 写入证据与状态

`LangGraphOrchestrator.run()` 每次运行重置私有 receipt 映射。scoped 写入工具在调用底层 `write_file` 前后读取目标文件并计算哈希；只有工具返回成功且文件内容确实与请求内容相符时才登记 receipt。

`verify_fix` 不再查看 `.bak` 是否存在。它首先把 Fixer 报告的 `FIXED` 条目转换为 `APPLIED`，前提是该文件存在匹配 receipt 且当前哈希仍与 receipt 的写后哈希一致；没有匹配证据时追加 `NOT_APPLIED`。原有完整性与备份回滚仍保留。

行为验证会解析 Fixer 输出的可选 `VERIFY|finding_id|command` 行。命令仅允许项目根目录内的 `pytest` 调用，使用现有受控执行能力并设置超时。对应测试通过才追加 `VERIFIED`；没有明确命令、命令被拒绝或测试失败时状态保持 `APPLIED`，并在报告中写明原因。不会把“语法可编译”当成业务正确性证明。

### 统一入口

新增一个轻量的 `create_langgraph_orchestrator(...)` 工厂，负责构造 `LangGraphOrchestrator` 并集中传递 `sandbox`、`hitl`、`memory`、`auto_fix`。CLI 和 API 的 `multi` 模式通过它执行；Streamlit 也改用该工厂。

CLI 只增加 `--auto-fix` 开关，默认关闭。API 的多代理路径保持原响应模型：以 LangGraph 最后一条审查消息作为 `result`，并保留节点统计。现有旧 `MultiAgentOrchestrator` 暂不删除，避免影响未迁移的外部导入，但三个正式入口都不再使用它。

### 测试

- 使用假 `write_file` 工具模拟旧 `.bak` 已存在但本次写入失败，断言结果不会出现 `APPLIED` 或 `VERIFIED`。
- 模拟真实本次写入并验证 receipt 的路径、哈希与状态转换。
- 通过 mock 验证无行为测试时状态保持 `APPLIED`，通过时才存在 `VERIFIED`。
- 验证 CLI、API 和 Streamlit 均从同一工厂创建多代理编排器，避免真实模型或网络调用。

## 错误处理

- `project_path` 不存在或写入越界继续由现有路径约束拒绝。
- receipt 缺失、目标文件随后被改动、或写入工具报错均视为未应用。
- 行为测试不是白名单 `pytest` 命令时不执行，并以可见状态说明原因。
- 任何完整性检查失败仍按已有策略从本次备份回滚。
