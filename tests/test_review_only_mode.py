"""默认仅审查模式的 LangGraph 与前端绑定测试。"""
import sys
from pathlib import Path

from langgraph.graph import END

# 与现有测试保持一致：pytest 直接运行时需显式暴露项目根目录。
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator


CONFIRMED_STATE = {
    "findings": [{"id": 1, "file": "target.py"}],
    "verdicts": [{"finding_id": 1, "verdict": "CONFIRMED"}],
}


def test_review_only_mode_skips_confirmed_findings():
    """默认模式即使存在确认问题也不得进入 Fixer。"""
    orchestrator = LangGraphOrchestrator(object(), [])

    route = orchestrator._fan_out_fix(CONFIRMED_STATE)

    assert route == [END]


def test_auto_fix_mode_routes_confirmed_findings_to_fixer():
    """显式开启自动修复后，确认问题仍应进入对应的 Fixer 节点。"""
    orchestrator = LangGraphOrchestrator(object(), [], auto_fix=True)

    route = orchestrator._fan_out_fix(CONFIRMED_STATE)

    assert len(route) == 1
    assert route[0].node == "fix_one"


def test_streamlit_passes_explicit_auto_fix_choice_to_orchestrator():
    """前端必须把用户显式选择传入多代理编排器。"""
    source = Path("src/app/streamlit_app.py").read_text(encoding="utf-8")

    assert "auto_fix=auto_fix_enabled" in source
