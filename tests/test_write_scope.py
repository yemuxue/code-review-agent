"""LangGraph Fixer 写入范围测试。"""
import sys
from pathlib import Path

import pytest

# 与现有测试保持一致：pytest 直接运行时需显式暴露项目根目录。
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.harness.agent import ToolDefinition
from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator
from src.tools.git_tools import write_file


WRITE_TOOL = ToolDefinition(
    name="write_file",
    description="写入文件",
    parameters={"type": "object", "properties": {}},
    fn=write_file,
)


def _orchestrator(project_root: Path) -> LangGraphOrchestrator:
    orchestrator = LangGraphOrchestrator(object(), [WRITE_TOOL])
    orchestrator._project_root = project_root.resolve()
    return orchestrator


def test_fixer_write_tool_refuses_file_outside_run_project(tmp_path):
    """Fixer 的写入闭包不能绕过本次运行的项目根目录。"""
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")

    tool = _orchestrator(project)._tools_for_names(["write_file"])[0]
    result = tool.fn(file_path=str(outside), content="x = 2\n")

    assert "REFUSED" in result
    assert outside.read_text(encoding="utf-8") == "x = 1\n"
    assert tool.parameters == WRITE_TOOL.parameters


def test_fixer_write_tool_allows_file_inside_run_project(tmp_path):
    """Fixer 在项目根目录内仍能执行经过约束的正常写入。"""
    project = tmp_path / "project"
    project.mkdir()
    target = project / "inside.py"

    tool = _orchestrator(project)._tools_for_names(["write_file"])[0]
    result = tool.fn(file_path=str(target), content="x = 1\n")

    assert result.startswith("OK:"), result
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_run_rejects_missing_project_directory(tmp_path):
    """任务根目录不存在时必须在调用模型前失败。"""
    orchestrator = LangGraphOrchestrator(object(), [WRITE_TOOL])

    with pytest.raises(ValueError, match="project_path"):
        orchestrator.run("review", str(tmp_path / "missing"))
