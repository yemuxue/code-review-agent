"""检查标注集中是否存在重复的 (file, line) 位置。

为什么重要：大量脚本用 `{(file, line): sample}` 建立索引。若数据集里同一位置
有两条样本，字典会**静默保留最后一条**，另一条消失——表现为分母变小、
或标签取错。这类错误不报错，只让读数偏离。

重复位置本身未必是错（同一行可以有两个不同问题），但必须**显式**处理：
要么合并描述，要么用更细的键（含类别/描述）。
"""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load(path: str):
    spec = importlib.util.spec_from_file_location("ds_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(path: str, samples_attr: str) -> int:
    module = _load(path)
    samples = getattr(module, samples_attr)
    by_key: dict[tuple[str, int], list] = defaultdict(list)
    for position, sample in enumerate(samples, start=1):
        by_key[(str(sample[0]), int(sample[1]))].append((position, sample))

    duplicates = {k: v for k, v in by_key.items() if len(v) > 1}
    print(f"=== {path} ===")
    print(f"  样本 {len(samples)} 条，唯一位置 {len(by_key)} 个")
    if not duplicates:
        print("  ✅ 无重复位置")
        return 0

    print(f"  ⚠️ {len(duplicates)} 个位置有重复条目：")
    for key, entries in sorted(duplicates.items()):
        print(f"\n  {key[0]}:{key[1]}")
        for position, sample in entries:
            print(f"    #{position}  {sample[2]}/{sample[3]}  is_bug={sample[6]}")
            print(f"        中文: {sample[5][:78]}")
            print(f"        英文: {sample[4][:78]}")

    # 重复且标签不一致的情况最危险
    conflicting = {k: v for k, v in duplicates.items()
                   if len({bool(s[6]) for _p, s in v}) > 1}
    if conflicting:
        print(f"\n  ❌ {len(conflicting)} 个位置**标签互相矛盾**（同一行既算正例又算负例）：")
        for key in sorted(conflicting):
            print(f"     {key[0]}:{key[1]}")
    return len(duplicates)


def main() -> int:
    total = 0
    total += check("tests/eval_llm_agent_qa.py", "EVAL_SAMPLES")
    total += check("tests/eval_dataset.py", "EVAL_SAMPLES")
    print(f"\n合计 {total} 个重复位置")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
