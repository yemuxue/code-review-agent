"""判据变更的前后对比：净变化 + 逐样本翻转明细。

为什么不能只看净变化：召回率 +5% 可能是"多召回 8 条、同时丢掉 3 条"。
只报净值会把回归掩盖掉，而这个回归往往出现在原本正确的样本上。

本脚本接受"基线产物"与"实验产物"，输出：
    - 混淆矩阵与 P/R/F1 的对照
    - **逐样本翻转**（TP→FN 回归 / FN→TP 改善）
    - 按标注有效性分层的改善（STALE 样本上的翻转毫无意义）

用法：
    python scripts/judge_compare.py --baseline reports/A.json --candidate reports/B.json \
        --validity reports/_fn_attribution_classification.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _unwrap(raw: dict, variant: str | None = None) -> dict:
    """兼容两种产物形态。

    - 直接跑评测的产物：顶层就是混淆矩阵与 details
    - judge_ab.py 的产物：结果嵌在 `variants.<name>` 下

    不做这层归一化时，对比会静默读到 None 并算出 n/a，看起来像"无变化"。
    """
    variants = raw.get("variants")
    if not isinstance(variants, dict):
        return raw
    if variant and variant in variants:
        return variants[variant]
    # 未指定时取第一个（judge_ab 单变体运行时即为该变体）
    first = next(iter(variants.values()), None)
    return first if isinstance(first, dict) else raw


def _cells(replay: dict) -> dict[tuple[str, int, int], str]:
    """(file, line, 第几次出现) -> 命中单元。

    必须带出现序号：数据集中同一 (file, line) 可以有多条标注
    （例：`tools/calculator.py:105` 同时有 SECURITY 与 BUG 两条）。
    只用 (file, line) 建字典会**静默丢掉一条**，表现为共有样本数少 1、
    翻转统计失真。preflight 会对这种重复给 warning。
    """
    seen: dict[tuple[str, int], int] = {}
    cells: dict[tuple[str, int, int], str] = {}
    for row in replay.get("details") or []:
        key = (str(row[0]), int(row[1]))
        ordinal = seen.get(key, 0)
        seen[key] = ordinal + 1
        cells[(key[0], key[1], ordinal)] = str(row[4])
    return cells


def _ratio(text: str) -> float | None:
    t = str(text).strip()
    if not t or t.lower() == "n/a":
        return None
    try:
        return float(t.rstrip("%")) / 100
    except ValueError:
        return None


def _fmt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1%}"


def _delta(new: float | None, old: float | None) -> str:
    if new is None or old is None:
        return "n/a"
    return f"{new - old:+.1%}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判据变更前后对比")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--validity", default=None,
                        help="归因结果 JSON；提供后按标注有效性分层")
    parser.add_argument("--baseline-name", default="baseline")
    parser.add_argument("--candidate-name", default="candidate")
    args = parser.parse_args(argv)

    base = _unwrap(json.loads(Path(args.baseline).read_text(encoding="utf-8")))
    cand = _unwrap(json.loads(Path(args.candidate).read_text(encoding="utf-8")))

    bm, cm = base.get("meta") or {}, cand.get("meta") or {}
    print("=" * 78)
    print("判据变更对比 / Judge variant comparison")
    print("=" * 78)
    print(f"{args.baseline_name:<12}: 判据={bm.get('judge_variant', '?')} "
          f"窗口=±{bm.get('context_lines', '?')} 样本={base.get('scanned')}/{base.get('total')}")
    print(f"{args.candidate_name:<12}: 判据={cm.get('judge_variant', '?')} "
          f"窗口=±{cm.get('context_lines', '?')} 样本={cand.get('scanned')}/{cand.get('total')}")

    print(f"\n{'指标':<12}{args.baseline_name:>12}{args.candidate_name:>12}{'变化':>10}")
    print("-" * 46)
    for label, key in (("Precision", "precision"), ("Recall", "recall"), ("F1", "f1_score")):
        b, c = base.get(key), cand.get(key)
        if key == "f1_score":
            try:
                delta = f"{float(c) - float(b):+.2f}"
            except (TypeError, ValueError):
                delta = "n/a"
        else:
            delta = _delta(_ratio(c) if c is not None else None,
                           _ratio(b) if b is not None else None)
        print(f"{label:<12}{str(b):>12}{str(c):>12}{delta:>10}")
    print(f"{'TP':<12}{base['true_positives']:>12}{cand['true_positives']:>12}"
          f"{cand['true_positives'] - base['true_positives']:>+10}")
    print(f"{'FP':<12}{base['false_positives']:>12}{cand['false_positives']:>12}"
          f"{cand['false_positives'] - base['false_positives']:>+10}")
    print(f"{'FN':<12}{base['false_negatives']:>12}{cand['false_negatives']:>12}"
          f"{cand['false_negatives'] - base['false_negatives']:>+10}")

    # ── 逐样本翻转 ──
    bc, cc = _cells(base), _cells(cand)
    common = set(bc) & set(cc)
    improved = [k for k in common if bc[k] == "FN" and cc[k] == "TP"]
    regressed = [k for k in common if bc[k] == "TP" and cc[k] == "FN"]
    new_fp = [k for k in common if bc[k] == "TN" and cc[k] == "FP"]
    fixed_fp = [k for k in common if bc[k] == "FP" and cc[k] == "TN"]

    print(f"\n逐样本翻转（仅统计两产物共有的 {len(common)} 条）")
    print(f"  ✅ FN → TP（改善）: {len(improved)} 条")
    print(f"  ❌ TP → FN（回归）: {len(regressed)} 条")
    print(f"  ⚠️ TN → FP（新增误报）: {len(new_fp)} 条")
    print(f"  ✅ FP → TN（误报修复）: {len(fixed_fp)} 条")

    # ── 按有效性分层 ──
    if args.validity:
        from tests.eval.validity import load_validity
        validity = load_validity(args.validity)
        # 有效性只对**基线里的漏报**做过归因（见 fn_attribution 的覆盖范围）。
        # 基线里是 TP 的样本从未被归因，因此不能声称它们"VALID"——
        # 那只是默认值。这里显式区分"已归因"与"推定"。
        fn_order = [k for k, v in bc.items() if v == "FN"]
        fn_validity = {k: validity.get(i, "CRITERION_DISPUTED")
                       for i, k in enumerate(fn_order, start=1)}

        print("\n改善样本按标注有效性分层（只有非 STALE 的改善才算真提升）")
        for label, keys in (("FN → TP", improved), ("TP → FN", regressed)):
            buckets: dict[str, int] = {}
            for k in keys:
                if k in fn_validity:
                    kind = fn_validity[k]
                else:
                    # 基线里是 TP：模型当时认为有问题，但**没有独立归因**
                    kind = "推定有效（基线已检出，未独立归因）"
                buckets[kind] = buckets.get(kind, 0) + 1
            detail = "、".join(f"{k} {v}" for k, v in sorted(buckets.items())) or "—"
            print(f"  {label}: {detail}")

        stale_gain = [k for k in improved if fn_validity.get(k) == "STALE"]
        real_gain = [k for k in improved if k in fn_validity
                     and fn_validity[k] != "STALE"]
        print(f"\n  可信的召回改善: {len(real_gain)} 条（非 STALE 且已归因）")
        if stale_gain:
            print(f"  ⚠️ 其中 {len(stale_gain)} 条改善来自 STALE 标注 —— "
                  "那不是真实召回提升（模型开始对已修复的旧问题报错）")
        presumed = [k for k in regressed if k not in fn_validity]
        if presumed:
            print(f"  ⚠️ {len(presumed)} 条回归未做过独立归因（基线已检出），"
                  "但基线检出过本身就是它们有效的证据")

    if regressed:
        print("\n  回归样本明细（前 8 条，这些必须逐条看）：")
        for key in regressed[:8]:
            print(f"    {key[0]}:{key[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
