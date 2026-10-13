"""用新标签重算既有 replay 产物的指标（零 API 成本）。

为什么可行：replay 产物记录了每个样本位置**模型的判定**（YES/NO）与命中单元
（TP/FP/FN/TN）。当**标注标签**修正后（例如某条从"真问题"改为"已修复"），
模型当时的回答没变，但它在混淆矩阵里的归属会变：

    label 正 → 负 时：模型的 YES 从 TP 变成 FP，模型的 NO 从 FN 变成 TN

因此修正标签不需要重新调用 API —— 只要把"模型判定 + 新标签"重新组合即可。
这也让"标注修正"与"模型改动"的影响可以被分开计量。

用法：
    python scripts/recompute_metrics.py <replay.json> [--out new.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_dataset_module():
    path = Path("tests/eval_llm_agent_qa.py")
    spec = importlib.util.spec_from_file_location("ds_current", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_new_labels(relocations: dict[tuple[str, int], int] | None = None
                     ) -> dict[tuple[str, int], list[bool]]:
    """从当前数据集读出 (file, line) -> [is_bug, ...]（按出现顺序）。

    值是**列表**而不是单个布尔：同一 (file, line) 可以有多条标注
    （例 `tools/calculator.py:105` 同时有 SECURITY 与 BUG 两条）。若只存一个
    布尔，两条样本会被当成同一标签——未来若只翻转其中一条，这里就会取错。

    relocations：{(file, 旧行号): 新行号}。RELOCATE 处置会改变行号，
    旧产物的 (file, 旧行号) 在新数据集里已不存在；不补这层映射会让这些样本
    被静默丢弃（分母变小、读数虚高）。
    """
    path = Path("tests/eval_llm_agent_qa.py")
    spec = importlib.util.spec_from_file_location("ds_current", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    labels: dict[tuple[str, int], list[bool]] = {}
    for sample in _load_dataset_module().EVAL_SAMPLES:
        labels.setdefault((str(sample[0]), int(sample[1])), []).append(bool(sample[6]))

    for (file_name, old_line), new_line in (relocations or {}).items():
        target = labels.get((file_name, int(new_line)))
        if target is not None:
            labels.setdefault((file_name, int(old_line)), []).extend(target)
    return labels


def _load_relocations() -> dict[tuple[str, int], int]:
    """从 reports/_stale_originals.json 读出 RELOCATE 的旧→新行号映射。"""
    path = Path("reports/_stale_originals.json")
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(str(e["file"]), int(e["line"])): int(e["new_line"])
            for e in data if e.get("action") == "RELOCATE"}


def _ratio(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{100 * num / den:.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用新标签重算既有产物的指标")
    parser.add_argument("replay", help="旧 replay 产物 JSON")
    parser.add_argument("--out", default=None, help="把重算结果写成新产物")
    parser.add_argument("--limit", type=int, default=0,
                        help="只取前 N 条（用于与子集实验对照，基线免费）")
    args = parser.parse_args(argv)

    replay = json.loads(Path(args.replay).read_text(encoding="utf-8"))
    relocations = _load_relocations()
    labels = _load_new_labels(relocations)
    print(f"（含 {len(relocations)} 条重定位映射，避免这些样本被静默丢弃）\n")

    tp = fp = fn = tn = 0
    details: list[list] = []
    missing = 0
    rows = list(replay.get("details") or [])
    if args.limit:
        rows = rows[:args.limit]
    # 同一位置的多个标签按出现顺序消费：只取一次会漏掉重复标注，
    # 每次取第一个又会把两条样本都算成同一条。
    consumed: dict[tuple[str, int], int] = {}
    for row in rows:
        file_name, line = str(row[0]), int(row[1])
        found = bool(row[3])                       # 模型当时的判定
        key = (file_name, line)
        bucket = labels.get(key)
        if not bucket:
            missing += 1
            continue
        ordinal = consumed.get(key, 0)
        # 位置重复时按出现顺序取标签；超出则沿用最后一个（重复位置标签一致时等价）
        is_bug = bucket[min(ordinal, len(bucket) - 1)]
        consumed[key] = ordinal + 1
        cell = ("TP" if found else "FN") if is_bug else ("FP" if found else "TN")
        if cell == "TP":
            tp += 1
        elif cell == "FP":
            fp += 1
        elif cell == "FN":
            fn += 1
        else:
            tn += 1
        details.append([file_name, line, is_bug, found, cell, row[5]])

    meta = dict(replay.get("meta") or {})
    version = str(_load_dataset_module().DATASET_META.get("version", "unknown"))
    # 必须改写 dataset_version：重算产物是按**当前标签**得出的，
    # 若保留原始运行时的旧版本号，任何下游消费者（含基线门禁的可比性判断）
    # 都会读到错误的版本，进而把不可比的读数当成可比。
    meta["dataset_version"] = version
    meta["recomputed_with_dataset"] = f"v{version}（标签修正后重算，未重新调用 API）"
    meta["original_metrics"] = {
        "true_positives": replay.get("true_positives"),
        "false_positives": replay.get("false_positives"),
        "false_negatives": replay.get("false_negatives"),
        "true_negatives": replay.get("true_negatives"),
        "precision": replay.get("precision"),
        "recall": replay.get("recall"),
        "f1_score": replay.get("f1_score"),
    }
    f1 = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else 0.0
    result = {
        "total": len(rows), "scanned": tp + fp + fn + tn,
        "true_positives": tp, "false_positives": fp,
        "false_negatives": fn, "true_negatives": tn,
        "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
        "f1_score": f"{f1:.2f}", "details": details, "meta": meta,
    }

    print("=" * 74)
    print("标签修正后重算 / Recompute with corrected labels (zero API cost)")
    print("=" * 74)
    print(f"{'指标':<14}{'v1.0.0（旧标签）':>20}{f'v{version}（修正后）':>20}")
    print("-" * 54)
    old = meta["original_metrics"]
    for label, okey, nkey in (("Precision", "precision", "precision"),
                              ("Recall", "recall", "recall"),
                              ("F1", "f1_score", "f1_score")):
        print(f"{label:<14}{str(old.get(okey)):>20}{str(result[nkey]):>20}")
    for label, key in (("TP", "true_positives"), ("FP", "false_positives"),
                       ("FN", "false_negatives"), ("TN", "true_negatives")):
        print(f"{label:<14}{old.get(key):>20}{result[key]:>20}")
    if missing:
        print(f"\n  ⚠️ 有 {missing} 条在数据集中找不到对应位置（未计入）")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  已写出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
