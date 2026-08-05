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
    verdicts: list[dict]          # ← Executor 验证结果
    phase: str
    complete: bool
    error: str
    retry_count: int              # ← 防止无限循环
    max_retries: int              # ← 最大重试次数


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

    # ═══ 共享：消除三个节点中重复的 Agent 创建代码 ═══
    def _make_agent(self, tool_names: list[str], system_prompt: str, max_turns: int):
        """统一创建 Agent，注入 sandbox/hitl/memory"""
        tools = [t for t in self.tools if t.name in tool_names]
        agent = self.AgentHarness(model=self.client, tools=tools, system_prompt=system_prompt, max_turns=max_turns)
        if self.sandbox: agent.sandbox = self.sandbox
        if self.hitl:     agent.hitl = self.hitl
        if self.memory:   agent.memory = self.memory
        return agent

    # ═══ 图结构 ═══
    def _build_graph(self):
        w = StateGraph(AgentState)

        w.add_node("plan", self._plan_node)
        w.add_node("execute", self._execute_node)
        w.add_node("review", self._review_node)

        # plan → findings > 0 ? execute : end
        w.add_conditional_edges("plan", self._check_plan, {"execute": "execute", "end": END})

        # execute → 验证充分? review : re-execute
        w.add_conditional_edges("execute", self._check_execute, {"review": "review", "execute": "execute"})

        # review → 报告完整 + 未超重试? END : re-execute
        w.add_conditional_edges("review", self._check_review, {"end": END, "execute": "execute"})

        w.set_entry_point("plan")
        return w.compile()

    # ═══ 对外入口 ═══
    def run(self, task: str, project_path: str) -> dict:
        state = {
            "messages": [HumanMessage(content=f"Task: {task}\nProject: {project_path}")],
            "findings": [], "verdicts": [], "phase": "plan",
            "complete": False, "error": "", "retry_count": 0, "max_retries": 2,
        }
        result = self.graph.invoke(state)
        return {
            "findings": result.get("findings", []),
            "verdicts": result.get("verdicts", []),
            "messages": [m.content for m in result.get("messages", [])],
            "complete": result.get("complete", False),
            "retries": result.get("retry_count", 0),
        }

    # ═══ 三个条件路由 ═══
    def _check_plan(self, state: AgentState) -> str:
        """Planner 之后：有发现就验证，没发现直接结束"""
        findings = state.get("findings", [])
        return "execute" if findings and len(findings) > 0 else "end"

    def _check_execute(self, state: AgentState) -> str:
        """Executor 之后：验证结果是否充分"""
        verdicts = state.get("verdicts", [])
        findings = state.get("findings", [])
        retries = state.get("retry_count", 0)
        max_r = state.get("max_retries", 2)

        # 没有 verdicts → 验证不充分，但先走 review 看能做什么
        if not verdicts:
            return "review"

        # 验证覆盖率：应该至少验证了半数发现
        verified_count = len(verdicts)
        total = len(findings) if findings else 1
        coverage = verified_count / total if total > 0 else 1

        if coverage < 0.3 and retries < max_r:
            return "execute"  # 覆盖率太低，重试
        return "review"

    def _check_review(self, state: AgentState) -> str:
        """Reviewer 之后：报告是否完整"""
        messages = state.get("messages", [])
        retries = state.get("retry_count", 0)
        max_r = state.get("max_retries", 2)

        if not messages:
            return "end"

        last_msg = messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])

        # 检查报告完整性：必须包含关键字段
        has_report = any(kw in last_msg.lower() for kw in ["report", "finding", "issue", "##", "分析"])

        if not has_report and retries < max_r:
            return "execute"  # Reviewer 没产出像样报告，回头重验证
        return "end"

    # ═══ 三个节点 ═══
    def _plan_node(self, state: AgentState) -> dict:
        agent = self._make_agent(["list_files", "read_file", "grep_pattern"],
                                 self.PLANNER_PROMPT, max_turns=8)
        result = agent.run(state["messages"][-1].content)
        findings = self._parse_findings(result)
        return {"messages": [AIMessage(content=result)], "findings": findings, "phase": "plan"}

    def _execute_node(self, state: AgentState) -> dict:
        agent = self._make_agent(["grep_pattern", "read_file"],
                                 self.EXECUTOR_PROMPT, max_turns=8)
        result = agent.run(
            "Verify the following findings:\n" +
            json.dumps(state["findings"], indent=2, ensure_ascii=False) +
            "\n\nFor EACH finding, output: VERDICT|finding_id|CONFIRMED/FALSE_POSITIVE/UNCERTAIN|evidence"
        )
        verdicts = self._parse_verdicts(result, state["findings"])
        new_retry = state.get("retry_count", 0) + 1
        return {
            "messages": [AIMessage(content=result)],
            "verdicts": verdicts,
            "phase": "execute",
            "retry_count": new_retry,
        }

    def _review_node(self, state: AgentState) -> dict:
        agent = self._make_agent(["read_file", "grep_pattern"],
                                 self.REVIEWER_PROMPT, max_turns=5)
        merged = self._merge_findings_verdicts(state["findings"], state.get("verdicts", []))
        result = agent.run(merged + "\n\nProduce final report.")
        return {
            "messages": [AIMessage(content=result)],
            "complete": True,
            "phase": "review",
        }

    # ═══ 解析 + 合并 ═══
    def _parse_findings(self, text: str) -> list[dict]:
        findings = []
        for line in text.split("\n"):
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
        return findings

    def _parse_verdicts(self, text: str, findings: list) -> list[dict]:
        verdicts = []
        seen_ids = set()
        for line in text.split("\n"):
            if line.startswith("VERDICT|"):
                parts = line.split("|")
                if len(parts) >= 4:
                    try:
                        fid = int(parts[1].strip())
                        if fid not in seen_ids:
                            seen_ids.add(fid)
                            verdicts.append({
                                "finding_id": fid,
                                "verdict": parts[2].strip(),
                                "evidence": parts[3].strip() if len(parts) > 3 else "",
                            })
                    except ValueError:
                        continue
        return verdicts

    def _merge_findings_verdicts(self, findings: list, verdicts: list) -> str:
        vmap = {v["finding_id"]: v for v in verdicts}
        lines = [f"## Merged Results ({len(findings)} findings, {len(verdicts)} verified)\n"]
        for f in findings:
            v = vmap.get(f["id"], {"verdict": "UNVERIFIED", "evidence": ""})
            lines.append(
                f"#{f['id']} [{f['severity']}] {f['file']}:{f['line']} — {f['description_en']}\n"
                f"  Verdict: {v['verdict']} | Evidence: {v.get('evidence', 'N/A')}"
            )
        return "\n\n".join(lines)
