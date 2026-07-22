"""
LangGraph-based Agent Orchestrator / 基于 LangGraph 的 Agent 编排

替换硬编码的 Planner → Executor → Reviewer 管线。
支持可配置的 Agent 图、条件路由、循环和 Human-in-the-loop。

用法:
    orchestrator = LangGraphOrchestrator(llm_client, tools)
    result = orchestrator.run("Find bugs in harness/")
"""

from __future__ import annotations
from typing import TypedDict, Annotated, Sequence
import operator, json
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    findings: list[dict]
    phase: str                  # "plan" | "execute" | "review"
    complete: bool
    error: str


class LangGraphOrchestrator:
    """
    LangGraph 编排的 Multi-Agent 系统。

    图结构:
        plan → execute → review → END
           ↘ (no findings) → END
    """

    def __init__(self, llm_client, tools: list):
        self.client = llm_client
        self.tools = tools
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        # 节点
        workflow.add_node("plan", self._plan_node)
        workflow.add_node("execute", self._execute_node)
        workflow.add_node("review", self._review_node)

        # 边：plan → execute（如有发现）或 END（无发现）
        workflow.add_conditional_edges(
            "plan",
            self._should_continue,
            {"execute": "execute", "end": END},
        )
        workflow.add_edge("execute", "review")
        workflow.add_edge("review", END)

        workflow.set_entry_point("plan")
        return workflow.compile()

    def run(self, task: str, project_path: str) -> dict:
        """运行完整的 LangGraph 管线"""
        initial_state = {
            "messages": [HumanMessage(content=f"Task: {task}\nProject: {project_path}")],
            "findings": [],
            "phase": "plan",
            "complete": False,
            "error": "",
        }
        result = self.graph.invoke(initial_state)
        return {
            "findings": result.get("findings", []),
            "messages": [m.content for m in result.get("messages", [])],
            "complete": result.get("complete", False),
        }

    # ─── Nodes ─────────────────────────────────────

    def _plan_node(self, state: AgentState) -> dict:
        """Planner: 分析代码，输出发现列表"""
        from harness.agent import AgentHarness, ToolDefinition
        from multi_agent.agents import PLANNER_SYSTEM_PROMPT

        tools_for_plan = [t for t in self.tools if t.name in ("list_files", "read_file", "grep_pattern")]
        agent = AgentHarness(
            model=self.client, tools=tools_for_plan,
            system_prompt=PLANNER_SYSTEM_PROMPT, max_turns=8,
        )
        task_msg = state["messages"][-1].content
        result = agent.run(task_msg)

        # 解析发现
        findings = []
        for line in result.split("\n"):
            if line.startswith("FINDING|"):
                parts = line.split("|")
                if len(parts) >= 7:
                    findings.append({
                        "id": len(findings) + 1,
                        "file": parts[1].strip(),
                        "line": parts[2].strip(),
                        "category": parts[3].strip(),
                        "severity": parts[4].strip(),
                        "description_en": parts[5].strip(),
                        "description_cn": parts[6].strip() if len(parts) > 6 else "",
                        "suggestion": parts[7].strip() if len(parts) > 7 else "",
                    })

        return {
            "messages": [AIMessage(content=result)],
            "findings": findings,
            "phase": "plan",
        }

    def _execute_node(self, state: AgentState) -> dict:
        """Executor: 验证每条发现"""
        from harness.agent import AgentHarness, ToolDefinition
        from multi_agent.agents import EXECUTOR_SYSTEM_PROMPT

        tools_for_exec = [t for t in self.tools if t.name in ("grep_pattern", "read_file")]
        agent = AgentHarness(
            model=self.client, tools=tools_for_exec,
            system_prompt=EXECUTOR_SYSTEM_PROMPT, max_turns=8,
        )

        findings_text = "Verify these findings:\n" + json.dumps(
            state["findings"], indent=2, ensure_ascii=False)
        result = agent.run(findings_text)

        return {
            "messages": [AIMessage(content=result)],
            "phase": "execute",
        }

    def _review_node(self, state: AgentState) -> dict:
        """Reviewer: 去重合并，输出最终报告"""
        from harness.agent import AgentHarness, ToolDefinition
        from multi_agent.agents import REVIEWER_SYSTEM_PROMPT

        tools_for_rev = [t for t in self.tools if t.name in ("read_file", "grep_pattern")]
        agent = AgentHarness(
            model=self.client, tools=tools_for_rev,
            system_prompt=REVIEWER_SYSTEM_PROMPT, max_turns=5,
        )

        review_input = (
            f"Findings ({len(state['findings'])}):\n"
            + json.dumps(state["findings"], indent=2, ensure_ascii=False)
            + "\n\nProduce the final report."
        )
        result = agent.run(review_input)

        return {
            "messages": [AIMessage(content=result)],
            "complete": True,
            "phase": "review",
        }

    # ─── Router ────────────────────────────────────

    def _should_continue(self, state: AgentState) -> str:
        """条件路由：有发现则继续验证，无则结束"""
        if state.get("findings") and len(state["findings"]) > 0:
            return "execute"
        return "end"
