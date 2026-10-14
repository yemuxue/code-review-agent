"""汇总分析 replay 产物：混淆矩阵、按模块的漏报分布、FP/TN 明细。"""
import json
import pathlib
import sys
from collections import Counter


def main(path: str) -> int:
    d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    tp, fp = d["true_positives"], d["false_positives"]
    fn, tn = d["false_negatives"], d["true_negatives"]
    print("最终读数")
    print(f"  TP={tp} FP={fp} FN={fn} TN={tn}  (合计 {tp+fp+fn+tn})")
    print(f"  Precision={d['precision']}  Recall={d['recall']}  F1={d['f1_score']}")
    meta = d["meta"]
    print(f"  healthy={meta['healthy']}  errored={meta['errored']}  "
          f"skipped={meta['skipped']}  model={meta['model']}")

    by_module = Counter()
    for row in d["details"]:
        if row[4] == "FN":
            by_module[row[0].split("/")[0]] += 1
    print("\n漏报（FN）按模块分布:")
    for name, count in by_module.most_common(12):
        print(f"  {name}: {count}")

    print("\n误报（FP）明细 — 把假问题判成真问题:")
    for row in d["details"]:
        if row[4] == "FP":
            print(f"  {row[0]}:{row[1]}  LLM={row[5][:70]!r}")

    print("\n正确排除（TN）— FP 陷阱被正确拒绝:")
    for row in d["details"]:
        if row[4] == "TN":
            print(f"  {row[0]}:{row[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1
                          else "reports/eval_replay_sol_high_full_replay.json"))
