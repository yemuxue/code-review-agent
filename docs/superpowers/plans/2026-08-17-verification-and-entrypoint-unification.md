# 修复证据与入口统一实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 让修复落盘与行为验证可审计，并统一三个正式入口的多代理 LangGraph 编排。

**架构：** LangGraph 在每次运行内维护私有写入 receipt；验证节点只认可匹配 receipt 的写入，并用受控 pytest 结果把 `APPLIED` 升为 `VERIFIED`。新增工厂负责三入口共用的编排器构造，旧编排保留兼容但不再作为正式入口。

**技术栈：** Python 3.14、pytest、LangGraph、FastAPI、Streamlit。

---

### 任务 1：本次写入 receipt 与状态机

**文件：**
- 修改：`src/multi_agent/langgraph_orchestrator.py`
- 测试：`tests/test_langgraph_fix_verification.py`

- [ ] **步骤 1：先写失败测试**

```python
def test_old_backup_without_current_receipt_is_not_applied(tmp_path):
    target = tmp_path / "target.py"
    target.write_text("x = 1\n", encoding="utf-8")
    target.with_suffix(".py.bak").write_text("x = 0\n", encoding="utf-8")
    orchestrator = LangGraphOrchestrator(object(), [])
    result = orchestrator._verify_fix_node({"fixes": [{"finding_id": 1, "file_path": str(target), "status": "FIXED"}]})
    assert result["fixes"][0]["status"] == "NOT_APPLIED"
```

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_langgraph_fix_verification.py::test_old_backup_without_current_receipt_is_not_applied -q`

预期：失败，因为旧实现只检查 `.bak` 是否存在。

- [ ] **步骤 3：实现最小 receipt 记录**

```python
def _record_write_receipt(self, file_path: str, before: str, after: str, backup_path: str) -> None:
    self._write_receipts[str(_Path(file_path).resolve())] = {
        "before_sha256": _sha256(before), "after_sha256": _sha256(after),
        "backup_path": backup_path, "written_at": time.time(),
    }
```

在每次 `run()` 开始重置映射；scoped 工具只有在底层写入成功且磁盘内容等于请求内容时记录 receipt。`verify_fix` 把有匹配 receipt 的 `FIXED` 追加为 `APPLIED`，其他追加为 `NOT_APPLIED`。

- [ ] **步骤 4：运行测试并确认通过**

运行：`pytest tests/test_langgraph_fix_verification.py -q`

预期：receipt 匹配、旧备份假阳性、文件被随后篡改三类测试全部通过。

- [ ] **步骤 5：提交**

运行：`git add src/multi_agent/langgraph_orchestrator.py tests/test_langgraph_fix_verification.py && git commit -m "fix: verify current write receipts"`

### 任务 2：行为验证状态

**文件：**
- 修改：`src/multi_agent/langgraph_orchestrator.py`、`src/multi_agent/agents.py`
- 测试：`tests/test_langgraph_fix_verification.py`

- [ ] **步骤 1：先写失败测试**

```python
def test_applied_fix_without_behavior_test_is_not_verified(tmp_path):
    orchestrator = _orchestrator_with_matching_receipt(tmp_path)
    result = orchestrator._verify_fix_node(_fixed_state(tmp_path / "target.py"))
    assert _statuses(result["fixes"]) == ["APPLIED"]
```

再增加明确 `VERIFY|1|pytest tests/test_target.py -q` 返回成功后存在 `VERIFIED` 的测试，以及非 pytest 命令不执行的测试。

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_langgraph_fix_verification.py -q`

预期：失败，因为旧实现没有 `APPLIED` / `VERIFIED` 状态。

- [ ] **步骤 3：实现受控验证**

解析 Fixer 的 `VERIFY|finding_id|pytest ...` 行；仅接受项目根目录内的 pytest，超时或非白名单命令均不执行。命令成功时追加 `VERIFIED`，否则只保留 `APPLIED` 并记录中文原因。

- [ ] **步骤 4：运行测试并确认通过**

运行：`pytest tests/test_langgraph_fix_verification.py -q`

预期：全部通过，且没有网络或真实模型调用。

- [ ] **步骤 5：提交**

运行：`git add src/multi_agent/langgraph_orchestrator.py src/multi_agent/agents.py tests/test_langgraph_fix_verification.py && git commit -m "feat: distinguish applied and verified fixes"`

### 任务 3：三个入口共用 LangGraph 工厂

**文件：**
- 新增：`src/multi_agent/factory.py`
- 修改：`src/app/cli_multi.py`、`src/api/server.py`、`src/app/streamlit_app.py`
- 测试：`tests/test_entrypoint_orchestration.py`

- [ ] **步骤 1：先写失败测试**

```python
def test_all_multi_agent_entrypoints_import_shared_factory():
    for path in ENTRYPOINTS:
        source = path.read_text(encoding="utf-8")
        assert "create_langgraph_orchestrator" in source
        assert "MultiAgentOrchestrator" not in source
```

- [ ] **步骤 2：运行并确认失败**

运行：`pytest tests/test_entrypoint_orchestration.py -q`

预期：失败，因为 CLI、API 仍导入旧 `MultiAgentOrchestrator`。

- [ ] **步骤 3：实现工厂与迁移入口**

```python
def create_langgraph_orchestrator(client, tools, *, sandbox=None, hitl=None, memory=None, auto_fix=False):
    return LangGraphOrchestrator(client, tools, sandbox=sandbox, hitl=hitl, memory=memory, auto_fix=auto_fix)
```

CLI 增加默认关闭的 `--auto-fix`；API 多代理结果改为取 LangGraph 最后的文本消息；Streamlit 改为调用该工厂并保留已有显式开关。

- [ ] **步骤 4：运行入口测试并确认通过**

运行：`pytest tests/test_entrypoint_orchestration.py tests/test_review_only_mode.py -q`

预期：全部通过，静态入口测试不触发真实模型请求。

- [ ] **步骤 5：提交**

运行：`git add src/multi_agent/factory.py src/app/cli_multi.py src/api/server.py src/app/streamlit_app.py tests/test_entrypoint_orchestration.py && git commit -m "refactor: unify LangGraph entrypoints"`

### 任务 4：回归验证与中文整改记录

**文件：**
- 修改：`docs/current-remediation-plan.md`
- 测试：`tests/`

- [ ] **步骤 1：更新中文整改状态**

将已完成的第 2、3、4、8 项更新为已验证状态，并保留验收命令与限制说明。

- [ ] **步骤 2：执行全量验证**

运行：`pytest tests -q`

预期：全部通过；任何失败先定位原因，不把失败归因于本次修改之外。

- [ ] **步骤 3：提交**

运行：`git add docs/current-remediation-plan.md && git commit -m "docs: record verification remediation"`
