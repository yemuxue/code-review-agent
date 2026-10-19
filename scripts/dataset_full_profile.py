"""数据集完整事实画像：正负例 × 类别 × 严重度，含每类真实样例。

回答「分别代表什么错误」时需要**正例与负例分开看**：
正例才是"要检出的错误类型"，负例是"不该报的陷阱"，两者混在一起
会误导读者以为类别分布就是错误分布。
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")
FIXED = ("【已修复】", "[FIXED]")


def _load():
    spec = importlib.util.spec_from_file_location("ds_full", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_fixed(sample) -> bool:
    text = str(sample[4]) + str(sample[5])
    return any(m in text for m in FIXED)


def main() -> int:
    module = _load()
    samples = module.EVAL_SAMPLES
    meta = module.DATASET_META

    positives = [s for s in samples if bool(s[6])]
    negatives = [s for s in samples if not bool(s[6])]
    flipped = [s for s in negatives if _is_fixed(s)]
    traps = [s for s in negatives if not _is_fixed(s)]

    print("=" * 78)
    print(f"数据集 v{meta['version']}｜{len(samples)} 条")
    print(f"  正例 {len(positives)}（要检出的真实错误）")
    print(f"  负例 {len(negatives)} = 原始 FP 陷阱 {len(traps)} + 审计转负 {len(flipped)}")
    print("=" * 78)

    print("\n[1] 正例 × 类别（这才是'错误类型'分布）")
    cat_pos = Counter(str(s[2]) for s in positives)
    for name, count in cat_pos.most_common():
        print(f"    {name:<10} {count:>3} 条  ({count / len(positives):.0%})")

    print("\n[2] 正例 × 严重度")
    sev_pos = Counter(str(s[3]) for s in positives)
    for name in ("High", "Medium", "Low"):
        if sev_pos.get(name):
            print(f"    {name:<8} {sev_pos[name]:>3} 条")

    print("\n[3] 正例 × 类别 × 严重度 交叉")
    cross = Counter((str(s[2]), str(s[3])) for s in positives)
    print(f"    {'类别':<10}{'High':>6}{'Medium':>8}{'Low':>6}")
    for cat in [c for c, _ in cat_pos.most_common()]:
        row = [cross.get((cat, sev), 0) for sev in ("High", "Medium", "Low")]
        print(f"    {cat:<10}{row[0]:>6}{row[1]:>8}{row[2]:>6}")

    print("\n[4] 负例 × 类别")
    cat_neg = Counter(str(s[2]) for s in negatives)
    for name, count in cat_neg.most_common():
        print(f"    {name:<10} {count:>3} 条")

    print("\n[5] 每个类别的真实样例（取正例）")
    shown: dict[str, bool] = defaultdict(bool)
    for s in positives:
        cat = str(s[2])
        if shown[cat]:
            continue
        shown[cat] = True
        print(f"    [{cat}/{s[3]}] {s[0]}:{s[1]}")
        print(f"        {s[4][:104]}")

    print("\n[6] 负例样例（两类陷阱的差别）")
    if traps:
        s = traps[0]
        print(f"    [原始 FP 陷阱] {s[0]}:{s[1]}  {s[2]}/{s[3]}")
        print(f"        {s[4][:104]}")
    if flipped:
        s = flipped[0]
        print(f"    [审计转负]    {s[0]}:{s[1]}  {s[2]}/{s[3]}")
        print(f"        {s[4][:104]}")

    print("\n[7] 文件覆盖")
    files = Counter(str(s[0]) for s in samples)
    print(f"    共 {len(files)} 个文件；样本最多："
          + "、".join(f"{k} {v}" for k, v in files.most_common(5)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
