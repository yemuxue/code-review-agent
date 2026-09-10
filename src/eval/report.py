"""
Eval Report — 评测报告生成器（markdown）

用法：
    python -m src.eval.report                       # 扫 logs/ → reports/eval_baseline_<date>.md
    python -m src.eval.report --logs logs --out x.md

报告只陈述"测到了什么、哪些测不了"，不做美化：数据源缺失的指标写 n/a 并给出原因。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.eval.log_parser import RunLog, invalid_json_ratio, load_logs
from src.eval.metrics import (
    EvalReportData,
    Metric,
    compute_all,
    failure_summary,
)

# §4.1/§4.2 目标值（基线报告用于对照，尚未作为门禁——门禁在 Phase 4）
TARGETS = {
    "工具调用成功率": (0.98, "≥"),
    "Plan 可执行率": (0.95, "≥"),
    "VERDICT 解析率": (0.95, "≥"),
    "fix 成功率": (0.70, "≥"),
    "空转率": (0.10, "≤"),
    "循环率": (0.0, "≤"),
    "端到端成功率": (0.90, "≥"),
}


def _verdict(metric: Metric) -> str:
    target = TARGETS.get(metric.name)
    if metric.value is None or target is None:
        return "—"
    limit, direction = target
    ok = metric.value >= limit if direction == "≥" else metric.value <= limit
    return "✅ 达标" if ok else "❌ 未达标"


def _metric_table(metrics: list[Metric]) -> list[str]:
    lines = ["| 指标 | 数值 | 样本量 | 目标 | 状态 | 备注 |",
             "|------|------|--------|------|------|------|"]
    for m in metrics:
        target = TARGETS.get(m.name)
        target_text = f"{target[1]} {target[0]:.0%}" if target else "建立基线"
        n = "—" if m.value is None else f"{int(m.denominator)}"
        lines.append(f"| {m.name} | {m.display()} | {n} | {target_text} "
                     f"| {_verdict(m)} | {m.note or ''} |")
    return lines


def render_report(data: EvalReportData, source: str, generated_at: datetime | None = None) -> str:
    """渲染完整基线报告 markdown。"""
    generated_at = generated_at or datetime.now()
    runs = data.runs
    cov = data.coverage

    lines: list[str] = [
        "# Agent 评测基线报告",
        "",
        f"- **生成时间**: {generated_at.strftime('%Y-%m-%d %H:%M')}",
        f"- **数据来源**: `{source}`（{cov['runs']} 个运行日志）",
        "- **评测体系版本**: v1（Phase 1 指标库；故障注入用例集为 Phase 2，尚未接入）",
        f"- **日志坏行率**: {invalid_json_ratio(runs):.2%}（{cov['bad_lines']} 行）",
        "",
        "> 本报告是**基线**：只统计现有运行日志能算出的指标。数据源缺失的指标明确标注",
        "> n/a 并给出原因，不用 0 冒充——这也是 Phase 0 遥测补全要解决的问题。",
        "",
        "## 1. 数据源覆盖度",
        "",
        "| 采集能力 | 覆盖运行数 | 占比 |",
        "|----------|-----------|------|",
        f"| 带角色归因（role） | {cov['with_roles']} | {_pct(cov['with_roles'], cov['runs'])} |",
        f"| tool_call_start（工具调用起点） | {cov['with_tool_start']} | {_pct(cov['with_tool_start'], cov['runs'])} |",
        f"| node_stats（节点统计） | {cov['with_node_stats']} | {_pct(cov['with_node_stats'], cov['runs'])} |",
        f"| parse_result（结构化解析埋点） | {cov['with_parse_results']} | {_pct(cov['with_parse_results'], cov['runs'])} |",
        f"| 多 Agent 完整运行 | {cov['multi_agent']} | {_pct(cov['multi_agent'], cov['runs'])} |",
        f"| 运行已收尾（有 session_end） | {cov['runs'] - cov['incomplete']} "
        f"| {_pct(cov['runs'] - cov['incomplete'], cov['runs'])} |",
        "",
        f"未收尾运行 {cov['incomplete']} 个（进程中断或 Phase 0 前多 Agent 未接 logger）。"
        "此项属**采集缺口**，不计入失败分类——否则会把日志问题伪装成模型失败。",
        "",
        "## 2. 轨迹指标（JD 核心）",
        "",
        *_metric_table(data.trajectory),
        "",
        "## 3. 效率与成本",
        "",
    ]

    if data.node_efficiency:
        lines += ["| 节点 | 运行数 | turns 合计 | tools 合计 | tokens/次 | 耗时 p50 | 耗时 p95 |",
                  "|------|--------|-----------|-----------|----------|----------|----------|"]
        for name, node in sorted(data.node_efficiency.items()):
            lines.append(f"| {name} | {node['runs']} | {node['turns']} | {node['tools']} "
                         f"| {node['tokens_per_run']} | {node['elapsed_p50_ms']} ms "
                         f"| {node['elapsed_p95_ms']} ms |")
        lines.append("")
    else:
        lines += ["无 node_stats 数据（Phase 0 前的运行未持久化节点统计）。", ""]

    lines += [
        f"- **总 token**: {data.tokens['total_tokens']}（{data.tokens['runs']} 次运行，"
        f"平均 {data.tokens['tokens_per_run']}/次）",
        "- **成本/任务**: 未启用（需 `src/eval/pricing.py` 单价表，Phase 5）",
        "",
        "## 4. 失败分类（F-01..F-09）",
        "",
    ]

    summary = failure_summary(data.failures)
    if summary:
        lines += ["| 代码 | 类型 | 次数 |", "|------|------|------|"]
        for code, name, count in summary:
            lines.append(f"| {code} | {name} | {count} |")
        lines.append("")
        for code, name, _ in summary:
            samples = data.failures[code][:5]
            lines.append(f"### {code} {name}（{len(data.failures[code])} 次）")
            for s in samples:
                lines.append(f"- `{s.get('run', '?')}` " +
                             ", ".join(f"{k}={v}" for k, v in s.items() if k != "run"))
            if len(data.failures[code]) > len(samples):
                lines.append(f"- …另有 {len(data.failures[code]) - len(samples)} 条同类记录")
            lines.append("")
    else:
        lines += ["本批运行未检出分类失败。", ""]

    lines += [
        "## 5. 稳定性",
        "",
        *_metric_table(data.stability),
        "",
        "## 6. 结论与下一步",
        "",
    ]
    lines += _conclusions(data)
    lines += [
        "",
        "---",
        "",
        "**指标定义**: [docs/agent-evaluation-plan.md](../../docs/agent-evaluation-plan.md) §4 ｜ "
        "**失败分类**: 同上 §4.5",
        "",
    ]
    return "\n".join(lines)


def _conclusions(data: EvalReportData) -> list[str]:
    usable = [m for m in data.trajectory if m.value is not None]
    missing = [m.name for m in data.trajectory if m.value is None]
    lines = [f"- 可算指标 {len(usable)}/{len(data.trajectory)}"
             f"（数据源缺失：{('、'.join(missing)) if missing else '无'}）。"]
    failed = [m.name for m in usable if _verdict(m).startswith("❌")]
    if failed:
        lines.append(f"- 未达标指标：{('、'.join(failed))} —— 需在 Phase 2 用故障注入用例"
                     "验证指标是否真能抓到对应故障，再决定修复优先级。")
    else:
        lines.append("- 已可算的指标均达标或无需达标（循环率/回归引入率目标为建立基线）。")
    top = failure_summary(data.failures)
    if top:
        lines.append(f"- 失败集中在 {top[0][0]} {top[0][1]}（{top[0][2]} 次），"
                     "对应 Phase 2 故障注入用例优先覆盖该路径。")
    lines.append("- **下一步（Phase 2）**: 为每个指标补一条故障注入用例，证明指标真能抓住故障。")
    return lines


def _pct(num: int, den: int) -> str:
    return f"{num / den:.1%}" if den else "—"


def write_report(markdown: str, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Agent 评测报告（默认扫 logs/）")
    parser.add_argument("--logs", default="logs", help="JSONL 日志目录")
    parser.add_argument("--out", default=None, help="输出 markdown 路径")
    args = parser.parse_args(argv)

    runs: list[RunLog] = load_logs(args.logs)
    if not runs:
        print(f"[eval] 未在 {args.logs} 找到 *.jsonl", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else Path("reports") / f"eval_baseline_{datetime.now():%Y%m%d}.md"
    data = compute_all(runs)
    path = write_report(render_report(data, source=args.logs), out)
    print(f"[eval] {len(runs)} 个运行 → {path}")
    for metric in data.trajectory:
        print(f"  {metric.name}: {metric.display()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
