"""核实「口径分歧」样本的真实标签：它们到底是正例还是负例？

把当前数据集（v1.4.0）与按序重算产物逐条对齐，
输出漏报（FN）按 类别×严重度 的构成。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")
ARTIFACT = Path("reports/eval_replay_v140_by_index.json")
FIXED = ("【已修复】", "[FIXED]")


def _load():
    spec = importlib.util.spec_from_file_location("ds_fn", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = _load()
    samples = module.EVAL_SAMPLES
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    details = artifact["details"]
    assert len(details) == len(samples), "顺序对齐前提不成立"

    fn = [(i, s, d) for i, (s, d) in enumerate(zip(samples, details), 1)
          if bool(s[6]) and not bool(d["found"])]

    print(f"漏报总数: {len(fn)}\n")

    print("[1] 漏报 × 类别（它们在标注里是正例）")
    for name, count in Counter(str(s[2]) for _, s, _ in fn).most_common():
        print(f"    {name:<10} {count:>3} 条  is_bug={True}")

    print("\n[2] 漏报 × 严重度")
    for name in ("High", "Medium", "Low"):
        count = sum(1 for _, s, _ in fn if str(s[3]) == name)
        if count:
            print(f"    {name:<8} {count:>3} 条")

    print("\n[3] 漏报里带【已修复】标记的（不应存在：标记的是负例）")
    marked = [(i, s) for i, s, _ in fn
              if any(m in str(s[5]) for m in FIXED)]
    print(f"    {len(marked)} 条" + (f" -> {[(i, f'{s[0]}:{s[1]}') for i, s in marked[:5]]}" if marked else ""))

    print("\n[4] 对比：负例的构成（模型答 NO 算 TN，不算漏报）")
    negatives = [s for s in samples if not bool(s[6])]
    for name, count in Counter(str(s[2]) for s in negatives).most_common():
        print(f"    {name:<10} {count:>3} 条  is_bug=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
