# 默认仅审查模式实施计划

> **供执行代理使用：** 必须按任务顺序执行；每个任务都遵循测试先行，完成后更新复选框。

**目标：** 多代理审查默认不进入 Fixer 写入阶段，只有用户明确选择自动修复时才会修复并验证文件。

**架构：** 在 `LangGraphOrchestrator` 构造函数引入默认值为 `False` 的 `auto_fix` 标志。修复分支路由在标志关闭时直接结束图；Streamlit 多代理侧栏提供同样默认关闭的复选框，并把该值传给编排器。

**技术栈：** Python 3.14、pytest、Streamlit、LangGraph。

---

### 任务 1：为默认仅审查路由建立失败测试

**文件：**
- 新增：`tests/test_review_only_mode.py`

- [x] **步骤 1：添加默认状态跳过 Fixer 的测试**

```python
def test_review_only_mode_skips_confirmed_findings():
    orchestrator = LangGraphOrchestrator(object(), [])
    route = orchestrator._fan_out_fix(CONFIRMED_STATE)

    assert route == [END]
```

- [x] **步骤 2：添加显式自动修复仍进入 Fixer 的测试**

```python
def test_auto_fix_mode_routes_confirmed_findings_to_fixer():
    orchestrator = LangGraphOrchestrator(object(), [], auto_fix=True)
    route = orchestrator._fan_out_fix(CONFIRMED_STATE)

    assert len(route) == 1
    assert route[0].node == "fix_one"
```

- [x] **步骤 3：运行测试并确认当前默认行为会错误地进入 Fixer**

运行：`pytest tests/test_review_only_mode.py -q`

预期：默认仅审查测试失败；显式自动修复测试因构造函数不接受参数失败。

### 任务 2：最小化修复编排器路由

**文件：**
- 修改：`src/multi_agent/langgraph_orchestrator.py:51-64,170-190`

- [x] **步骤 1：在构造函数中定义默认关闭的自动修复标志**

```python
def __init__(self, llm_client, tools: list, sandbox=None, hitl=None,
             memory=None, auto_fix: bool = False):
    self.auto_fix = auto_fix
```

- [x] **步骤 2：在 `_fan_out_fix` 开始处终止默认仅审查流程**

```python
if not self.auto_fix:
    return [END]
```

添加简洁中文注释，说明该检查必须早于 finding 和 verdict 处理，确保只审查任务没有写入副作用。

- [x] **步骤 3：运行路由测试并确认通过**

运行：`pytest tests/test_review_only_mode.py -q`

预期：默认模式不产生 `fix_one` 任务；显式自动修复保留既有路由。

### 任务 3：把 Streamlit 交互绑定到编排器

**文件：**
- 修改：`src/app/streamlit_app.py:230-238,483-492`
- 测试：`tests/test_review_only_mode.py`

- [x] **步骤 1：在多代理模式侧栏提供默认关闭的复选框**

```python
auto_fix_enabled = st.checkbox(
    "Allow automatic fixes / 允许自动修复",
    value=False,
    help="默认仅审查；开启后才允许 Fixer 修改当前项目内的文件。",
)
```

仅在多代理模式下显示此控件；单代理模式不引入写入能力。

- [x] **步骤 2：把复选框值传入 LangGraph 编排器**

```python
orch = Orchestrator(
    client, TOOLS, sandbox=Sandbox(), hitl=hitl_guard,
    memory=ContextMemory(strategy="hybrid", window_size=10),
    auto_fix=auto_fix_enabled,
)
```

- [x] **步骤 3：增加静态绑定测试**

```python
source = Path("src/app/streamlit_app.py").read_text(encoding="utf-8")
assert "auto_fix=auto_fix_enabled" in source
```

- [x] **步骤 4：运行目标测试和完整测试**

运行：`pytest tests/test_review_only_mode.py -q`，随后运行 `pytest tests -q`。

预期：默认审查流程没有 Fixer 写入，显式自动修复保留功能，全部测试通过。

### 任务 4：提交独立修复

**文件：**
- 修改：`src/multi_agent/langgraph_orchestrator.py`
- 修改：`src/app/streamlit_app.py`
- 新增：`tests/test_review_only_mode.py`
- 新增：`docs/superpowers/plans/2026-08-17-review-only-default.md`

- [x] **步骤 1：检查范围与编码**

运行：`git diff --check`、`git status --short`。

预期：只包含上述四个文件，新增 UI 文案、注释和计划均含中文且为 UTF-8。

- [x] **步骤 2：提交**

```bash
git add src/multi_agent/langgraph_orchestrator.py src/app/streamlit_app.py tests/test_review_only_mode.py docs/superpowers/plans/2026-08-17-review-only-default.md
git commit -m "fix: default multi-agent runs to review-only"
```
