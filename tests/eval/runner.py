"""评测执行入口。

离线模式不访问网络；replay/full 必须显式 ``--allow-live``，防止 CI 意外产生 API 成本。

门禁（--gate）由三部分组成，全部落在报告里可审计：
    1. 指标目标值：直接消费报告层的 TARGETS（单一阈值来源），必需指标无样本即失败；
    2. 基线回归：与 --baseline 快照比对，任一指标变差或失败计数增加即失败；
    3. 健康链路：工具成功率 ≥98%、端到端成功率 ≥90%。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.eval.baseline import (
    RegressionResult,
    compare_to_baseline,
    evaluate_targets,
    gate_passed,
    load_baseline,
    metrics_snapshot,
    write_baseline,
)
from src.eval.log_parser import load_logs
from src.eval.metrics import compute_all
from src.eval.pricing import set_model_price
from src.eval.report import TARGETS, write_report
from tests.eval.faults import FaultResult, run_all_faults, run_healthy_case
from tests.eval.report import render_evaluation_report


def _metric_value(data, name: str) -> float:
    for metric in data.trajectory:
        if metric.name == name and metric.value is not None:
            return metric.value
    return 0.0


def run_offline(work_dir: str | Path, repeat: int = 1, model: str | None = None) -> tuple[list[FaultResult], object, dict, object]:
    """运行故障集与独立健康链路，返回报告数据及不被注入污染的门禁指标。

    返回值第 4 项是健康链路的 EvalReportData —— 指标门禁必须在**健康链路**上判定，
    否则故障注入的预期失败会把门禁拉红（这正是此前门禁只看两个指标的原因之一）。
    """
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
    return results, data, gate, health_data


def _run_replay(target_dir: str, samples: int, judge: str = "live",
                dataset: str = "external", sample_delay_s: float = 0.0,
                sample_retries: int = 0, circuit_breaker: int = 8) -> dict:
    """逐样本评估；可注入固定判分器做离线管道验证。

    judge="live" 需调用方显式确认 live 模式；
    judge="stub-yes"/"stub-no" 完全不触网，用于验证评测管道本身
    （文件解析/上下文/计分/元数据），**不代表模型质量**。

    dataset="external"（124 条外部项目标注）或 "self"（33 条本仓自评）。
    sample_delay_s 是样本间最小间隔：真实网关在连续高频请求下会返回大量
    502，不限速会让整批读数因 ERROR 而不可用。
    """
    from tests.eval_llm_agent_qa import run_per_sample_eval, run_self_eval

    override = None
    if judge == "stub-yes":
        override = lambda *_: True    # noqa: E731  全部判为有问题
    elif judge == "stub-no":
        override = lambda *_: False   # noqa: E731  全部判为无问题

    if dataset == "self":
        return run_self_eval(target_dir=target_dir, max_samples=samples,
                             judge_override=override, sample_delay_s=sample_delay_s,
                             sample_retries=sample_retries,
                             circuit_breaker=circuit_breaker)
    return run_per_sample_eval(target_dir=target_dir, max_samples=samples,
                               judge_override=override, sample_delay_s=sample_delay_s,
                               sample_retries=sample_retries,
                             circuit_breaker=circuit_breaker)


def _write_replay_artifact(result: dict, report_path: Path | None) -> Path:
    """持久化真实评测汇总与逐样本明细，避免终端输出丢失后无法复核结论。"""
    base = report_path.with_suffix("") if report_path else Path("reports") / (
        f"eval_replay_{datetime.now():%Y%m%d}")
    artifact = base.with_name(base.name + "_replay.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifact


def _append_replay_report(report_path: Path, result: dict, artifact: Path, target_dir: str) -> None:
    """将真实结果附加到报告，让离线与真实阶段在同一产物中可审计。

    replay-only 运行时报告文件还不存在（offline 分支没跑），
    因此这里必须在追加前补一个标题——否则 `--report` 的产物只有一段
    没有上下文的片段，或者直接因父目录不存在而失败。
    """
    report_path.parent.mkdir(parents=True, exist_ok=True)
    meta = result.get("meta") or {}
    quality_claim = str(meta.get("quality_claim") or "")
    # 标题必须与真实口径一致：桩判分器的产物若被标成"真实评测报告"，
    # 后续读者会把它当成模型质量读数（本轮已经出现过这种误读风险）。
    is_real = "不代表模型质量" not in quality_claim
    title = "# Agent 真实评测报告（replay）" if is_real else "# Agent 评测管道验证报告（离线桩）"
    caveat = ("- 本报告来自**真实模型调用**；样本量与数据集版本见下表，"
              "跨版本读数不可直接比较。"
              if is_real else
              "- ⚠️ 本报告使用**固定判分器离线运行**，只验证评测管道"
              "（文件解析/上下文/计分/元数据）是否可用，**不代表模型质量**。")
    header = [] if report_path.exists() else [
        title, "", caveat,
        "", "## 1. 运行元数据", "",
        "| 项 | 值 |", "|----|----|",
        f"| 数据集 | {meta.get('dataset_id')} v{meta.get('dataset_version')} |",
        f"| 模型 | {meta.get('model')} |",
        f"| 温度 | {meta.get('temperature')} |",
        f"| 判分方式 | {meta.get('scoring')} |",
        f"| 质量声明 | {quality_claim} |",
        f"| 运行时间 | {meta.get('run_at')} |",
        f"| 目标项目 | `{meta.get('target_dir')}` |",
        f"| 健康判分 | {meta.get('healthy')}（跳过 {meta.get('skipped', 0)}、"
        f"异常 {meta.get('errored', 0)}） |", "",
    ]
    lines = header + ["", "## 2. Replay 结果", "",
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
    parser.add_argument("--gate", action="store_true", help="校验指标目标值与健康链路阈值")
    parser.add_argument("--baseline", default=None, metavar="JSON",
                        help="与既有基线快照比对，任一指标回归即失败")
    parser.add_argument("--write-baseline", default=None, metavar="JSON",
                        help="把本次健康链路指标写成基线快照")
    parser.add_argument("--ratio-tolerance", type=float, default=0.0,
                        help="回归比对允许的相对下降（默认 0，任何下降都算回归）")
    parser.add_argument("--allow-live", action="store_true", help="允许 replay/full 调用真实 API")
    parser.add_argument("--target-dir", default=".", help="replay 的被评测项目目录")
    parser.add_argument("--samples", type=int, default=20, help="replay 默认抽样数量")
    parser.add_argument("--replay-judge", choices=("live", "stub-yes", "stub-no"),
                        default="live",
                        help="replay 判分器；stub-* 为离线管道验证（不代表模型质量）")
    parser.add_argument("--dataset", choices=("external", "self"), default="external",
                        help="replay 使用的标注集：external=124 条外部项目，self=33 条本仓自评")
    parser.add_argument("--sample-delay", type=float, default=1.0, metavar="SECONDS",
                        help="replay 样本间最小间隔（默认 1s；网关限速下可调大）")
    parser.add_argument("--sample-retries", type=int, default=0, metavar="ROUNDS",
                        help="批末对判分异常样本的重试轮数（网关抖动时建议 1-2）")
    parser.add_argument("--circuit-breaker", type=int, default=8, metavar="N",
                        help="连续 N 个样本判分异常后停止整批（0 = 关闭；"
                             "网关整体不可用时避免跑到数小时）")
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
    # 只有真实判分才需要联网确认；stub 判分器完全离线
    if (args.suite in {"replay", "full"} and args.replay_judge == "live"
            and not args.allow_live):
        parser.error("replay/full 使用真实判分器会调用 API，必须显式传入 --allow-live"
                     "（离线管道验证请用 --replay-judge stub-yes|stub-no）")

    offline_results: list[FaultResult] = []
    gate: dict = {"tool_success_rate": 0.0, "e2e_success_rate": 0.0, "passed": False}
    # 报告路径必须在分支之前算好：replay-only 运行（--suite replay）不进
    # offline 分支，若在那里赋值就会出现 `--report` 被静默忽略、
    # 产物落到默认文件名的情况（实测踩过）。
    report_path: Path | None = None
    if args.report is not None:
        report_path = (Path("reports") / f"eval_offline_{datetime.now():%Y%m%d}.md"
                       if args.report == "" else Path(args.report))
    if args.suite in {"offline", "full"}:
        offline_results, data, gate, health_data = run_offline(args.work_dir, args.repeat, args.model)
        failed = [result for result in offline_results if not result.passed]
        print(f"[eval] offline: {len(offline_results) - len(failed)}/{len(offline_results)} 条断言通过")

        # ── 指标门禁：目标值 + 基线回归（在健康链路上判定） ──
        indicators = evaluate_targets(health_data, TARGETS)
        regression: RegressionResult | None = None
        if args.baseline:
            regression = compare_to_baseline(
                metrics_snapshot(health_data, model=args.model, repeat=args.repeat),
                load_baseline(args.baseline), ratio_tolerance=args.ratio_tolerance)
        if args.write_baseline:
            snapshot_path = write_baseline(
                metrics_snapshot(health_data, model=args.model, repeat=args.repeat),
                args.write_baseline)
            print(f"[eval] baseline written: {snapshot_path}")

        if report_path is not None:
            body = render_evaluation_report(
                health_data, data, offline_results, args.repeat, gate,
                indicators=indicators, regression=regression, baseline_path=args.baseline)
            write_report(body, report_path)
            print(f"[eval] report: {report_path}")

        for indicator in indicators:
            if indicator.is_failure:
                print(f"[eval] 指标未达标: {indicator.name} — {indicator.detail}", file=sys.stderr)
        if failed:
            return 1
        if args.gate and not gate_passed(indicators):
            print("[eval] gate failed: 存在未达标或必需指标无样本（详见报告第 9 节）",
                  file=sys.stderr)
            return 1
        if args.gate and regression is not None and not regression.passed:
            print("[eval] gate failed: 检出基线回归 — " + "；".join(regression.regressions),
                  file=sys.stderr)
            return 1
        if args.gate and not gate["passed"]:
            print("[eval] gate failed: 工具成功率需 >=98%，端到端成功率需 >=90%", file=sys.stderr)
            return 1

    if args.suite in {"replay", "full"}:
        replay = _run_replay(args.target_dir, args.samples, args.replay_judge,
                             args.dataset, args.sample_delay, args.sample_retries,
                             args.circuit_breaker)
        artifact = _write_replay_artifact(replay, report_path)
        if report_path:
            _append_replay_report(report_path, replay, artifact, args.target_dir)
        print(f"[eval] replay detail: {artifact}")
        print("[eval] replay: " + json.dumps({k: v for k, v in replay.items() if k != "details"},
                                                ensure_ascii=False))
        meta = replay.get("meta") or {}
        if meta.get("skipped") or meta.get("errored"):
            print(f"[eval] 数据集未完整判分：跳过 {meta.get('skipped', 0)} 条、"
                  f"异常 {meta.get('errored', 0)} 条 —— 读数分母不完整", file=sys.stderr)
        if args.gate and not meta.get("healthy", False):
            print("[eval] gate failed: replay 数据集未健康判分（存在跳过/异常样本，"
                  "precision/recall 仅覆盖部分样本），详见产物 meta", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
