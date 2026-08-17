"""三个正式入口共用的 LangGraph 编排器工厂。"""
from __future__ import annotations

from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator


def create_langgraph_orchestrator(
    client,
    tools: list,
    *,
    sandbox=None,
    hitl=None,
    memory=None,
    auto_fix: bool = False,
) -> LangGraphOrchestrator:
    """集中传递运行依赖，避免不同入口悄然使用不同编排。"""
    return LangGraphOrchestrator(
        client,
        tools,
        sandbox=sandbox,
        hitl=hitl,
        memory=memory,
        auto_fix=auto_fix,
    )


def langgraph_final_report(result: dict) -> str:
    """保留审查、修复和验证结论，隐藏规划与执行的中间消息。"""
    report_markers = ("# Code Analysis Report", "## Fix Results", "Fix Verification")
    reports = [str(message) for message in result.get("messages", [])
               if any(marker in str(message) for marker in report_markers)]
    if reports:
        return "\n\n---\n\n".join(reports)
    messages = result.get("messages", [])
    return str(messages[-1]) if messages else "No findings"
