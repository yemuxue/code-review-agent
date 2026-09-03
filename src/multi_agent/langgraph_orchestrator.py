"""LangGraph Orchestrator — imports injected at init to avoid node context issues

架构: plan → Send(fan-out execute_one × N 并行) → review → fix → END

升级点:
  1. Send API: Executor 按 finding 分片并行执行
  2. node_stats: 每节点记录 turns/tokens/耗时（可观测性）
  3. fix 节点: 对 CONFIRMED findings 调用 write_file 修复代码
"""
from __future__ import annotations
import sys
import json
import time
import hashlib
import shlex
import subprocess
from dataclasses import replace
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from pathlib import Path as _Path

from typing import TypedDict, Annotated, Sequence
import operator
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from src.skills import Skill, load_skills, build_role_blocks


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

    def __init__(self, llm_client, tools: list, sandbox=None, hitl=None, memory=None,
                 auto_fix: bool = False, skills_dir=None):
        self.client = llm_client
        self.tools = tools
        self.sandbox = sandbox
        self.hitl = hitl
        self.memory = memory
        self.auto_fix = auto_fix
        self._project_root: _Path | None = None
        # receipt 只存活于单次 run：历史 .bak 不能作为本轮写入成功的证据。
        self._write_receipts: dict[str, dict[str, str | float]] = {}
        # 技能：构造时加载一次（skills_dir=None → 仓库根默认 skills/）；命中结果每次 run() 按 task 重算。
        self.skills: tuple[Skill, ...] = tuple(load_skills(skills_dir))
        self._role_blocks: dict[str, str] = {}
        from src.harness.agent import AgentHarness
        from src.multi_agent.agents import PLANNER_SYSTEM_PROMPT, EXECUTOR_SYSTEM_PROMPT, REVIEWER_SYSTEM_PROMPT, FIXER_SYSTEM_PROMPT
        self.AgentHarness = AgentHarness
        self.PLANNER_PROMPT = PLANNER_SYSTEM_PROMPT
        self.EXECUTOR_PROMPT = EXECUTOR_SYSTEM_PROMPT
        self.REVIEWER_PROMPT = REVIEWER_SYSTEM_PROMPT
        self.FIXER_PROMPT = FIXER_SYSTEM_PROMPT
        self.graph = self._build_graph()

    # ═══ 共享工厂 ═══
    def _tools_for_names(self, tool_names: list[str]) -> list:
        """按名称取工具，并只为 Fixer 绑定本次任务的写入根目录。"""
        selected = [tool for tool in self.tools if tool.name in tool_names]
        return [self._scoped_write_tool(tool) if tool.name == "write_file" else tool
                for tool in selected]

    def _scoped_write_tool(self, tool):
        """把项目根目录闭包绑定到写入工具，避免模型伪造 allowed_root。"""
        project_root = self._project_root

        def scoped_write_file(file_path: str = "", content: str = "",
                              start_line: int = 1) -> str:
            if project_root is None:
                return "ERROR: REFUSED — write_file has no active project root."
            target = _Path(file_path).resolve(strict=False)
            try:
                before = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                before = ""
            result = tool.fn(file_path=file_path, content=content, start_line=start_line,
                             allowed_root=str(project_root))
            if result.startswith("OK:"):
                try:
                    after = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return result
                self._write_receipts[str(target)] = {
                    "before_sha256": self._text_sha256(before),
                    "after_sha256": self._text_sha256(after),
                    "backup_path": str(target.with_suffix(target.suffix + ".bak")),
                    "written_at": time.time(),
                }
            return result

        return replace(tool, fn=scoped_write_file)

    @staticmethod
    def _text_sha256(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _has_current_write_receipt(self, file_path: str) -> bool:
        """确认目标仍是本轮成功写入后的版本，避免后续覆盖导致误判。"""
        path = _Path(file_path).resolve(strict=False)
        receipt = self._write_receipts.get(str(path))
        if not receipt:
            return False
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return self._text_sha256(content) == receipt["after_sha256"]

    def _run_behavior_verification(self, command: str) -> tuple[bool, str]:
        """只运行项目内的受限 pytest，用于证明修复后的目标行为。"""
        if self._project_root is None:
            return False, "没有活动项目根目录，未执行行为验证"
        try:
            args = shlex.split(command, posix=False)
        except ValueError as exc:
            return False, f"验证命令无法解析：{exc}"
        # 仅允许 PATH 中的 pytest，不能让模型用同名绝对路径替换可执行文件。
        if not args or args[0].lower() != "pytest":
            return False, "验证命令仅允许 pytest"

        allowed_options = {"-q", "-v", "--disable-warnings", "--tb=short"}
        for arg in args[1:]:
            if arg in allowed_options:
                continue
            if arg.startswith("-"):
                return False, f"不允许的 pytest 参数：{arg}"
            test_path = _Path(arg.split("::", 1)[0])
            candidate = (self._project_root / test_path).resolve(strict=False)
            try:
                candidate.relative_to(self._project_root)
            except ValueError:
                return False, f"测试路径超出项目根目录：{arg}"
            if not candidate.is_file():
                return False, f"测试文件不存在：{arg}"

        try:
            completed = subprocess.run(
                args, cwd=self._project_root, capture_output=True, text=True,
                timeout=60, check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "行为验证超时（60 秒）"
        except OSError as exc:
            return False, f"行为验证无法启动：{type(exc).__name__}: {exc}"
        if completed.returncode == 0:
            return True, "pytest 通过"
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return False, f"pytest 失败（exit {completed.returncode}）：{detail[-1] if detail else '无输出'}"

    def _make_agent(self, tool_names: list[str], system_prompt: str, max_turns: int,
                    role: str | None = None):
        """统一创建 Agent，注入 sandbox/hitl/memory

        role（planner/executor/reviewer/fixer）命中技能时，把追加块接在基础 prompt
        之后。role=None 或 run() 之外调用（_role_blocks 为空）→ 零注入，与改造前一致。
        """
        tools = self._tools_for_names(tool_names)
        extra = self._role_blocks.get(role) if role else None
        if extra:
            system_prompt = system_prompt + extra
        agent = self.AgentHarness(model=self.client, tools=tools, system_prompt=system_prompt, max_turns=max_turns)
        if self.sandbox:
            agent.sandbox = self.sandbox
        if self.hitl:
            agent.hitl = self.hitl
        if self.memory:
            agent.memory = self.memory
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
        try:
            self._project_root = _Path(project_path).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("project_path must be an existing directory") from None
        if not self._project_root.is_dir():
            raise ValueError("project_path must be an existing directory")
        self._project_path = str(self._project_root)  # 供 Send fan-out 使用
        self._write_receipts = {}
        # 技能按本轮 task 匹配（Send 并行子状态不含 task 文本，只能在此算一次）。
        # 无技能/无匹配 → {} → 各角色 prompt 与改造前逐字节一致。
        self._role_blocks = build_role_blocks(self.skills, task)
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
        # 必须在处理 finding 前终止，确保默认仅审查流程没有任何写入副作用。
        if not self.auto_fix:
            return [END]

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
        # role 取值对应 AGENT_DEFINITIONS 的键（planner/executor/reviewer/fixer），
        # 命中技能时 _make_agent 按角色把技能正文追加进 system prompt。
        agent = self._make_agent(["list_files", "read_file", "grep_pattern"],
                                 self.PLANNER_PROMPT, max_turns=8, role="planner")
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
                                 self.EXECUTOR_PROMPT, max_turns=4, role="executor")
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
                                 self.REVIEWER_PROMPT, max_turns=5, role="reviewer")
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
                                 self.FIXER_PROMPT, max_turns=10, role="fixer")
        result = agent.run(
            "Fix ALL " + str(len(group)) + " findings in " + file_path + ":\n" +
            json.dumps(group, indent=2, ensure_ascii=False) +
            "\n\nEFFICIENCY: read the ENTIRE file ONCE, then write_file ONCE with the complete fixed content.\n" +
            "Then output status lines: FIXED|finding_id|file_path|EN summary|中文摘要\n" +
            "If a fix is not possible: FAILED|finding_id|file_path|EN reason|中文原因\n" +
            "IMPORTANT: This is the ONLY agent editing this file. One read + one write is enough for all fixes."
        )
        fixes = self._parse_fixes(result)
        verify_commands = self._parse_behavior_verifications(result)
        for fix in fixes:
            command = verify_commands.get(fix["finding_id"])
            if command:
                fix["verify_command"] = command
        return {
            "messages": [AIMessage(content=result)],
            "fixes": fixes,
            # 注意：不写 phase——并行节点同时写普通字段会触发 InvalidUpdateError
            "node_stats": self._node_stats(agent, f"fix_{_Path(file_path).stem}", start),
        }

    def _verify_fix_node(self, state: AgentState) -> dict:
        """修复审核节点：检查 fix 是否真正修复了 bug 且没有破坏代码

        验证内容（管线加固 v2）:
        1. 完整性检查覆盖**所有**被 fixer 触碰的文件（含报告 FAILED 的——
           截断事故就发生在 FAILED 文件上：jwt_auth.py 被截断后仍报 4 个 FAILED）
        2. 语法检查 + 与备份对比（尺寸比例 + 关键符号），拦截"截断后语法
           仍合法"的假成功
        3. 检测到损坏 → **自动从 .bak 回滚**，追加 ROLLED_BACK 状态条目，
           保证修复失败不会破坏代码库
        """
        start = time.time()
        fixes = state.get("fixes", [])
        from src.tools.git_tools import verify_file_integrity, restore_from_backup

        # 覆盖所有修复目标文件（FIXED 和 FAILED 的 file_path 都检查）
        check_files = sorted({f.get("file_path", "") for f in fixes if f.get("file_path")})

        lines = []
        rollback_entries = []  # 追加到 fixes（operator.add 拼接，不改原条目）
        not_applied_entries = []
        applied_entries = []
        verified_entries = []
        invalid_files = set()
        for fp in check_files:
            issues = verify_file_integrity(fp)
            if not issues:
                lines.append(f"- [OK] {_Path(fp).name}: syntax valid, integrity intact")
            else:
                lines.append(f"- [FAIL] {_Path(fp).name}: "
                             + "; ".join(issues) + " → restoring from backup")
                restore_from_backup(fp)
                invalid_files.add(str(_Path(fp).resolve(strict=False)))
                rollback_entries.append({
                    "finding_id": 0,
                    "file_path": fp,
                    "summary": f"Rolled back to backup — fix damaged the file ({issues[0]})",
                    "summary_cn": f"已从备份回滚 — 修复过程损坏了文件（{issues[0]}）",
                    "status": "ROLLED_BACK",
                })

        for f in fixes:
            if f.get("status") != "FIXED":
                continue
            fp = f.get("file_path", "")
            normalized = str(_Path(fp).resolve(strict=False))
            if not self._has_current_write_receipt(fp):
                not_applied_entries.append({
                    "finding_id": f.get("finding_id", 0),
                    "file_path": fp,
                    "summary": "Claimed FIXED but this run has no matching write receipt "
                               "— fix not applied",
                    "summary_cn": "声称 FIXED 但本轮没有匹配的写入凭据 — 修复未应用",
                    "status": "NOT_APPLIED",
                })
            elif normalized not in invalid_files:
                applied = {
                    "finding_id": f.get("finding_id", 0),
                    "file_path": fp,
                    "summary": "Write receipt and integrity checks passed — fix applied",
                    "summary_cn": "写入凭据与完整性检查均通过 — 修复已应用",
                    "status": "APPLIED",
                }
                applied_entries.append(applied)
                command = f.get("verify_command")
                if command:
                    passed, detail = self._run_behavior_verification(command)
                    if passed:
                        verified_entries.append({
                            "finding_id": f.get("finding_id", 0),
                            "file_path": fp,
                            "summary": f"Behavior verification passed: {command}",
                            "summary_cn": f"行为验证通过：{command}",
                            "status": "VERIFIED",
                        })
                    else:
                        lines.append(f"- [APPLIED] {_Path(fp).name}: {detail}")

        result = "## Fix Verification Report\n\n"
        if not fixes:
            result += "No fixes were applied."
        else:
            n_fixed = sum(1 for f in fixes if f.get("status") == "FIXED")
            n_failed = sum(1 for f in fixes if f.get("status") == "FAILED")
            n_rolled = len(rollback_entries)
            n_na = len(not_applied_entries)
            n_applied = len(applied_entries)
            n_verified = len(verified_entries)
            result += (f"Fixed: {n_fixed} | Failed: {n_failed}"
                       + (f" | Not applied: {n_na}" if n_na else "")
                       + (f" | Applied: {n_applied}" if n_applied else "")
                       + (f" | Verified: {n_verified}" if n_verified else "")
                       + (f" | Rolled back: {n_rolled}" if n_rolled else "") + "\n\n")
            if lines:
                result += "### Integrity & Syntax Check\n" + "\n".join(f"- {l}" for l in lines) + "\n\n"
            if not_applied_entries:
                _na_files = sorted({e["file_path"] for e in not_applied_entries})
                result += ("### Fix Claim Check\n"
                           + "\n".join(f"- [NOT_APPLIED] {fp}: claimed FIXED but no write "
                                       f"was persisted (write_file blocked/errored, no .bak created)"
                                       for fp in _na_files)
                           + "\n\n")

        return {
            "messages": [AIMessage(content=result)],
            "fixes": rollback_entries + not_applied_entries + applied_entries + verified_entries,
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
        """解析 fix 输出。容错：支持 markdown 代码块内、'FIXED:' 冒号、'- ' 列表前缀
        格式: FIXED|id|path|EN summary|CN summary（中英文双语）"""
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
                        "summary_cn": parts[4].strip() if len(parts) > 4 else "",
                        "status": status,
                    })
        return fixes

    def _parse_behavior_verifications(self, text: str) -> dict[int, str]:
        """解析 Fixer 为单个 finding 指定的行为测试命令。"""
        commands: dict[int, str] = {}
        for raw_line in text.split("\n"):
            line = raw_line.strip().removeprefix("- ").strip()
            if not line.startswith("VERIFY|"):
                continue
            parts = line.split("|", 2)
            if len(parts) != 3 or not parts[1].strip().isdigit():
                continue
            commands.setdefault(int(parts[1].strip()), parts[2].strip())
        return commands

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
