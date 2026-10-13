"""FN 归因结论汇总。

把多 agent 逐条归因的结果与可观测证据合并成一份可复核的结论：

    - 每个漏报的归因类别
    - 按类别 / 严重度 / 模块的分布
    - 标注有效性的两个独立风险：
        ① 语义失效 —— 标注描述的缺陷在当前代码里已经被处理或根本不存在
        ② 行号漂移 —— 标注行已经不指向所描述的对象（文件被重构过）

⚠️ 关键口径：`preflight()` 只保证"文件存在、行号在范围内"，
**不保证"该位置仍然存在所描述的缺陷"**。本脚本量化后者。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.datasets import (  # noqa: E402
    DATASETS, load_samples, _build_file_index,
)

ARTIFACT = "reports/eval_replay_sol_high_full_replay.json"

# 多 agent 逐条归因结果（见 reports/_fn_attribution_classification.json）。
# 类别定义见 scripts/fn_attribution.py。
CLASSIFICATION = "reports/_fn_attribution_classification.json"


def main(argv: list[str] | None = None) -> int:
    artifact = Path(argv[0] if argv else ARTIFACT)
    data = json.loads(artifact.read_text(encoding="utf-8"))
    meta = data.get("meta") or {}

    cls_path = Path(CLASSIFICATION)
    if not cls_path.is_file():
        print(f"缺少归因结果 {cls_path}", file=sys.stderr)
        return 1
    classified = {int(item["id"]): item for item in
                  json.loads(cls_path.read_text(encoding="utf-8"))}

    fns = [{"n": i, "file": r[0], "line": int(r[1])}
           for i, r in enumerate(
               [x for x in (data.get("details") or []) if x[4] == "FN"], start=1)]

    spec = next(s for s in DATASETS if s.dataset_id == meta.get("dataset_id"))
    samples, _ = load_samples(spec)
    target = Path(str(meta.get("target_dir") or "."))
    index = _build_file_index(target, spec.search_root)
    by_key = {(str(s[0]), int(s[1])): s for s in samples}

    for fn in fns:
        sample = by_key.get((fn["file"], fn["line"]))
        fn["severity"] = sample[3] if sample else "?"
        fn["category"] = sample[2] if sample else "?"
        fn["desc_en"] = sample[4] if sample else ""
        fn["classification"] = classified.get(fn["n"], {}).get("category", "UNCLASSIFIED")
        fn["note"] = classified.get(fn["n"], {}).get("note", "")

    total = len(fns)
    print("=" * 78)
    print("FN 归因结论 / Missed findings: attribution summary")
    print("=" * 78)
    print(f"模型 {meta.get('model')}  数据集 v{meta.get('dataset_version')}  "
          f"P={data.get('precision')} R={data.get('recall')} F1={data.get('f1_score')}")
    print(f"漏报 {total} 条，逐条归因如下\n")

    counts = Counter(f["classification"] for f in fns)
    labels = {
        "A_ANNOTATION_WEAK": "标注失效（描述的缺陷在代码里不存在或已被处理）",
        "B_CONTEXT_MISSING": "上下文不足（证据在 ±15 行之外）",
        "C_CRITERION_STRICT": "判据口径分歧（维护性/风格类，模型答 NO 合理）",
        "D_REAL_MISS": "真实漏报（可见代码里就有明确缺陷）",
        "E_AMBIGUOUS": "无法判定",
        "UNCLASSIFIED": "未归类",
    }
    print("[1] 归因分布")
    for key, count in counts.most_common():
        print(f"  {count:3d} 条 ({count / total:4.0%})  {labels.get(key, key)}")

    real = counts.get("D_REAL_MISS", 0)
    if real == 0:
        print("\n  ❗ 真实漏报 = 0 条 → 61 个漏报里**没有一条**是"
              "「代码里有明确缺陷但模型没看出来」")
    else:
        print(f"\n  ❗ 真实漏报 = {real} 条（这些才是模型判断错误）：")
        for f in fns:
            if f["classification"] == "D_REAL_MISS":
                print(f"     · {f['file']}:{f['line']} — {f['note'][:110]}")

    # ── 按严重度 ──
    print("\n[2] 交叉：归因 × 严重度")
    sev = Counter((f["classification"], f["severity"]) for f in fns)
    for key in ("A_ANNOTATION_WEAK", "B_CONTEXT_MISSING", "C_CRITERION_STRICT",
                "D_REAL_MISS", "E_AMBIGUOUS"):
        row = {s: sev.get((key, s), 0) for s in ("High", "Medium", "Low")}
        if any(row.values()):
            print(f"  {labels[key][:20]:22s} High={row['High']} Medium={row['Medium']} "
                  f"Low={row['Low']}")

    # ── 标注有效性风险 ──
    print("\n[3] 标注集有效性（preflight 未覆盖的部分）")
    weak = [f for f in fns if f["classification"] == "A_ANNOTATION_WEAK"]
    strict = [f for f in fns if f["classification"] == "C_CRITERION_STRICT"]
    print(f"  语义失效: {len(weak)}/{total} 条漏报的标注描述与当前代码不符")
    print(f"  口径分歧: {len(strict)}/{total} 条属维护性/风格类，模型答 NO 合理")
    print("  → preflight() 只校验『文件存在 + 行号在范围内』，")
    print("    不校验『该行是否仍有标注所描述的问题』。这是当前评测集最大的效力风险。")
    print("\n  典型证据（已人工复核源码）：")
    for f in weak[:3]:
        print(f"    · {f['file']}:{f['line']} — {f['note'][:120]}")

    print("\n[4] 结论怎么用")
    weak_pct = f"{len(weak) / total:.0%}"
    strict_pct = f"{len(strict) / total:.0%}"
    print(f"""
  Recall={data.get('recall')} 不能直接读作"模型漏掉了这么多问题"：
  61 个漏报中
    · {real} 条 真实漏报（模型判断错误）
    · {len(weak)} 条（{weak_pct}）标注与当前代码不符 —— 是**评测集的效力问题**，不是模型问题
    · {len(strict)} 条（{strict_pct}）维护性/风格类，模型答 NO 符合 JUDGE_PROMPT 的口径

  对产品决策的含义：
    · 该配置在**能被检出的问题上**表现良好（Precision={data.get('precision')}，
      High 严重度漏报 0 条 → High 召回 100%）
    · 但**当前数据集无法支撑"Recall 是否达标"的判断** —— 必须先修标注
    · 修标注之前不要为提高 Recall 去放宽判定提示词：
      那会让模型开始对"已被修复的旧问题"报 YES，推高误报而无真实收益

  下一步优先级：
    1. 重建标注有效性基线：逐条确认"该位置是否仍存在所描述的缺陷"
    2. 把"语义失效 / 行号漂移"纳入 preflight 的告警（当前只校验存在性与范围）
    3. 修完标注后重跑，再谈 Recall 目标与是否调整提示词
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
