"""按样本顺序对齐，用当前标签重算既有产物（零 API 成本）。

## 为什么不用 (file, line) 匹配

标签修正会**移动行号**（RELOCATE）。每轮修正都新增几张映射，位置匹配因此
越来越脆弱：v1.3.0 只有 3 条重定位，但按位置重算就有 3 条映射不上，
分母悄悄从 78 变成 75 —— 读数看起来合理，实际不完整。

## 为什么顺序对齐是对的

`run_per_sample_eval` 按 `samples` 的顺序逐条判分并顺序追加结果，
补丁引擎（`patch_dataset.py`）只就地改标签/行号、不重排样本
（v1.3.0 的 `MERGE_DUPLICATE` 为 0，没有删除条目）。
所以产物 `details[i]` 与当前 `EVAL_SAMPLES[i]` 一一对应。

本脚本会**校验这个前提**：统计 index 对齐下有多少条位置完全一致、
多少条仅行号不同（= 重定位）、多少条连文件名都不同（= 顺序真的变了）。
若顺序变了，必须改用别的方式对齐，不能硬套。

`aligned_rows()` 是本模块的公开入口，测试也复用它 —— 保证"重算"只有一处实现。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")


def _load_dataset():
    spec = importlib.util.spec_from_file_location("ds_by_index", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ratio(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{100 * num / den:.1f}%"


def aligned_rows(samples: list[tuple], details: list[list]) -> list[dict]:
    """按样本顺序对齐，返回每条的判定单元与元信息。

    每条返回：file / line / is_bug / found / cell / severity / answer。
    `cell` ∈ {TP, FP, FN, TN} —— 由"模型是否报出"与"标注是否真问题"决定。
    """
    if len(details) != len(samples):
        raise ValueError(
            f"产物 {len(details)} 条 ≠ 数据集 {len(samples)} 条，顺序对齐不成立")

    rows: list[dict] = []
    for detail, sample in zip(details, samples):
        found = bool(detail[3])
        is_bug = bool(sample[6])
        cell = ("TP" if found else "FN") if is_bug else ("FP" if found else "TN")
        rows.append({
            "file": str(sample[0]),
            "line": int(sample[1]),
            "is_bug": is_bug,
            "found": found,
            "cell": cell,
            "severity": str(sample[3]),
            "answer": str(detail[5]),
        })
    return rows


def alignment_check(samples: list[tuple], details: list[list]) -> dict[str, int]:
    """核对"顺序对齐"前提：位置完全一致 / 仅行号漂移 / 文件名不同。"""
    same = drift = file_drift = 0
    for detail, sample in zip(details, samples):
        if str(detail[0]) != str(sample[0]):
            file_drift += 1
        elif int(detail[1]) != int(sample[1]):
            drift += 1
        else:
            same += 1
    return {"same_position": same, "line_drift": drift, "file_drift": file_drift}


def severity_stats(rows: list[dict]) -> dict[str, list[int]]:
    """按严重度分层统计正例的 [检出, 漏报]。"""
    order = {"High": 0, "Medium": 1, "Low": 2}
    stats: dict[str, list[int]] = {}
    for row in rows:
        if not row["is_bug"]:
            continue
        bucket = stats.setdefault(row["severity"], [0, 0])
        bucket[0 if row["cell"] == "TP" else 1] += 1
    return dict(sorted(stats.items(), key=lambda kv: order.get(kv[0], 9)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按样本顺序重算指标")
    parser.add_argument("artifact", help="旧 replay 产物 JSON")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    module = _load_dataset()
    samples = module.EVAL_SAMPLES
    meta = module.DATASET_META
    artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    details = artifact.get("details") or []

    if len(details) != len(samples):
        print(f"⚠️ 产物 {len(details)} 条 ≠ 数据集 {len(samples)} 条，"
              "顺序对齐不成立，终止", file=sys.stderr)
        return 1

    check = alignment_check(samples, details)
    rows = aligned_rows(samples, details)
    counts = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    for row in rows:
        counts[row["cell"]] += 1
    tp, fp, fn, tn = counts["TP"], counts["FP"], counts["FN"], counts["TN"]

    print("=" * 70)
    print("按样本顺序重算 / Recompute aligned by sample order")
    print("=" * 70)
    print(f"数据集 v{meta['version']}（{meta['real_bug_count']} 正例 / "
          f"{meta['fp_trap_count']} 负例）")
    print(f"顺序对齐校验: 位置完全一致 {check['same_position']}、"
          f"仅行号漂移 {check['line_drift']}、文件名不同 {check['file_drift']}")
    if check["file_drift"]:
        print(f"⚠️ 有 {check['file_drift']} 条文件名不同 —— 顺序可能已变，"
              "读数需人工确认")

    f1 = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else 0.0
    print(f"\n混淆矩阵: TP={tp} FP={fp} FN={fn} TN={tn}  (合计 {tp + fp + fn + tn})")
    print(f"Precision: {_ratio(tp, tp + fp)}（{tp}/{tp + fp}）")
    print(f"Recall:    {_ratio(tp, tp + fn)}（{tp}/{tp + fn}）")
    print(f"F1:        {f1:.2f}")

    print("\n按严重度分层（仅正例）")
    for level, (hit, miss) in severity_stats(rows).items():
        total = hit + miss
        print(f"  {level:<7} 正例 {total:>3}  检出 {hit:>3}  漏报 {miss:>3}  "
              f"召回 {_ratio(hit, total)}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 重算只改**标签**，不改运行配置。因此把原始产物的 meta 继承下来
        # （model / scoring / judge_variant / context_lines / run_at），
        # 只把 dataset_version 换成当前版本 —— 否则基线比对会因"配置字段缺失"
        # 判为不可比较（exit 3），那是自伤而非保护。
        source_meta = dict(artifact.get("meta") or {})
        source_meta.update({
            "dataset_id": meta["dataset_id"],
            "dataset_version": meta["version"],
            "alignment": "sample_order",
            "alignment_check": check,
            "recomputed_from": str(args.artifact),
        })
        payload = {
            "total": len(samples), "scanned": tp + fp + fn + tn,
            "true_positives": tp, "false_positives": fp,
            "false_negatives": fn, "true_negatives": tn,
            "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
            "f1_score": f"{f1:.2f}", "details": rows,
            "meta": source_meta,
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\n已写出: {out}")
        kept = [k for k in ("model", "scoring", "judge_variant", "context_lines")
                if k in source_meta]
        if kept:
            print(f"继承的运行配置字段: {', '.join(kept)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
