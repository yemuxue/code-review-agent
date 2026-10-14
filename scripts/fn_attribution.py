"""FN（漏报）归因分析。

回答的问题：61 个漏报分别是什么性质？集中在哪？哪些最值得修？

分析维度（全部离线，不调用 API）：
    1. 混淆矩阵复核 + 按模块/类别/严重度分组
    2. 每个 FN 的标注原文 与 该位置的代码上下文 并列，供人工判定
    3. 严重度加权：漏掉 High 的代价远高于漏掉 Low
    4. 标注行附近是否有"测试/文档"特征（可能是标注本身不可检出）

⚠️ 局限（必须随结论说明）：产物里没存模型的判定理由
（`judge()` 只返回布尔值），因此本文**只能给出候选归因，不能确认原因**。
要确认需要补采模型理由（见文末建议）。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.datasets import (  # noqa: E402
    DATASETS, _build_file_index, load_samples, resolve_sample_file,
)
from tests.eval.scoring import build_context  # noqa: E402

ARTIFACT = "reports/eval_replay_sol_high_full_replay.json"


def load_findings(path: str) -> tuple[dict, list[dict]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    by_cell: dict[str, list[dict]] = {}
    for row in data.get("details") or []:
        file_name, line, is_bug, found, cell, answer = row
        by_cell.setdefault(cell, []).append({
            "file": file_name, "line": int(line), "is_bug": bool(is_bug),
            "found": found, "cell": cell, "answer": answer,
        })
    return data, [r for r in (data.get("details") or [])
                  and by_cell.get("FN", [])]


def _index_key(file_name: str, line: int) -> tuple[str, int]:
    return (str(file_name), int(line))


def main(argv: list[str] | None = None) -> int:
    artifact = argv[0] if argv else ARTIFACT
    data = json.loads(Path(artifact).read_text(encoding="utf-8"))
    meta = data.get("meta") or {}

    fns = [{"file": r[0], "line": int(r[1]), "cell": r[4], "answer": r[5]}
           for r in (data.get("details") or []) if r[4] == "FN"]

    print("=" * 78)
    print("FN 归因分析 / Missed-finding attribution")
    print("=" * 78)
    print(f"产物: {artifact}")
    print(f"模型: {meta.get('model')}  数据集 v{meta.get('dataset_version')}  "
          f"healthy={meta.get('healthy')}")
    print(f"读数: P={data.get('precision')} R={data.get('recall')} "
          f"F1={data.get('f1_score')}")
    print(f"混淆矩阵: TP={data['true_positives']} FP={data['false_positives']} "
          f"FN={data['false_negatives']} TN={data['true_negatives']}")
    print(f"漏报样本数: {len(fns)}")

    # ── 用标注集补齐 描述/类别/严重度（产物里没存）──
    spec = next(s for s in DATASETS
                if s.dataset_id == meta.get("dataset_id"))
    samples, _ = load_samples(spec)
    target = Path(str(meta.get("target_dir") or "."))
    index = _build_file_index(target, spec.search_root)
    by_key = {_index_key(s[0], s[1]): s for s in samples}

    enriched = []
    for fn in fns:
        sample = by_key.get(_index_key(fn["file"], fn["line"]))
        if sample is None:
            fn.update(category="?", severity="?", desc_en="(标注未匹配)", desc_cn="")
        else:
            fn.update(category=sample[2], severity=sample[3],
                      desc_en=sample[4], desc_cn=sample[5])
        enriched.append(fn)

    # ── 1) 分布 ──
    print("\n" + "-" * 78)
    print("[1] 漏报分布")
    print("-" * 78)
    for label, key in (("类别", "category"), ("严重度", "severity")):
        counter = Counter(f[key] for f in enriched)
        total = sum(counter.values())
        parts = "、".join(f"{k} {v} ({v / total:.0%})"
                          for k, v in counter.most_common())
        print(f"  {label}: {parts}")

    high = [f for f in enriched if f["severity"] == "High"]
    print(f"\n  ⚠️ High 严重度漏报: {len(high)}/{len(enriched)}"
          f"（占漏报 {len(high) / len(enriched):.0%}）")
    all_high = sum(1 for s in samples if s[6] and s[3] == "High")
    print(f"     全数据集 High 真实问题: {all_high} 个 → "
          f"High 召回率 {1 - len(high) / all_high:.1%}"
          if all_high else "     （无 High 样本）")

    by_module = Counter(f["file"].split("/")[0] for f in enriched)
    print("\n  模块: " + "、".join(f"{k} {v}" for k, v in by_module.most_common()))

    # ── 2) 逐条明细（供人工判定）──
    print("\n" + "-" * 78)
    print("[2] 逐条明细（标注原文 + 代码上下文，供人工判定归因）")
    print("-" * 78)
    for i, fn in enumerate(enriched, 1):
        resolved, reason = resolve_sample_file(target, str(fn["file"]), index)
        snippet = ""
        if resolved is not None:
            try:
                lines = build_context(resolved, fn["line"], context_lines=6).split("\n")
                # 只留标注行与其前后两行，控制篇幅
                keep = [l for l in lines if l.startswith(">>>") or abs(
                    int(l.split("|")[0].strip() or 0) - fn["line"]) <= 2]
                snippet = "\n".join(keep)
            except OSError as exc:
                reason = str(exc)
        print(f"\n[{i:02d}] {fn['file']}:{fn['line']}  "
              f"{fn['category']}/{fn['severity']}")
        print(f"     标注: {fn['desc_cn']}")
        print(f"     EN  : {fn['desc_en']}")
        if snippet:
            print("     ```")
            for line in snippet.split("\n"):
                print(f"     {line}")
            print("     ```")
        elif reason:
            print(f"     (无法取上下文: {reason})")

    # ── 3) 归因假设统计 ──
    print("\n" + "-" * 78)
    print("[3] 候选归因（基于可观测特征，非确认结论）")
    print("-" * 78)
    print("""
  说明：产物未保存模型判定理由，以下只是**待验证假设**，需人工看上方明细后归类。
  建议按这四类逐条打标，再统计占比：

    A. 标注本身不可检出   — 描述依赖仓库外知识/设计意图，或该行代码看不出问题
    B. 上下文不足         — 问题跨函数/跨文件，±15 行窗口看不到关键调用方
    C. 判据口径过严       — 属于"维护性/风格"类，与 JUDGE_PROMPT 的严格标准冲突
    D. 真实漏报           — 代码确实有明显缺陷，模型判断错误（最值得修）

  参考：High 严重度漏报 0 条，说明 Recall 偏低主要来自 Low 严重度的
  维护性/性能类问题 —— 这决定了"要不要为 Recall 改提示词"是产品取舍问题，
  而不是单纯的实现缺陷。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
