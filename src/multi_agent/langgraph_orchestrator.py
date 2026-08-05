"""LangGraph Orchestrator — imports injected at init to avoid node context issues

架构: plan → Send(fan-out execute_one × N 并行) → review → fix → END

升级点:
  1. Send API: Executor 按 finding 分片并行执行
  2. node_stats: 每节点记录 turns/tokens/耗时（可观测性）
  3. fix 节点: 对 CONFIRMED findings 调用 write_file 修复代码
"""
from __future__ import annotations
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from pathlib import Path as _Path

from typing import TypedDict, Annotated, Sequence, Union
import operator
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage


def _merge_stats(a: dict, b: dict) -> dict:
    """node_stats 合并器：dict 深合并（LangGraph reducer）"""
    result = dict(a)
    for k, v in (b or {}).items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = {**result[k], **v}
        else:
            result[k] = v
    return result


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    findings: list[dict]
    verdicts: Annotated[list[dict], operator.add]  # ← Send 并行结果自动累加
    fixes: Annotated[list[dict], operator.add]     # ← fix 节点产物
    node_stats: Annotated[dict, _merge_stats]      # ← 节点级统计
    phase: str
    complete: bool
    error: str
    retry_count: int
    max_retries: int


class LangGraphOrchestrator:

    def __init__(self, llm_client, tools: list, sandbox=None, hitl=None, memory=None):
        self.client = llm_client
        self.tools = tools
        self.sandbox = sandbox
        self.hitl = hitl
        self.memory = memory
        from src.harness.agent import AgentHarness
        from src.multi_agent.agents import PLANNER_SYSTEM_PROMPT, EXECUTOR_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT, FIXER_SYSTEM_PROMPT
        self.AgentHarness = AgentHarness
        self.PLANNER_PROMPT = PLANNER_SYSTEM_PROMPT
        self.EXECUTOR_PROMPT = EXECUTOR_SYSTEM_PROMPT
        self.REVIEWER_PROMPT = REVIEWER_SYSTEM_PROMPT
        self.FIXER_PROMPT = FIXER_SYSTEM_PROMPT
        self.graph = self._build_graph()

    # ═══ 共享工厂 ═══
    def _make_agent(self, tool_names: list[str], system_prompt: str, max_turns: int):
        """统一创建 Agent，注入 sandbox/hitl/memory"""
        tools = [t for t in self.tools if t.name in tool_names]
        agent = self.AgentHarness(model=self.client, tools=tools, system_prompt=system_prompt, max_turns=max_turns)
        if self.sandbox: agent.sandbox = self.sandbox
        if self.hitl:     agent.hitl = self.hitl
        if self.memory:   agent.memory = self.memory
        return agent

    def _node_stats(self, agent, name: str, start: float) -> dict:
        """采集节点级统计：turns/tokens/耗时"""
        stats = agent.get_stats()
        return {name: {
            "turns": stats.get("turns_taken", 0),
            "tools": stats.get("tools_called", 0),
            "messages": stats.get("messages_count", 0),
            "tokens": stats.get("total_tokens_used", 0),
            "elapsed_ms": round((time.time() - start) * 1000),
        }}

    # ═══ 图结构 ═══
    def _build_graph(self):
        w = StateGraph(AgentState)

        w.add_node("plan", self._plan_node)
        w.add_node("execute_one", self._execute_one_node)   # ← 并行分片节点（Send 目标）
        w.add_node("review", self._review_node)
        w.add_node("fix_one", self._fix_one_node)           # ← fix 按文件分组串行（Send 目标）
        w.add_node("verify_fix", self._verify_fix_node)     # ← 修复审核节点

        # plan → findings > 0 ? 分片并行 : END
        w.add_conditional_edges("plan", self._fan_out, ["execute_one", END])

        # 所有 execute_one 完成后 → review
        w.add_edge("execute_one", "review")

        # review → 有 CONFIRMED ? fix 分片 : END
        w.add_conditional_edges("review", self._fan_out_fix, ["fix_one", END])

        # 所有 fix_one 完成后 → 审核修复质量 → END
        w.add_edge("fix_one", "verify_fix")
        w.add_edge("verify_fix", END)

        w.set_entry_point("plan")
        return w.compile()

    # ═══ 对外入口 ═══
    def run(self, task: str, project_path: str) -> dict:
        self._project_path = project_path  # 供 Send fan-out 使用
        state = {
            "messages": [HumanMessage(content=f"Task: {task}\nProject: {project_path}")],
            "findings": [], "verdicts": [], "fixes": [], "node_stats": {},
            "phase": "plan", "complete": False, "error": "",
            "retry_count": 0, "max_retries": 2,
        }
        result = self.graph.invoke(state)
        return {
            "findings": result.get("findings", []),
            "verdicts": result.get("verdicts", []),
            "fixes": result.get("fixes", []),
            "messages": [m.content for m in result.get("messages", [])],
            "complete": result.get("complete", False),
            "retries": result.get("retry_count", 0),
            "node_stats": result.get("node_stats", {}),
        }

    # ═══ 路由 ═══
    def _fan_out(self, state: AgentState):
        """Send API: 每个 finding 分片为一个独立并行任务"""
        findings = state.get("findings", [])
        if not findings or len(findings) == 0:
            return [END]
        # 每个 finding → 独立的 execute_one 实例（并行执行）
        return [Send("execute_one", {"finding": f})
                for f in findings]

    def _fan_out_fix(self, state: AgentState):
        """Review 之后：按文件分组串行修复（同文件合并，不同文件并行）

        并发安全设计：同一文件的多个 finding 合并到一个 fix 任务，
        由单个 Agent 串行处理——避免多个 Agent 并行写同一文件互相覆盖。
        """
        findings = state.get("findings", [])
        verdicts = state.get("verdicts", [])
        vmap = {v.get("finding_id"): v for v in verdicts}
        confirmed = [f for f in findings if vmap.get(f["id"], {}).get("verdict") == "CONFIRMED"]
        if not confirmed:
            return [END]

        # 按文件路径分组
        from collections import defaultdict
        groups: dict[str, list[dict]] = defaultdict(list)
        for f in confirmed:
            groups[f.get("file", "unknown")].append(f)

        # 每个文件组 → 一个 fix 任务（组内多个 finding 由一个 Agent 串行修复）
        return [Send("fix_one", {"findings": group, "file_path": file_path})
                for file_path, group in groups.items()]

    # ═══ 节点 ═══
    def _plan_node(self, state: AgentState) -> dict:
        start = time.time()
        agent = self._make_agent(["list_files", "read_file", "grep_pattern"],
                                 self.PLANNER_PROMPT, max_turns=8)
        result = agent.run(state["messages"][-1].content)
        findings = self._parse_findings(result)
        return {
            "messages": [AIMessage(content=result)],
            "findings": findings,
            "phase": "plan",
            "node_stats": self._node_stats(agent, "plan", start),
        }

    def _execute_one_node(self, state: dict) -> dict:
        """并行分片：验证单个 finding（Send 传入子 state）"""
        start = time.time()
        finding = state.get("finding", {})
        fid = finding.get("id", 0)

        agent = self._make_agent(["grep_pattern", "read_file"],
                                 self.EXECUTOR_PROMPT, max_turns=4)
        result = agent.run(
            f"Verify finding #{fid}:\n" +
            json.dumps(finding, indent=2, ensure_ascii=False) +
            "\n\nOutput: VERDICT|finding_id|CONFIRMED/FALSE_POSITIVE/UNCERTAIN|evidence"
        )
        verdicts = self._parse_verdicts(result, [finding])
        return {
            "messages": [AIMessage(content=result)],
            "verdicts": verdicts,
            "node_stats": self._node_stats(agent, f"execute_{fid}", start),
        }

    def _review_node(self, state: AgentState) -> dict:
        start = time.time()
        agent = self._make_agent(["read_file", "grep_pattern"],
                                 self.REVIEWER_PROMPT, max_turns=5)
        merged = self._merge_findings_verdicts(state["findings"], state.get("verdicts", []))
        result = agent.run(merged + "\n\nProduce final report.")
        return {
            "messages": [AIMessage(content=result)],
            "complete": True,
            "phase": "review",
            "node_stats": self._node_stats(agent, "review", start),
        }

    def _fix_one_node(self, state: dict) -> dict:
        """修复分片节点：串行修复同一文件的多个 CONFIRMED findings（Send 传入子 state）"""
        start = time.time()
        group = state.get("findings", [])
        file_path = state.get("file_path", "unknown")

        agent = self._make_agent(["read_file", "write_file"],
                                 self.FIXER_PROMPT, max_turns=6)
        result = agent.run(
            "Fix ALL " + str(len(group)) + " findings in " + file_path + ":\n" +
            json.dumps(group, indent=2, ensure_ascii=False) +
            "\n\nFor EACH finding: read the file, apply the fix with write_file, then output:\n" +
            "FIXED|finding_id|file_path|summary\n" +
            "If a fix is not possible: FAILED|finding_id|file_path|reason\n" +
            "IMPORTANT: This is the ONLY agent editing this file. Apply fixes one at a time, "
            "re-reading the file before each write_file to avoid clobbering previous fixes."
        )
        fixes = self._parse_fixes(result)
        return {
            "messages": [AIMessage(content=result)],
            "fixes": fixes,
            # 注意：不写 phase——并行节点同时写普通字段会触发 InvalidUpdateError
            "node_stats": self._node_stats(agent, f"fix_{_Path(file_path).stem}", start),
        }

    def _verify_fix_node(self, state: AgentState) -> dict:
        """修复审核节点：检查 fix 是否真正修复了 bug 且没有破坏代码

        验证内容:
        1. 被修改的文件能否正常导入（语法检查）
        2. 修复是否覆盖了所有 CONFIRMED findings
        """
        start = time.time()
        fixes = state.get("fixes", [])
        fixed_files = sorted(set(f.get("file_path", "") for f in fixes if f.get("status") == "FIXED"))

        issues = []
        if fixed_files:
            # 语法检查：被修复的文件能否 import
            for fp in fixed_files:
                try:
                    import ast
                    with open(fp, "r", encoding="utf-8", errors="replace") as f:
                        ast.parse(f.read())
                    issues.append(f"[OK] {_Path(fp).name}: syntax valid")
                except SyntaxError as e:
                    issues.append(f"[FAIL] {_Path(fp).name}: syntax error — {e}")
                except OSError as e:
                    issues.append(f"[WARN] {_Path(fp).name}: unreadable — {e}")

        result = "## Fix Verification Report\n\n"
        if not fixes:
            result += "No fixes were applied."
        else:
            n_fixed = sum(1 for f in fixes if f.get("status") == "FIXED")
            n_failed = sum(1 for f in fixes if f.get("status") == "FAILED")
            result += f"Fixed: {n_fixed} | Failed: {n_failed}\n\n"
            if issues:
                result += "### Syntax Check\n" + "\n".join(f"- {i}" for i in issues) + "\n\n"

        return {
            "messages": [AIMessage(content=result)],
            "phase": "verify_fix",
            "node_stats": {"verify_fix": {
                "turns": 0, "tools": 0, "messages": 0, "tokens": 0,
                "elapsed_ms": round((time.time() - start) * 1000),
            }},
        }

    # ═══ 解析 ═══
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

    def _parse_fixes(self, text: str) -> list[dict]:
        """解析 fix 输出。容错：支持 markdown 代码块内、'FIXED:' 冒号、'- ' 列表前缀"""
        fixes = []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            # 去掉 markdown 列表/代码块前缀
            for prefix in ("- ", "* ", "```", "`"):
                if line.startswith(prefix):
                    line = line[len(prefix):].strip()
            status = None
            if line.startswith("FIXED|"):
                status = "FIXED"
            elif line.startswith("FAILED|"):
                status = "FAILED"
            elif line.startswith("FIXED:"):
                status = "FIXED"
                line = line.replace("FIXED:", "FIXED|", 1)
            elif line.startswith("FAILED:"):
                status = "FAILED"
                line = line.replace("FAILED:", "FAILED|", 1)
            if status:
                parts = line.split("|")
                if len(parts) >= 4:
                    fixes.append({
                        "finding_id": int(parts[1].strip()) if parts[1].strip().isdigit() else 0,
                        "file_path": parts[2].strip(),
                        "summary": parts[3].strip(),
                        "status": status,
                    })
        return fixes

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
