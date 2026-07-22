"""LangGraph Orchestrator — imports injected at init to avoid node context issues"""
from __future__ import annotations
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from typing import TypedDict, Annotated, Sequence
import operator
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    findings: list[dict]
    phase: str
    complete: bool
    error: str


class LangGraphOrchestrator:

    def __init__(self, llm_client, tools: list, sandbox=None, hitl=None, memory=None):
        self.client = llm_client
        self.tools = tools
        self.sandbox = sandbox
        self.hitl = hitl
        self.memory = memory
        from src.harness.agent import AgentHarness
        from src.multi_agent.agents import PLANNER_SYSTEM_PROMPT, EXECUTOR_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT
        self.AgentHarness = AgentHarness
        self.PLANNER_PROMPT = PLANNER_SYSTEM_PROMPT
        self.EXECUTOR_PROMPT = EXECUTOR_SYSTEM_PROMPT
        self.REVIEWER_PROMPT = REVIEWER_SYSTEM_PROMPT
        self.graph = self._build_graph()

    def _build_graph(self):
        w = StateGraph(AgentState)
        w.add_node("plan", self._plan_node)
        w.add_node("execute", self._execute_node)
        w.add_node("review", self._review_node)
        w.add_conditional_edges("plan", self._should_continue, {"execute": "execute", "end": END})
        w.add_edge("execute", "review")
        w.add_edge("review", END)
        w.set_entry_point("plan")
        return w.compile()

    def run(self, task: str, project_path: str) -> dict:
        state = {
            "messages": [HumanMessage(content=f"Task: {task}\nProject: {project_path}")],
            "findings": [], "phase": "plan", "complete": False, "error": "",
        }
        result = self.graph.invoke(state)
        return {
            "findings": result.get("findings", []),
            "messages": [m.content for m in result.get("messages", [])],
            "complete": result.get("complete", False),
        }

    def _plan_node(self, state: AgentState) -> dict:
        tools = [t for t in self.tools if t.name in ("list_files", "read_file", "grep_pattern")]
        agent = self.AgentHarness(model=self.client, tools=tools, system_prompt=self.PLANNER_PROMPT, max_turns=8)
        if self.sandbox: agent.sandbox = self.sandbox
        if self.hitl: agent.hitl = self.hitl
        if self.memory: agent.memory = self.memory
        result = agent.run(state["messages"][-1].content)
        findings = []
        for line in result.split("\n"):
            if line.startswith("FINDING|"):
                parts = line.split("|")
                if len(parts) >= 6:
                    findings.append({
                        "id": len(findings) + 1, "file": parts[1].strip(), "line": parts[2].strip(),
                        "category": parts[3].strip(), "severity": parts[4].strip(),
                        "description_en": parts[5].strip() if len(parts) > 5 else "",
                        "description_cn": parts[6].strip() if len(parts) > 6 else "",
                        "suggestion": parts[7].strip() if len(parts) > 7 else "",
                    })
        return {"messages": [AIMessage(content=result)], "findings": findings, "phase": "plan"}

    def _execute_node(self, state: AgentState) -> dict:
        tools = [t for t in self.tools if t.name in ("grep_pattern", "read_file")]
        agent = self.AgentHarness(model=self.client, tools=tools, system_prompt=self.EXECUTOR_PROMPT, max_turns=8)
        if self.sandbox: agent.sandbox = self.sandbox
        if self.hitl: agent.hitl = self.hitl
        if self.memory: agent.memory = self.memory
        result = agent.run("Verify:\n" + json.dumps(state["findings"], indent=2, ensure_ascii=False))
        return {"messages": [AIMessage(content=result)], "phase": "execute"}

    def _review_node(self, state: AgentState) -> dict:
        tools = [t for t in self.tools if t.name in ("read_file", "grep_pattern")]
        agent = self.AgentHarness(model=self.client, tools=tools, system_prompt=self.REVIEWER_PROMPT, max_turns=5)
        if self.sandbox: agent.sandbox = self.sandbox
        if self.hitl: agent.hitl = self.hitl
        if self.memory: agent.memory = self.memory
        result = agent.run(f"Findings ({len(state['findings'])}):\n" + json.dumps(state["findings"], indent=2, ensure_ascii=False) + "\n\nProduce report.")
        return {"messages": [AIMessage(content=result)], "complete": True, "phase": "review"}

    def _should_continue(self, state: AgentState) -> str:
        return "execute" if (state.get("findings") and len(state["findings"]) > 0) else "end"
