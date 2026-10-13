"""按严重度分层统计召回率（用于产品文档引用，必须可机器复核）。

产品文档里"高危问题召回率"这类数字不能只靠某次运行的日志印象，
必须能从产物 + 数据集稳定重算出来。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = "tests/eval_llm_agent_qa.py"
DEFAULT_ARTIFACT = "reports/eval_replay_v120_recomputed.json"


def _load_dataset():
    spec = importlib.util.spec_from_file_location("ds_sev", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_relocations() -> dict[tuple[str, int], int]:
    """RELOCATE 处置把行号改了：产物里是旧行号，数据集里是新行号。

    不补这层映射会让这些样本静默漏统计（分层后的正例数之和 < 总正例数），
    而"数字对不上"很容易被当成四舍五入忽略过去。
    """
    path = Path("reports/_stale_originals.json")
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(str(e["file"]), int(e["line"])): int(e["new_line"])
            for e in data if e.get("action") == "RELOCATE"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按严重度分层的召回率")
    parser.add_argument("artifact", nargs="?", default=DEFAULT_ARTIFACT)
    args = parser.parse_args(argv)

    module = _load_dataset()
    version = module.DATASET_META["version"]

    # (file, line) -> [severity, ...]（重复位置按出现顺序消费）
    severity: dict[tuple[str, int], list[str]] = {}
    for sample in module.EVAL_SAMPLES:
        severity.setdefault((str(sample[0]), int(sample[1])), []).append(
            str(sample[3]))
    # 旧行号也指向同一份严重度（重定位样本在产物里仍是旧行号）
    for (file_name, old_line), new_line in _load_relocations().items():
        target = severity.get((file_name, int(new_line)))
        if target is not None:
            severity.setdefault((file_name, int(old_line)), []).extend(target)

    artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    consumed: dict[tuple[str, int], int] = {}
    stats: dict[str, Counter] = {}
    for row in artifact.get("details") or []:
        key = (str(row[0]), int(row[1]))
        bucket = severity.get(key)
        if not bucket:
            continue
        index = consumed.get(key, 0)
        level = bucket[min(index, len(bucket) - 1)]
        consumed[key] = index + 1
        if not bool(row[2]):          # 只看真实问题（负例不计入召回）
            continue
        cell = str(row[4])
        stats.setdefault(level, Counter())[cell] += 1

    print(f"数据集 v{version}  产物 {args.artifact}")
    print(f"{'严重度':<8}{'正例数':>8}{'检出(TP)':>10}{'漏报(FN)':>10}{'召回率':>10}")
    print("-" * 46)
    order = {"High": 0, "Medium": 1, "Low": 2}
    for level in sorted(stats, key=lambda item: order.get(item, 9)):
        counter = stats[level]
        total = counter["TP"] + counter["FN"]
        recall = f"{counter['TP'] / total:.0%}" if total else "n/a"
        print(f"{level:<8}{total:>8}{counter['TP']:>10}{counter['FN']:>10}{recall:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
