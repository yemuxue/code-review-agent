"""三个正式入口必须共享同一个 LangGraph 编排工厂。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.multi_agent.factory import langgraph_final_report


ENTRYPOINTS = [
    Path("src/app/cli_multi.py"),
    Path("src/api/server.py"),
    Path("src/app/streamlit_app.py"),
]


def test_all_multi_agent_entrypoints_use_shared_langgraph_factory():
    """入口不得各自选择旧编排或直接构造新编排。"""
    for path in ENTRYPOINTS:
        source = path.read_text(encoding="utf-8")
        assert "create_langgraph_orchestrator" in source, path
        assert "MultiAgentOrchestrator" not in source, path


def test_shared_result_formatter_preserves_review_and_verification_reports():
    """自动修复完成后，调用方仍应收到审查结论与验证结论。"""
    report = langgraph_final_report({"messages": [
        "planner internals",
        "# Code Analysis Report\nreview conclusion",
        "## Fix Results\nfix summary",
        "## Fix Verification Report\nverification conclusion",
    ]})

    assert "review conclusion" in report
    assert "fix summary" in report
    assert "verification conclusion" in report
