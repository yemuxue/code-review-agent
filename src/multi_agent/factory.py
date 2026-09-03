"""三个正式入口共用的 LangGraph 编排器工厂。"""
from __future__ import annotations
import os

from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator


def create_langgraph_orchestrator(
    client,
    tools: list,
    *,
    sandbox=None,
    hitl=None,
    memory=None,
    auto_fix: bool = False,
    skills_dir: str | None = None,
) -> LangGraphOrchestrator:
    """集中传递运行依赖，避免不同入口悄然使用不同编排。

    skills_dir 解析优先级：显式参数 > $SKILLS_DIR 环境变量 > <仓库根>/skills 默认。
    环境变量回退放这里而非各入口，保证 CLI/Streamlit/API 行为一致
    （.env 中设置 SKILLS_DIR 也会经 src.config 推入 os.environ 而生效）。
    """
    if skills_dir is None:
        skills_dir = os.environ.get("SKILLS_DIR") or None  # 空串视为未设置
    return LangGraphOrchestrator(
        client,
        tools,
        sandbox=sandbox,
        hitl=hitl,
        memory=memory,
        auto_fix=auto_fix,
        skills_dir=skills_dir,
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
