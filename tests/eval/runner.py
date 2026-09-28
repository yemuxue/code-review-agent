"""评测执行入口。

离线模式不访问网络；replay/full 必须显式 ``--allow-live``，防止 CI 意外产生 API 成本。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.eval.log_parser import load_logs
from src.eval.metrics import compute_all
from src.eval.pricing import set_model_price
from src.eval.report import write_report
from tests.eval.faults import FaultResult, run_all_faults, run_healthy_case
from tests.eval.report import render_evaluation_report


def _metric_value(data, name: str) -> float:
    for metric in data.trajectory:
        if metric.name == name and metric.value is not None:
            return metric.value
    return 0.0


def run_offline(work_dir: str | Path, repeat: int = 1, model: str | None = None) -> tuple[list[FaultResult], object, dict]:
    """运行故障集与独立健康链路，返回报告数据及不被注入污染的门禁指标。"""
    # 不清理调用方给出的目录：评测命令不应因参数失误覆盖已有文件。
    root = Path(work_dir) / f"run_{time.time_ns()}"
    root.mkdir(parents=True, exist_ok=False)
    results: list[FaultResult] = []
    pass_groups: dict[str, list[bool]] = defaultdict(list)
    all_runs = []
    for index in range(repeat):
        run_dir = root / f"run_{index + 1}"
        current = run_all_faults(run_dir / "faults")
        results.extend(current)
        for result in current:
            pass_groups[result.case_id].append(result.passed)
        for case_dir in (run_dir / "faults").iterdir():
            all_runs.extend(load_logs(case_dir))
    health_dir = root / "health"
    run_healthy_case(health_dir)
    health_data = compute_all(load_logs(health_dir))
    data = compute_all(all_runs, list(pass_groups.values()), model=model)
    tool_success = _metric_value(health_data, "工具调用成功率")
    e2e_success = _metric_value(health_data, "端到端成功率")
    gate = {"tool_success_rate": tool_success, "e2e_success_rate": e2e_success,
            "passed": tool_success >= 0.98 and e2e_success >= 0.90}
    return results, data, gate


def _run_replay(target_dir: str, samples: int) -> dict:
    """复用既有逐样本真实 API 评估；调用方必须先显式确认 live 模式。"""
    from tests.eval_llm_agent_qa import run_per_sample_eval
    return run_per_sample_eval(target_dir=target_dir, max_samples=samples)


def _write_replay_artifact(result: dict, report_path: Path | None) -> Path:
    """持久化真实评测汇总与逐样本明细，避免终端输出丢失后无法复核结论。"""
    base = report_path.with_suffix("") if report_path else Path("reports") / (
        f"eval_replay_{datetime.now():%Y%m%d}")
    artifact = base.with_name(base.name + "_replay.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def _append_replay_report(report_path: Path, result: dict, artifact: Path, target_dir: str) -> None:
    """将真实结果附加到 full 报告，让离线与真实阶段在同一产物中可审计。"""
    lines = ["", "## 9. 真实 API Replay", "",
             f"- 目标项目: `{target_dir}`",
             f"- 已扫描样本: {result['scanned']}/{result['total']}",
             f"- Precision: {result['precision']}",
             f"- Recall: {result['recall']}",
             f"- F1: {result['f1_score']}",
             f"- 混淆矩阵: TP={result['true_positives']}、FP={result['false_positives']}、"
             f"FN={result['false_negatives']}、TN={result['true_negatives']}",
             f"- 明细产物: `{artifact}`", ""]
    with report_path.open("a", encoding="utf-8") as report:
        report.write("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent 评测 runner")
    parser.add_argument("--suite", choices=("offline", "replay", "full"), default="offline")
    parser.add_argument("--repeat", type=int, default=1, help="每条离线用例的重复次数")
    parser.add_argument("--report", nargs="?", const="", default=None, help="写入 Markdown 报告")
    parser.add_argument("--work-dir", default=".eval-runs", help="离线日志临时目录")
    parser.add_argument("--gate", action="store_true", help="校验健康链路的 CI 阈值")
    parser.add_argument("--allow-live", action="store_true", help="允许 replay/full 调用真实 API")
    parser.add_argument("--target-dir", default=".", help="replay 的被评测项目目录")
    parser.add_argument("--samples", type=int, default=20, help="replay 默认抽样数量")
    parser.add_argument("--model", default=None, help="成本核算对应的模型标识")
    parser.add_argument("--input-price-per-million", type=float, default=None,
                        help="模型输入价格（USD/百万 token）")
    parser.add_argument("--output-price-per-million", type=float, default=None,
                        help="模型输出价格（USD/百万 token）")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat 必须大于等于 1")
    prices = (args.input_price_per_million, args.output_price_per_million)
    if any(price is not None for price in prices):
        if not args.model or any(price is None for price in prices):
            parser.error("成本核算需同时提供 --model、输入和输出单价")
        set_model_price(args.model, *prices)
    if args.suite in {"replay", "full"} and not args.allow_live:
        parser.error("replay/full 会调用真实 API，必须显式传入 --allow-live")

    offline_results: list[FaultResult] = []
    gate: dict = {"tool_success_rate": 0.0, "e2e_success_rate": 0.0, "passed": False}
    report_path: Path | None = None
    if args.suite in {"offline", "full"}:
        offline_results, data, gate = run_offline(args.work_dir, args.repeat, args.model)
        failed = [result for result in offline_results if not result.passed]
        print(f"[eval] offline: {len(offline_results) - len(failed)}/{len(offline_results)} 条断言通过")
        if args.report is not None:
            report_path = (Path("reports") / f"eval_offline_{datetime.now():%Y%m%d}.md"
                           if args.report == "" else Path(args.report))
            write_report(render_evaluation_report(data, offline_results, args.repeat, gate), report_path)
            print(f"[eval] report: {report_path}")
        if failed:
            return 1
        if args.gate and not gate["passed"]:
            print("[eval] gate failed: 工具成功率需 >=98%，端到端成功率需 >=90%", file=sys.stderr)
            return 1

    if args.suite in {"replay", "full"}:
        replay = _run_replay(args.target_dir, args.samples)
        artifact = _write_replay_artifact(replay, report_path)
        if report_path:
            _append_replay_report(report_path, replay, artifact, args.target_dir)
        print(f"[eval] replay detail: {artifact}")
        print("[eval] replay: " + json.dumps({k: v for k, v in replay.items() if k != "details"},
                                                ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
