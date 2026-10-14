"""判据变体的 A/B 对比（召回率提升的受控实验）。

为什么需要它：召回率提升只能靠"改一处、跑同一数据集、比读数"来证明。
手工跑两次再看两个 JSON 容易看错，也容易在读数变好时忘记检查误报是否恶化。

本脚本：
    1. 对同一数据集、同一模型依次跑多个判据变体（strict / lenient …）；
    2. 输出对照表：Precision / Recall / F1 / FP / FN 的变化；
    3. **同时报告误报变化** —— 提升召回率的常见代价是误报上升，
       只看召回会得出错误的结论；
    4. 把结果落盘成可复核的 JSON，供后续引用。

用法：
    python scripts/judge_ab.py --dataset external --samples 40 \
        --target-dir X:/VScode/llm-agent-qa-system --variants strict lenient
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _ratio(value: str) -> float | None:
    """把 "94.0%" 解析成 0.94；"n/a" 返回 None。"""
    text = str(value).strip()
    if not text or text.lower() == "n/a":
        return None
    try:
        return float(text.rstrip("%")) / 100
    except ValueError:
        return None


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _delta(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return "n/a"
    diff = new - old
    return f"{diff:+.1%}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判据变体 A/B 对比")
    parser.add_argument("--dataset", choices=("external", "self"), default="external")
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--variants", nargs="+", default=["strict", "lenient"])
    parser.add_argument("--context-lines", type=int, default=None,
                        help="强制所有变体使用同一上下文窗口（隔离提示词与窗口的影响）")
    parser.add_argument("--sample-delay", type=float, default=1.0)
    parser.add_argument("--sample-retries", type=int, default=1)
    parser.add_argument("--circuit-breaker", type=int, default=15)
    parser.add_argument("--keys-file", default=None,
                        help="只跑该 JSON 里列出的样本（scripts/select_samples.py 生成）；"
                             "用于把 API 实验聚焦在受影响的子集上")
    parser.add_argument("--out", default="reports/judge_ab_result.json")
    args = parser.parse_args(argv)

    from tests.eval_llm_agent_qa import run_per_sample_eval, run_self_eval

    # 通过环境变量把窗口覆盖传给 judge.context_lines（见其 docstring）
    if args.context_lines:
        import os
        os.environ["JUDGE_CONTEXT_LINES"] = str(args.context_lines)

    subset = None
    if args.keys_file:
        payload = json.loads(Path(args.keys_file).read_text(encoding="utf-8"))
        wanted = {(str(k[0]), int(k[1])) for k in payload["keys"]}
        from tests.eval_llm_agent_qa import EVAL_SAMPLES
        subset = [s for s in EVAL_SAMPLES
                  if (str(s[0]), int(s[1])) in wanted]
        missing = wanted - {(str(s[0]), int(s[1])) for s in subset}
        print(f"[ab] keys-file 选中 {len(subset)}/{len(wanted)} 条"
              + (f"；⚠️ {len(missing)} 条在数据集中找不到" if missing else ""))
        if not subset:
            print("[ab] 子集为空，退出", file=sys.stderr)
            return 2

    runs: dict[str, dict] = {}
    for variant in args.variants:
        print(f"\n{'=' * 70}\n[ab] 运行变体 {variant}"
              f"（样本 {args.samples}，数据集 {args.dataset}）\n{'=' * 70}")
        kwargs = dict(max_samples=args.samples, judge_override=None,
                      sample_delay_s=args.sample_delay,
                      sample_retries=args.sample_retries,
                      circuit_breaker=args.circuit_breaker, variant=variant)
        if subset is not None:
            kwargs["samples"] = subset
        if args.dataset == "self":
            result = run_self_eval(target_dir=args.target_dir, **kwargs)
        else:
            result = run_per_sample_eval(target_dir=args.target_dir, **kwargs)
        runs[variant] = result
        print(f"[ab] {variant}: P={result['precision']} R={result['recall']} "
              f"F1={result['f1_score']} | TP={result['true_positives']} "
              f"FP={result['false_positives']} FN={result['false_negatives']} "
              f"TN={result['true_negatives']}")

    baseline = args.variants[0]
    base = runs[baseline]
    print(f"\n{'=' * 70}\n[ab] 对照表（基线 = {baseline}）\n{'=' * 70}")
    header = f"{'变体':<10}{'P':>9}{'ΔP':>9}{'R':>9}{'ΔR':>9}{'F1':>7}{'FP':>5}{'FN':>5}{'TP':>5}"
    print(header)
    print("-" * len(header))
    for variant in args.variants:
        row = runs[variant]
        print(f"{variant:<10}"
              f"{_fmt(_ratio(row['precision'])):>9}"
              f"{_delta(_ratio(row['precision']), _ratio(base['precision'])):>9}"
              f"{_fmt(_ratio(row['recall'])):>9}"
              f"{_delta(_ratio(row['recall']), _ratio(base['recall'])):>9}"
              f"{row['f1_score']:>7}"
              f"{row['false_positives']:>5}"
              f"{row['false_negatives']:>5}"
              f"{row['true_positives']:>5}")

    # ── 判定结论时必须同时看误报 ──
    print("\n[ab] 判读要点")
    for variant in args.variants[1:]:
        row = runs[variant]
        d_recall = ((_ratio(row["recall"]) or 0) - (_ratio(base["recall"]) or 0))
        d_prec = ((_ratio(row["precision"]) or 0) - (_ratio(base["precision"]) or 0))
        fp_delta = row["false_positives"] - base["false_positives"]
        verdict = []
        if d_recall > 0.02:
            verdict.append(f"召回 +{d_recall:.1%}")
        if d_prec < -0.02:
            verdict.append(f"⚠️ 精确率 -{abs(d_prec):.1%}（代价）")
        if fp_delta > 0:
            verdict.append(f"⚠️ 误报 +{fp_delta} 条")
        print(f"  {variant}: " + ("；".join(verdict) if verdict else "无显著变化"))

    healthy = all(r.get("meta", {}).get("healthy") for r in runs.values())
    print(f"\n[ab] 全部变体均健康判分: {healthy}"
          + ("" if healthy else "  ⚠️ 存在跳过/异常样本，读数分母不完整"))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "dataset": args.dataset,
        "samples": args.samples,
        "baseline": baseline,
        "variants": {v: {k: r[k] for k in
                         ("total", "scanned", "true_positives", "false_positives",
                          "false_negatives", "true_negatives", "precision", "recall",
                          "f1_score")}
                     # details 必须落盘：否则无法做逐样本翻转分析，
                     # 而"净变化"会把"多召回 8 条、同时丢掉 3 条"掩盖成提升。
                     | {"details": r.get("details") or [],
                        "meta": r.get("meta", {})}
                     for v, r in runs.items()},
        "healthy": healthy,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ab] 结果: {out}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
