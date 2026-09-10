"""
Eval Log Parser — JSONL 运行日志 → 结构化事件流

职责：把 `AgentLogger` 写出的 JSONL（一行一事件）解析成供指标计算消费的
`RunLog`，并**显式标注采集能力**（capabilities）。

设计约束：
    - 零项目依赖（只读 JSONL，不 import harness/multi_agent）→ 可单独测试
    - 新旧日志双兼容：P0 之前的日志没有 role / tool_call_start / node_stats，
      解析不报错，缺失能力在 capabilities 中降级标记（指标层据此跳过而非算 0）
    - 容错优先：坏行跳过并计数（parser 自身绝不因脏日志抛异常）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# 视为"解析层已知"的事件名；未知事件保留在 events 中但计入 unknown_events
KNOWN_EVENTS = frozenset({
    "session_start", "session_end", "node_end", "turn_start", "turn_end",
    "tool_call_start", "tool_call_end", "parse_result", "node_stats", "error",
})


@dataclass
class RunLog:
    """一次运行（一个 JSONL 文件）的解析结果。"""

    path: Path
    session_id: str
    events: list[dict] = field(default_factory=list)
    bad_lines: int = 0
    unknown_events: int = 0

    # ─── 采集能力（缺失即降级，不由指标层猜） ───

    @property
    def has_roles(self) -> bool:
        """是否存在带 role 的事件（P0 之前恒为 False）。"""
        return any(e.get("role") for e in self.events)

    @property
    def has_tool_start(self) -> bool:
        return any(e["event"] == "tool_call_start" for e in self.events)

    @property
    def has_node_stats(self) -> bool:
        return any(e["event"] == "node_stats" for e in self.events)

    @property
    def has_parse_results(self) -> bool:
        return any(e["event"] == "parse_result" for e in self.events)

    @property
    def is_multi_agent(self) -> bool:
        """多 Agent 运行：出现 node_end（角色视图 finish）或 node_stats。"""
        return self.has_node_stats or any(e["event"] == "node_end" for e in self.events)

    # ─── 便捷视图 ───

    def of(self, event: str) -> list[dict]:
        return [e for e in self.events if e["event"] == event]

    def tool_events(self) -> list[tuple[dict | None, dict]]:
        """按 (tool_call_start, tool_call_end) 配对返回，容忍 unpaired。

        旧日志（P0 前）只有 end → start 为 None；视图 0 条 start 属预期降级。
        """
        pairs: list[tuple[dict | None, dict]] = []
        pending: list[dict] = []
        for e in self.events:
            if e["event"] == "tool_call_start":
                pending.append(e)
            elif e["event"] == "tool_call_end":
                start = pending.pop(0) if pending else None
                pairs.append((start, e))
        # 有 start 无 end（运行中途被杀）→ end 缺失，用 None 占位保留
        for start in pending:
            pairs.append((start, None))  # type: ignore[arg-type]
        return pairs

    def node_stats(self) -> dict:
        """node_stats 事件里的节点统计（无则 {}）。"""
        for e in self.of("node_stats"):
            stats = e.get("stats")
            if isinstance(stats, dict):
                return stats
        return {}

    def summary(self) -> dict:
        """运行级汇总：session_end 字段 + 事件计数（供报告快速取数）。"""
        ends = self.of("session_end")
        end = ends[-1] if ends else {}
        return {
            "session_id": self.session_id,
            "events": len(self.events),
            "errors": len(self.of("error")),
            "has_session_end": bool(ends),
            "elapsed_s": end.get("elapsed_s"),
            "complete": end.get("complete"),
            "findings": end.get("findings"),
            "fixes": end.get("fixes"),
            "fix_status": end.get("fix_status"),
            "total_tokens_used": end.get("total_tokens_used"),
        }


def parse_log_file(path: str | Path) -> RunLog:
    """解析单个 JSONL 日志文件（坏行跳过并计数，不抛异常）。"""
    path = Path(path)
    run = RunLog(path=path, session_id=path.stem)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            run.bad_lines += 1
            continue
        if not isinstance(event, dict) or "event" not in event:
            run.bad_lines += 1
            continue
        if event["event"] not in KNOWN_EVENTS:
            run.unknown_events += 1
        run.events.append(event)
    return run


def load_logs(logs_dir: str | Path = "logs") -> list[RunLog]:
    """解析目录下全部 JSONL（按文件名排序，确定性顺序）。"""
    logs_dir = Path(logs_dir)
    if not logs_dir.is_dir():
        return []
    return [parse_log_file(p) for p in sorted(logs_dir.glob("*.jsonl"))]


def invalid_json_ratio(runs: list[RunLog]) -> float:
    """坏行占比（日志完整性指标；P0 前的老日志可能被并发写入截断）。"""
    total = sum(len(r.events) + r.bad_lines for r in runs)
    if total == 0:
        return 0.0
    return sum(r.bad_lines for r in runs) / total
