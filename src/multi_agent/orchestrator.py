"""
Multi-Agent Orchestrator: 任务分解 → 分派 → 执行 → 合并 → 最终报告

架构:
    User Task
        │
        ▼
    Orchestrator.delegate()
        │
        ├──► Phase 1: Planner  ── 分析代码 → 生成 findings 列表
        │
        ├──► Phase 2: Executor ── 验证每条 finding → CONFIRMED / FALSE_POSITIVE
        │         (并行执行：每条 finding 独立验证)
        │
        └──► Phase 3: Reviewer ── 去重合并 → 最终报告
"""

try:
    from src.harness.agent import AgentHarness, ToolDefinition
except ImportError:
    from harness.agent import AgentHarness, ToolDefinition
from typing import Callable


def parse_findings(text: str) -> list[dict]:
    """从 Planner 输出中解析 FINDING 行"""
    findings = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("FINDING|"):
            parts = line.split("|")
            if len(parts) >= 7:
                findings.append({
                    "id": len(findings) + 1,
                    "file": parts[1].strip(),
                    "line": parts[2].strip(),
                    "category": parts[3].strip(),
                    "severity": parts[4].strip(),
                    "description": parts[5].strip(),
                    "suggestion": parts[6].strip(),
                })
    return findings


def parse_verdicts(text: str) -> list[dict]:
    """从 Executor 输出中解析 VERDICT 行"""
    verdicts = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("VERDICT|"):
            parts = line.split("|")
            if len(parts) >= 4:
                verdicts.append({
                    "finding_id": int(parts[1].strip()),
                    "verdict": parts[2].strip(),
                    "evidence": parts[3].strip() if len(parts) > 3 else "",
                })
    return verdicts


class MultiAgentOrchestrator:
    """
    Multi-Agent 调度器。

    用法:
        orch = MultiAgentOrchestrator(llm_client, all_tools)
        report = orch.run("Analyze /path/to/project for bugs")
    """

    def __init__(self, llm_client, all_tools: list[ToolDefinition],
                 logger=None, sandbox=None, hitl=None, memory=None):
        self.client = llm_client
        self.all_tools = all_tools
        self.logger = logger
        self.sandbox = sandbox
        self.hitl = hitl
        self.memory = memory

        from .agents import AGENT_DEFINITIONS
        self._defs = AGENT_DEFINITIONS

        # 按名称筛选工具
        def _tools_for(names: list[str]) -> list[ToolDefinition]:
            return [t for t in all_tools if t.name in names]

        self._planner_tools = _tools_for(self._defs["planner"]["tools"])
        self._executor_tools = _tools_for(self._defs["executor"]["tools"])
        self._reviewer_tools = _tools_for(self._defs["reviewer"]["tools"])

    def run(self, task: str, project_path: str) -> dict:
        """
        运行完整的 Multi-Agent 分析流程。

        Returns:
            {
                "planner_findings": [...],
                "executor_verdicts": [...],
                "final_report": "markdown string",
                "stats": {"planner": {...}, "executor": {...}, "reviewer": {...}}
            }
        """
        print("=" * 50)
        print(f"  Multi-Agent Analysis: {project_path}")
        print("=" * 50)

        # ─── Phase 1: Planner ───
        print("\n[Phase 1/3] Planner: Analyzing code structure...")
        planner_result = self._run_planner(task, project_path)
        findings = parse_findings(planner_result)

        if not findings:
            print("  No findings from Planner. Task complete.")
            return {
                "planner_findings": [],
                "executor_verdicts": [],
                "final_report": "## No issues found.\n\nThe Planner did not identify any potential issues.",
                "stats": {"planner": self._planner_agent.get_stats()},
            }

        print(f"  Found {len(findings)} potential issues.")
        for f in findings:
            print(f"    [{f['severity']}] {f['file']}:{f['line']} — {f['description'][:60]}")

        # ─── Phase 2: Executor ───
        print(f"\n[Phase 2/3] Executor: Verifying {len(findings)} findings...")
        executor_result = self._run_executor(findings, project_path)
        verdicts = parse_verdicts(executor_result)

        confirmed = sum(1 for v in verdicts if v["verdict"] == "CONFIRMED")
        fp = sum(1 for v in verdicts if v["verdict"] == "FALSE_POSITIVE")
        uncertain = sum(1 for v in verdicts if v["verdict"] == "UNCERTAIN")
        print(f"  Verdicts: {confirmed} confirmed, {fp} false-positive, {uncertain} uncertain")

        # ─── Phase 3: Reviewer ───
        print(f"\n[Phase 3/3] Reviewer: Producing final report...")
        final_report = self._run_reviewer(findings, verdicts, project_path)
        print(f"  Report ready.")

        return {
            "planner_findings": findings,
            "executor_verdicts": verdicts,
            "final_report": final_report,
            "stats": {
                "planner": self._planner_agent.get_stats(),
                "executor": self._executor_agent.get_stats(),
                "reviewer": self._reviewer_agent.get_stats(),
            },
        }

    # ─── 内部：各 Phase 的实现 ─────────────────────

    def _run_planner(self, task: str, project_path: str) -> str:
        """Phase 1: Planner 分析代码"""
        from .agents import PLANNER_SYSTEM_PROMPT

        self._planner_agent = AgentHarness(
            model=self.client,
            tools=self._planner_tools,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            max_turns=8,
            logger=self.logger,
        )

        query = (
            f"Task: {task}\n\n"
            f"Project path: {project_path}\n\n"
            f"Start by listing the project files with list_files(path='{project_path}'). "
            f"Then read the key source files. For each file, output findings in the "
            f"FINDING|file|line|category|severity|description|suggestion format.\n\n"
            f"Be thorough but focus on real issues, not style nitpicks."
        )
        return self._planner_agent.run(query)

    def _run_executor(self, findings: list[dict], project_path: str) -> str:
        """Phase 2: Executor 验证每条 finding"""
        from .agents import EXECUTOR_SYSTEM_PROMPT

        self._executor_agent = AgentHarness(
            model=self.client,
            tools=self._executor_tools,
            system_prompt=EXECUTOR_SYSTEM_PROMPT,
            max_turns=8,
            logger=self.logger,
        )
        if self.sandbox: self._planner_agent.sandbox = self.sandbox
        if self.hitl: self._planner_agent.hitl = self.hitl
        if self.memory: self._planner_agent.memory = self.memory

        # 把 Planner 的发现列出来
        findings_text = "## Planner's Findings to Verify\n\n"
        for f in findings:
            findings_text += (
                f"Finding #{f['id']}: [{f['category']}] {f['severity']} — {f['file']}:{f['line']}\n"
                f"  Description: {f['description']}\n"
                f"  Suggestion: {f['suggestion']}\n\n"
            )

        query = (
            f"Verify the following findings from the Planner for the project at: {project_path}\n\n"
            f"{findings_text}\n\n"
            f"For EACH finding #1 through #{len(findings)}, output:\n"
            f"VERDICT|finding_id|CONFIRMED/FALSE_POSITIVE/UNCERTAIN|Evidence: ...\n\n"
            f"Use grep_pattern to search for the code in each finding. "
            f"Use run_command to execute tests if applicable.\n"
            f"Be honest: if you can't find evidence either way, say UNCERTAIN."
        )
        return self._executor_agent.run(query)

    def _run_reviewer(self, findings: list[dict], verdicts: list[dict], project_path: str) -> str:
        """Phase 3: Reviewer 去重合并，输出最终报告"""
        from .agents import REVIEWER_SYSTEM_PROMPT

        # 把 findings 和 verdicts 合并
        verdict_map = {v["finding_id"]: v for v in verdicts}

        summary = "## Input for Review\n\n"
        for f in findings:
            v = verdict_map.get(f["id"], {"verdict": "UNVERIFIED", "evidence": "Not checked by Executor"})
            summary += (
                f"#{f['id']}: [{f['category']}] {f['severity']} — {f['file']}:{f['line']}\n"
                f"  Description: {f['description']}\n"
                f"  Suggestion: {f['suggestion']}\n"
                f"  Verdict: {v['verdict']}\n"
                f"  Evidence: {v['evidence']}\n\n"
            )

        self._reviewer_agent = AgentHarness(
            model=self.client,
            tools=self._reviewer_tools,
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            max_turns=5,
            logger=self.logger,
        )
        if self.sandbox: self._reviewer_agent.sandbox = self.sandbox
        if self.hitl: self._reviewer_agent.hitl = self.hitl
        if self.memory: self._reviewer_agent.memory = self.memory

        query = (
            f"Review the following findings and verdicts for project: {project_path}\n\n"
            f"{summary}\n\n"
            f"Produce the final analysis report. Deduplicate similar findings. "
            f"Sort by severity. Only include CONFIRMED items in the main section. "
            f"Put UNCERTAIN in a separate section. List FALSE_POSITIVE items with brief explanation.\n\n"
            f"Output as clean markdown."
        )
        return self._reviewer_agent.run(query)
