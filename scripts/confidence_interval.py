"""用 Wilson 区间算当前读数的置信区间与所需样本量。

## 为什么企业级必须看这个

正例只有几十条时，报告的 "Recall 51.2%" 是一个点估计。商业决策
（能不能卖、能不能阻塞合并）需要知道**真实值的范围**：样本量小的时候，
点估计的波动可以覆盖整个决策阈值。

## 数据来源

默认读 `reports/eval_replay_*_by_index.json`（按当前标签重算的产物），
不再硬编码任何百分比 —— 否则每次审计修正标签后，这里的区间都会过期。

## 为什么用 Wilson 而不是正态近似

小样本、比例接近 0/1 时正态近似会给出越界区间（例如 4/4 时算出上界 > 1），
Wilson 始终落在 [0,1] 内。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPORTS = Path("reports")


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 区间的上下界。"""
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (z / denominator) * math.sqrt(
        p * (1 - p) / total + z * z / (4 * total * total))
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _latest_artifact() -> dict:
    """取最新的按序重算产物。"""
    candidates = sorted(REPORTS.glob("eval_replay_v*_by_index.json"))
    if not candidates:
        raise SystemExit(
            "找不到按序重算产物；先运行：\n"
            "  python scripts/recompute_by_index.py "
            "reports/eval_replay_sol_high_full_replay.json "
            "--out reports/eval_replay_v140_by_index.json")
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def sample_size_for(width_pp: float, p: float = 0.5, z: float = 1.96) -> int:
    """要把 95% 区间宽度压到 width_pp 个百分点，需要多少样本。"""
    target = width_pp / 100 / 2 / z
    return math.ceil(p * (1 - p) / (target ** 2))


def main() -> int:
    artifact = _latest_artifact()
    meta = artifact.get("meta") or {}
    tp = int(artifact["true_positives"])
    fp = int(artifact["false_positives"])
    fn = int(artifact["false_negatives"])
    tn = int(artifact["true_negatives"])

    print("=" * 76)
    print(f"当前读数的置信区间（Wilson, 95%）  数据集 v{meta.get('dataset_version')}")
    print("=" * 76)

    def report(label: str, successes: int, total: int) -> None:
        if total == 0:
            print(f"  {label:<24} 无样本")
            return
        low, high = wilson(successes, total)
        print(f"  {label:<24} {successes}/{total} = {100 * successes / total:5.1f}%"
              f"    95% CI [{100 * low:.1f}%, {100 * high:.1f}%]"
              f"   宽度 {100 * (high - low):.1f}pp")

    report("Precision", tp, tp + fp)
    report("Recall（整体）", tp, tp + fn)

    # 按严重度分层：正例的 [检出, 漏报]
    strata: dict[str, list[int]] = {}
    for row in artifact.get("details") or []:
        if not row.get("is_bug"):
            continue
        bucket = strata.setdefault(str(row.get("severity")), [0, 0])
        bucket[0 if row.get("cell") == "TP" else 1] += 1
    for level in ("High", "Medium", "Low"):
        if level in strata:
            hit, miss = strata[level]
            report(f"{level} 严重度召回", hit, hit + miss)

    report("负例拒绝率（TN）", tn, tn + fp)

    print("\n" + "=" * 76)
    print("要把区间压到指定宽度，正例需要多少条")
    print("=" * 76)
    for width in (20, 15, 10, 5):
        print(f"  区间宽度 ±{width / 2:.1f}pp（宽 {width}pp）→ 约需 "
              f"{sample_size_for(width)} 条正例")

    print("\n关键含义")
    high_hit, high_miss = strata.get("High", [0, 0])
    if high_hit + high_miss:
        low_h, _ = wilson(high_hit, high_hit + high_miss)
        print(f"  · High {high_hit}/{high_hit + high_miss} 的 95% 下界约 "
              f"{100 * low_h:.1f}% —— 样本太少，「高危可靠」在统计上不成立")
    low_r, high_r = wilson(tp, tp + fn)
    print(f"  · 整体 Recall 真实值可能在 {100 * low_r:.1f}%~{100 * high_r:.1f}% 之间；"
          "对外承诺前必须先看区间")
    print(f"  · 要支撑「比竞品高 X 个点」这类比较，正例至少需 "
          f"{sample_size_for(10)} 条量级")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
