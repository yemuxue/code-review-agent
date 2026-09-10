"""
Eval Metrics — 四层指标体系（轨迹 / 效率成本 / 稳定性 / 失败归因）

设计原则：
    - 纯函数：输入 RunLog 列表，输出普通 dict/数据类；零项目依赖、无 IO
    - 不猜数：数据源缺失（老日志无 tool_call_start / 无 parse_result）时
      对应指标返回 None 并计入 `coverage`（分子分母都如实给出），**不用 0 冒充**
    - 可复核：每个指标都带 n_*（样本量），报告里数值永远与样本量同时出现

指标定义见 docs/agent-evaluation-plan.md §4。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from src.eval.log_parser import RunLog

# 失败分类（§4.5）
FAILURE_KINDS = {
    "F-01": "格式解析失败",
    "F-02": "幻觉工具名",
    "F-03": "参数错误",
    "F-04": "工具执行异常",
    "F-05": "模型API故障",
    "F-06": "空转/死循环",
    "F-07": "上下文超限",
    "F-08": "状态污染",
    "F-09": "平台差异",
}

# 显式的 API 故障文案（llm_client 修复后写入的 message）
_API_FAILURE_MARKERS = ("API HTTP 429", "API HTTP 500", "API HTTP 502", "API HTTP 503",
                        "API HTTP 504", "API HTTP 529", "API connection error",
                        "API timeout/connection error", "WinError 10013")

# 栈帧兜底：项目工具（read_file/grep/list_files）不走 HTTP，错误栈里出现这些帧
# 即等价于"故障发生在 LLM 客户端的网络层"（老日志 500 字截断后仍可判定）
_API_LAYER_MARKERS = ("llm_client.py", "urlopen", "urllib\\request.py", "urllib/request.py",
                      "urllib\\error.py", "urllib.error", "http\\client.py", "http/client.py")


@dataclass
class Metric:
    """一个指标值 + 样本量（value=None 表示数据源缺失，不可算）。"""

    name: str
    value: float | None
    numerator: float = 0
    denominator: float = 0
    unit: str = "ratio"
    note: str = ""

    def display(self) -> str:
        if self.value is None:
            return f"n/a（样本不足：{self.note or '无数据源'}）"
        if self.unit == "ratio":
            return f"{self.value:.1%}（{_fmt(self.numerator)}/{_fmt(self.denominator)}）"
        if self.unit == "ms":
            return f"{self.value:.0f} ms"
        return f"{self.value:.2f}"


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def _ratio(name: str, num: float, den: float, note: str = "") -> Metric:
    """note 是"为什么算不出"的说明：有数据时清空，避免报告里出现自相矛盾的提示。"""
    if den <= 0:
        return Metric(name, None, 0, 0, note=note or "分母为 0")
    return Metric(name, num / den, num, den, note="")


# ═══ 轨迹指标（§4.2） ═══

def tool_success_rate(runs: list[RunLog]) -> Metric:
    """1 − tool_call_end.error 占比（历史日志即可算，是唯一有存量数据的轨迹指标）。"""
    ends = [e for r in runs for e in r.of("tool_call_end")]
    errors = [e for e in ends if e.get("error")]
    m = _ratio("工具调用成功率", len(ends) - len(errors), len(ends))
    if errors:
        kinds = Counter(e.get("failure_kind") or "legacy(无 failure_kind，归因见 §4)" for e in errors)
        m.note = "失败构成: " + ", ".join(f"{k}×{v}" for k, v in kinds.most_common())
    return m


def args_valid_rate(runs: list[RunLog]) -> Metric:
    """schema 校验通过率（依赖 P0：tool_call_start.args_ok；老日志无此字段）。"""
    starts = [s for r in runs for s, _ in r.tool_events() if s and "args_ok" in s]
    valid = [s for s in starts if s.get("args_ok")]
    return _ratio("参数合法率", len(valid), len(starts),
                  note="需 P0 后新日志（tool_call_start.args_ok）")


def plan_parse_rate(runs: list[RunLog]) -> Metric:
    """planner 结构化输出可解析率（parse_result kind=findings）。"""
    return _parse_rate(runs, "planner", "findings", "Plan 可执行率")


def verdict_parse_rate(runs: list[RunLog]) -> Metric:
    """VERDICT 解析率（parse_result kind=verdict，由 execute 节点产出）。"""
    return _parse_rate(runs, "executor", "verdict", "VERDICT 解析率")


def fix_parse_rate(runs: list[RunLog]) -> Metric:
    return _parse_rate(runs, "fixer", "fix", "fix 输出解析率")


def _parse_rate(runs: list[RunLog], role: str, kind: str, label: str) -> Metric:
    rows = [e for r in runs for e in r.of("parse_result")
            if e.get("role") == role and e.get("kind") == kind]
    ok = [e for e in rows if e.get("ok")]
    return _ratio(label, len(ok), len(rows), note="需 P0 后新日志（parse_result 事件）")


def marker_parse_failures(runs: list[RunLog]) -> list[dict]:
    """含协议标记但解析 0 条 → F-01 实例（真·格式失败，与"无发现"区分）。"""
    return [{"run": r.session_id, "role": e.get("role"), "kind": e.get("kind")}
            for r in runs for e in r.of("parse_result")
            if e.get("marker") and not e.get("ok")]


def _turn_records(run: RunLog) -> list[dict]:
    """线性扫描事件流，还原每次 turn 的"有无工具产出"。

    必须线性扫描而非按 turn 号分组：一个日志文件里可能有多次运行（turn 号从 1 重来），
    且旧日志没有 tool_call_start，只能用 tool_call_end 当工具产出证据。
    """
    records: list[dict] = []
    current: dict | None = None
    for e in run.events:
        name = e["event"]
        if name == "turn_start":
            current = {"role": e.get("role"), "turn": e.get("turn"), "tools": 0,
                       "has_tools_flag": None}
            records.append(current)
        elif name in ("tool_call_start", "tool_call_end"):
            if current is not None and e.get("turn") == current["turn"]:
                current["tools"] += 1
        elif name == "turn_end":
            if current is not None and e.get("turn") == current["turn"]:
                current["has_tools_flag"] = bool(e.get("has_tool_calls"))
    return records


def idle_turn_rate(runs: list[RunLog]) -> Metric:
    """空转率：无工具产出且未终结的 turn / 总 turn（F-06 的前置信号）。

    "终结轮"（模型给出最终答案的那轮）必然无工具调用，不算空转——判定依据是该 turn
    之后 turn 号是否重新开始（turn 号不增 → 上一轮就是那次运行的最后一轮）。
    """
    idle = total = 0
    for run in runs:
        records = _turn_records(run)
        for idx, rec in enumerate(records):
            total += 1
            next_turn = records[idx + 1]["turn"] if idx + 1 < len(records) else None
            terminal = next_turn is None or next_turn <= rec["turn"]
            has_tools = rec["tools"] > 0 or rec["has_tools_flag"] is True
            if not has_tools and not terminal:
                idle += 1
    return _ratio("空转率", idle, total)


def loop_rate(runs: list[RunLog], threshold: int = 3) -> Metric:
    """循环率：同一 (tool, args_fingerprint) 出现 ≥ threshold 次的运行占比。

    历史日志 tool_call_start 为 0 条（P0 前的采集缺口），分母为 0 → 返回 n/a。
    """
    looping = 0
    considered = 0
    for run in runs:
        starts = [s for s, _ in run.tool_events() if s]
        if not starts:
            continue
        considered += 1
        counts = Counter((s.get("tool"), s.get("args_fingerprint")) for s in starts)
        if any(c >= threshold for c in counts.values()):
            looping += 1
    return _ratio("循环率", looping, considered,
                  note="需 P0 后新日志（tool_call_start.args_fingerprint）")


def fix_success_rate(runs: list[RunLog]) -> Metric:
    """fix 成功率：FIXED / (FIXED + FAILED)，来自 session_end.fix_status。"""
    fixed = failed = 0
    for run in runs:
        status = run.summary().get("fix_status") or {}
        fixed += int(status.get("FIXED", 0))
        failed += int(status.get("FAILED", 0))
    return _ratio("fix 成功率", fixed, fixed + failed, note="需 auto_fix 运行")


def fix_regression_rate(runs: list[RunLog]) -> Metric:
    """回归引入率：修复导致文件损坏/未应用（ROLLED_BACK + NOT_APPLIED）占修复记录比。"""
    bad = total = 0
    for run in runs:
        status = run.summary().get("fix_status") or {}
        total += sum(int(v) for v in status.values())
        bad += int(status.get("ROLLED_BACK", 0)) + int(status.get("NOT_APPLIED", 0))
    return _ratio("回归引入率", bad, total, note="需 auto_fix 运行")


def turn_budget_exhausted_rate(runs: list[RunLog]) -> Metric:
    """turn 预算耗尽率：用满 max_turns 的节点占比（F-06 的另一面：没崩但没收敛）。

    真实运行发现：耗尽预算的节点由 `_force_finish` 兜底（额外一次 LLM 调用 +
    "Max turns reached, give your best answer now"），其产出质量下降，
    且 node_end 事件缺失——这条路径此前无任何指标覆盖。
    """
    nodes = [(name, s) for r in runs for name, s in r.node_stats().items()
             if isinstance(s, dict) and int(s.get("max_turns", 0)) > 0]
    exhausted = [(n, s) for n, s in nodes if int(s.get("turns", 0)) >= int(s["max_turns"])]
    forced = sum(len(r.of("forced_finish")) for r in runs)
    metric = _ratio("turn 预算耗尽率", len(exhausted), len(nodes),
                    note="需 node_stats.max_turns（P1 补采）")
    if exhausted:
        # 用满上限 ≠ 被强制收尾：末轮若已给出最终答案则属正常收尾
        names = ", ".join(n for n, _ in exhausted[:3]) + ("…" if len(exhausted) > 3 else "")
        metric.note = (f"{len(exhausted)} 个节点用满 turns 上限（{names}），"
                       f"其中 {forced} 次由 _force_finish 强制收尾（末轮仍在调工具）")
    return metric


def e2e_success_rate(runs: list[RunLog]) -> Metric:
    """端到端成功率：有 session_end 且无 error 事件、且未被标记 complete=False 的 run 占比。

    complete 语义：多 Agent 运行显式写入；单 Agent 路径 session_end 本身即代表正常收尾
    （只有产出最终答案时才写），故 None 视为隐式完成、False 才算失败。
    旧日志（多 Agent 未接 logger → 只有 session_start）会被计为未完成——
    这是基线报告中要显式呈现的历史采集缺口，不是模型能力指标。
    """
    ok = 0
    for run in runs:
        summary = run.summary()
        if (summary["has_session_end"] and summary["errors"] == 0
                and summary["complete"] is not False):
            ok += 1
    return _ratio("端到端成功率", ok, len(runs))


# ═══ 效率与成本指标（§4.3） ═══

def node_efficiency(runs: list[RunLog]) -> dict:
    """按节点拆解：turns/tokens 合计、耗时 p50/p95。

    无 node_stats 的 run（P0 前）不计入；返回 per_node 与 totals。
    """
    per_node: dict[str, dict] = {}
    seen_runs: dict[str, set[int]] = {}
    for idx, run in enumerate(runs):
        for name, stats in run.node_stats().items():
            if not isinstance(stats, dict):
                continue
            node = per_node.setdefault(name, {"runs": 0, "turns": 0, "tools": 0,
                                              "tokens": 0, "elapsed_ms": []})
            seen_runs.setdefault(name, set()).add(idx)
            node["turns"] += int(stats.get("turns", 0))
            node["tools"] += int(stats.get("tools", 0))
            node["tokens"] += int(stats.get("tokens", 0))
            node["elapsed_ms"].append(float(stats.get("elapsed_ms", 0)))
    for name, node in per_node.items():
        node["runs"] = len(seen_runs.get(name, ()))
        samples = sorted(node["elapsed_ms"])
        node["elapsed_p50_ms"] = round(median(samples)) if samples else 0
        node["elapsed_p95_ms"] = round(_percentile(samples, 0.95)) if samples else 0
        node["tokens_per_run"] = round(node["tokens"] / node["runs"], 1) if node["runs"] else 0
        del node["elapsed_ms"]
    return per_node


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[idx]


def parallel_efficiency(runs: list[RunLog]) -> dict:
    """Send 并行的实际收益：节点耗时合计 / 挂钟时间 = 有效并发度。

    真实运行读数（2 次运行合计，src/eval 范围）: 节点合计 352 s vs 挂钟 160 s ≈ 2.2×。
    并发度 < 1 说明存在串行等待或日志/时钟异常，值得排查。
    """
    wall = 0.0
    node_ms = 0.0
    samples = 0
    for run in runs:
        elapsed = run.summary().get("elapsed_s")
        stats = run.node_stats()
        if not elapsed or not stats:
            continue
        samples += 1
        wall += float(elapsed)
        node_ms += sum(float(s.get("elapsed_ms", 0)) for s in stats.values()
                       if isinstance(s, dict))
    return {
        "runs": samples,
        "wall_s": round(wall, 1),
        "node_total_s": round(node_ms / 1000, 1),
        "concurrency": round(node_ms / 1000 / wall, 2) if wall > 0 else None,
    }


def total_tokens(runs: list[RunLog]) -> dict:
    """全局 token 消耗（node_stats 求和；单 Agent 路径取 session_end.total_tokens_used）。"""
    total = 0
    for run in runs:
        stats = run.node_stats()
        if stats:
            total += sum(int(s.get("tokens", 0)) for s in stats.values()
                         if isinstance(s, dict))
        else:
            total += int(run.summary().get("total_tokens_used") or 0)
    return {"total_tokens": total,
            "tokens_per_run": round(total / len(runs), 1) if runs else 0,
            "runs": len(runs)}


# ═══ 失败分类（§4.5） ═══

def incomplete_runs(runs: list[RunLog]) -> list[dict]:
    """未收尾的运行（有事件但无 session_end）。

    **不计入失败分类**：这是遥测缺口/进程中断，不是模型或系统行为失败——
    混进 F-08（状态污染）会让失败分布失去意义。
    """
    return [{"run": r.session_id, "events": len(r.events)}
            for r in runs if r.events and not r.summary()["has_session_end"]]


def classify_failures(runs: list[RunLog]) -> dict[str, list[dict]]:
    """把每个失败信号归入 F-01..F-09，返回 {code: [证据]}。

    同一工具异常会同时产生 tool_call_end(error) 与 error 事件：按 (run, turn) 去重，
    避免同一个故障在报告里被计两次。
    """
    buckets: dict[str, list[dict]] = {code: [] for code in FAILURE_KINDS}

    for run in runs:
        rid = run.session_id
        tool_error_turns = {e.get("turn") for _, e in run.tool_events()
                            if e and e.get("error")}
        for _, end in run.tool_events():
            if not end or not end.get("error"):
                continue
            kind = end.get("failure_kind")
            if kind == "unknown_tool":
                buckets["F-02"].append({"run": rid, "tool": end.get("tool")})
            elif kind == "args_invalid":
                buckets["F-03"].append({"run": rid, "tool": end.get("tool"),
                                        "detail": end.get("result_preview", "")[:120]})
            elif kind == "tool_exception":
                buckets["F-04"].append({"run": rid, "tool": end.get("tool"),
                                        "detail": end.get("result_preview", "")[:120]})
            elif kind is None:
                # P0 前的老日志：只有 error 布尔位，按结果文本粗分类
                preview = str(end.get("result_preview", ""))
                if "not found" in preview:
                    buckets["F-02"].append({"run": rid, "tool": end.get("tool")})
                else:
                    buckets["F-04"].append({"run": rid, "tool": end.get("tool"),
                                            "detail": preview[:120]})

        for entry in marker_parse_failures([run]):
            buckets["F-01"].append(entry)

        for e in run.of("error"):
            if e.get("turn") in tool_error_turns:
                continue  # 已由 tool_call_end 路径计入，跳过重复
            message = str(e.get("message", ""))
            code = None
            if any(m in message for m in _API_FAILURE_MARKERS):
                code = "F-05"
            elif any(m in message for m in _API_LAYER_MARKERS):
                # 2026-09-03 前的老日志在 500 字处截断，栈尾根因（如 WinError 10013）
                # 已丢失 —— 按抛出层（LLM 客户端的 HTTP 调用）归类，并标注截断。
                code = "F-05"
            elif "context" in message.lower() and "length" in message.lower():
                code = "F-07"
            detail = message[:160].replace("\n", " ")
            if len(message) >= 500:
                detail += " …（老日志在 500 字处截断，栈尾根因不可见）"
            buckets[code or "F-04"].append({"run": rid, "error_type": e.get("error_type"),
                                            "detail": detail})

        if _has_stall(run):
            buckets["F-06"].append({"run": rid, "detail": "同一 (tool,args) 重复 ≥3 次"})

    return {code: entries for code, entries in buckets.items() if entries}


def _has_stall(run: RunLog, threshold: int = 3) -> bool:
    starts = [s for s, _ in run.tool_events() if s and s.get("args_fingerprint")]
    counts = Counter((s.get("tool"), s.get("args_fingerprint")) for s in starts)
    return any(c >= threshold for c in counts.values())


def failure_summary(buckets: dict[str, list[dict]]) -> list[tuple[str, str, int]]:
    """失败 Top 列表：(code, 名称, 次数)，按次数降序。"""
    return sorted(((code, FAILURE_KINDS[code], len(items))
                   for code, items in buckets.items()),
                  key=lambda row: (-row[2], row[0]))


# ═══ 稳定性指标（§4.4；需重复运行，P5 接线） ═══

def pass_at_k(groups: list[list[bool]], k: int | None = None) -> Metric:
    """同一用例重复 k 次全部通过的占比（groups = 每用例的 k 次通过布尔序列）。"""
    considered = [g for g in groups if g]
    if k is not None:
        considered = [g[:k] for g in considered]
    if not considered:
        return _ratio("pass@k", 0, 0, note="需 --repeat k 采集")
    all_pass = sum(1 for g in considered if all(g))
    return _ratio(f"pass@{len(considered[0])}", all_pass, len(considered))


def flaky_rate(groups: list[list[bool]]) -> Metric:
    """flaky 率：同一用例多次运行结果翻转（既非全通也非全挂）的占比。"""
    considered = [g for g in groups if len(g) > 1]
    if not considered:
        return _ratio("flaky 率", 0, 0, note="需 --repeat k 采集")
    flaky = sum(1 for g in considered if any(g) and not all(g))
    return _ratio("flaky 率", flaky, len(considered))


# ═══ 汇总入口 ═══

@dataclass
class EvalReportData:
    """一次评测（一批 run）的全部指标结果。"""

    runs: list[RunLog]
    trajectory: list[Metric] = field(default_factory=list)
    stability: list[Metric] = field(default_factory=list)
    failures: dict[str, list[dict]] = field(default_factory=dict)
    node_efficiency: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)
    parallel: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)


def compute_all(runs: list[RunLog], pass_groups: list[list[bool]] | None = None) -> EvalReportData:
    """计算全部可算指标，并如实标注数据源覆盖度。"""
    return EvalReportData(
        runs=runs,
        trajectory=[
            tool_success_rate(runs),
            args_valid_rate(runs),
            plan_parse_rate(runs),
            verdict_parse_rate(runs),
            fix_parse_rate(runs),
            idle_turn_rate(runs),
            loop_rate(runs),
            fix_success_rate(runs),
            fix_regression_rate(runs),
            turn_budget_exhausted_rate(runs),
            e2e_success_rate(runs),
        ],
        stability=[pass_at_k(pass_groups or []), flaky_rate(pass_groups or [])],
        failures=classify_failures(runs),
        node_efficiency=node_efficiency(runs),
        tokens=total_tokens(runs),
        parallel=parallel_efficiency(runs),
        coverage={
            "runs": len(runs),
            "with_roles": sum(1 for r in runs if r.has_roles),
            "with_tool_start": sum(1 for r in runs if r.has_tool_start),
            "with_node_stats": sum(1 for r in runs if r.has_node_stats),
            "with_parse_results": sum(1 for r in runs if r.has_parse_results),
            "multi_agent": sum(1 for r in runs if r.is_multi_agent),
            "bad_lines": sum(r.bad_lines for r in runs),
            "incomplete": len(incomplete_runs(runs)),
        },
    )
