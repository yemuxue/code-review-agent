# 写入边界约束实施计划

> **供执行代理使用：** 必须按任务顺序执行；每个任务都遵循测试先行，完成后更新复选框。

**目标：** 让 Fixer 的 `write_file` 只能写入当前任务明确指定的项目根目录，拒绝目录外与符号链接逃逸路径。

**架构：** `write_file` 必须接收调用方提供的 `allowed_root` 并在任何备份或临时文件创建前校验解析后的目标路径。LangGraph 在创建 Fixer 时闭包绑定 `run()` 的 `project_path`，模型工具参数中不暴露该根目录，防止模型伪造范围。

**技术栈：** Python 3.14、`pathlib`、pytest、LangGraph。

---

### 任务 1：为写入边界建立回归测试

**文件：**
- 修改：`tests/test_fix_safety.py`

- [x] **步骤 1：添加项目根目录外写入的失败测试**

```python
def test_write_file_refuses_path_outside_allowed_root(tmp_path):
    allowed_root = tmp_path / "project"
    allowed_root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    result = write_file(
        str(outside), "x = 2\n", allowed_root=str(allowed_root)
    )

    assert "REFUSED" in result
    assert outside.read_text(encoding="utf-8") == "x = 1\n"
    assert not outside.with_suffix(".py.bak").exists()
```

- [x] **步骤 2：添加根目录内允许写入的失败测试**

```python
def test_write_file_allows_path_inside_allowed_root(tmp_path):
    allowed_root = tmp_path / "project"
    allowed_root.mkdir()
    target = allowed_root / "inside.py"

    result = write_file(
        str(target), "x = 1\n", allowed_root=str(allowed_root)
    )

    assert result.startswith("OK:"), result
    assert target.read_text(encoding="utf-8") == "x = 1\n"
```

- [x] **步骤 3：运行测试并确认因缺少参数或未限制路径而失败**

运行：`pytest tests/test_fix_safety.py -q`

预期：新增的根目录外写入测试失败，证明当前实现没有边界限制。

### 任务 2：让底层写入工具 fail-closed

**文件：**
- 修改：`src/tools/git_tools.py:165-220`
- 修改：`tests/test_fix_safety.py`

- [x] **步骤 1：要求调用方显式提供 `allowed_root`**

```python
def write_file(
    file_path: str = "", content: str = "", start_line: int = 1,
    *, allowed_root: str = "",
) -> str:
```

- [x] **步骤 2：在读取、备份或写入前解析并校验边界**

```python
    if not allowed_root:
        return "ERROR: REFUSED — allowed_root is required for write_file."
    try:
        root = Path(allowed_root).resolve(strict=True)
        path = Path(file_path).resolve(strict=False)
        path.relative_to(root)
    except (OSError, ValueError):
        return "ERROR: REFUSED — target path is outside the allowed project root."
```

新增中文注释说明：必须在创建 `.bak` 或 `.tmp` 前解析路径，避免符号链接使受限路径逃逸。

- [x] **步骤 3：更新已有测试，全部显式传入 `tmp_path` 作为允许根目录**

```python
write_file(str(p), new, start_line=1, allowed_root=str(tmp_path))
```

- [x] **步骤 4：运行测试并确认通过**

运行：`pytest tests/test_fix_safety.py -q`

预期：所有写入安全测试通过，且目录外目标没有副作用。

### 任务 3：仅向 Fixer 注入固定项目根目录

**文件：**
- 修改：`src/multi_agent/langgraph_orchestrator.py:51-74`
- 新增测试：`tests/test_write_scope.py`

- [x] **步骤 1：添加作用域工具测试**

```python
def test_fixer_write_tool_rejects_file_outside_run_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    orchestrator = LangGraphOrchestrator(object(), [WRITE_TOOL])
    orchestrator._project_root = project.resolve()
    tool = orchestrator._tools_for_names(["write_file"])[0]

    result = tool.fn(file_path=str(outside), content="x = 2\n")
    assert "REFUSED" in result
```

- [x] **步骤 2：新增 `_tools_for_names`，仅替换 Fixer 的写入函数**

```python
def _tools_for_names(self, tool_names: list[str]) -> list:
    selected = [tool for tool in self.tools if tool.name in tool_names]
    if "write_file" not in tool_names:
        return selected
    return [self._scoped_write_tool(tool) if tool.name == "write_file" else tool
            for tool in selected]
```

`_scoped_write_tool` 使用 `functools.partial` 或闭包把 `self._project_root` 作为 `allowed_root` 传入底层函数；工具 schema 保持不变。

- [x] **步骤 3：在 `run()` 开始时解析并校验 `project_path`**

```python
self._project_root = Path(project_path).resolve(strict=True)
if not self._project_root.is_dir():
    raise ValueError("project_path must be an existing directory")
```

- [x] **步骤 4：运行新增测试和全量测试**

运行：`pytest tests/test_write_scope.py tests/test_fix_safety.py -q`，随后运行 `pytest tests -q`。

预期：Fixer 只能写入 `run()` 提供的根目录，52 项既有测试与新增测试均通过。

### 任务 4：提交独立修复

**文件：**
- 修改：`src/tools/git_tools.py`
- 修改：`src/multi_agent/langgraph_orchestrator.py`
- 修改：`tests/test_fix_safety.py`
- 新增：`tests/test_write_scope.py`
- 新增：`docs/superpowers/plans/2026-08-16-write-boundary-scope.md`

- [x] **步骤 1：检查范围与编码**

运行：`git diff --check`、`git status --short`。

预期：只包含上述五个文件，新增注释和计划均为 UTF-8 中文。

- [x] **步骤 2：提交**

```bash
git add src/tools/git_tools.py src/multi_agent/langgraph_orchestrator.py tests/test_fix_safety.py tests/test_write_scope.py docs/superpowers/plans/2026-08-16-write-boundary-scope.md
git commit -m "fix: scope fixer writes to project root"
```
