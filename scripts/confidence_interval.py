"""用 Wilson 区间算当前读数的置信区间与所需样本量。

为什么企业级必须看这个
    124 条样本里正例 78 条，报告 "Recall 52.6%" 是一个点估计。
    商业决策（能不能卖、能不能阻塞合并）需要知道**真实值的范围**。
    样本量小的时候，点估计的波动可以覆盖整个决策阈值。

Wilson 区间而不是正态近似：小样本、接近 0/1 时正态近似会给出越界区间
（例如 0/4 或 4/4 时算出 [-x, >1]），Wilson 始终落在 [0,1] 内。
"""

from __future__ import annotations

import math


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


def report(label: str, successes: int, total: int) -> None:
    low, high = wilson(successes, total)
    print(f"  {label:<26} {successes}/{total} = {100 * successes / total:5.1f}%"
          f"    95% CI [{100 * low:.1f}%, {100 * high:.1f}%]"
          f"   宽度 {100 * (high - low):.1f}pp")


def sample_size_for(width_pp: float, p: float = 0.5, z: float = 1.96) -> int:
    """要把 95% 区间宽度压到 width_pp 个百分点，需要多少样本。"""
    target = width_pp / 100 / 2 / z      # 半宽对应的标准误
    return math.ceil(p * (1 - p) / (target ** 2))


def main() -> int:
    print("=" * 76)
    print("当前读数的置信区间（Wilson, 95%）")
    print("=" * 76)

    # v1.3.0 顺序对齐重算的结果
    report("Precision", 41, 50)
    report("Recall（整体）", 41, 78)
    report("High 严重度召回", 4, 4)
    report("Medium 严重度召回", 9, 15)
    report("Low 严重度召回", 28, 59)
    report("负例拒绝率（TN）", 37, 46)

    print("\n" + "=" * 76)
    print("要把区间压到指定宽度，正例需要多少条")
    print("=" * 76)
    for width in (20, 15, 10, 5):
        print(f"  区间宽度 ±{width / 2:.1f}pp（宽 {width}pp）→ 约需 "
              f"{sample_size_for(width)} 条正例")

    print("\n关键含义")
    _, high = wilson(4, 4)
    low_4, _ = wilson(4, 4)
    print(f"  · High 4/4 的 95% 下界只有 {100 * low_4:.1f}% —— "
          f"'高危 100% 可靠'在统计上不成立，真实值可能低至五成")
    low_r, high_r = wilson(41, 78)
    print(f"  · 整体 Recall 的真实值可能在 {100 * low_r:.1f}%~{100 * high_r:.1f}% 之间；"
          f"若对外承诺 60%，当前样本无法证伪也无法证实")
    print(f"  · 要支撑'精确优先'这类定性结论够用；"
          f"要支撑'比竞品高 X 个点'这类比较，正例至少需 {sample_size_for(10)} 条量级")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
