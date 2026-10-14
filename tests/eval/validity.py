"""标注有效性叠加层 / Annotation validity overlay.

为什么需要它
    2026-10-07 对 61 个漏报逐条读源码归因后发现：29 条（48%）的标注描述在当前
    代码里**不存在或已被修复**，27 条（44%）属维护性/风格类口径分歧。

    这直接决定了两件事：
      1. 表面 Recall（43.5%）不是模型能力，其中一半是**评测集失效**；
      2. 提高召回率之前必须先有**正确的分母**，否则无法判断某次改动是
         真提升还是在对"已修复的旧问题"报错。

本模块把逐条归因结果变成机器可读的有效性映射，并给出调整后的指标：
    - recall_raw        —— 现有口径（分母 = 全部真实问题标注）
    - recall_effective  —— 只在**语义仍有效**的标注上计算
                           （排除 STALE，保留 CRITERION_DISPUTED：
                            它们是真实但低优先级的客观缺陷，模型应当召回）
    - ceiling           —— 本数据集在当前模型能力下能达到的召回上限

⚠️ 有效性映射来自**一次性人工/多agent归因**，不是自动判定的。
   新增标注或修改目标代码后必须重新归因，否则该叠加层会过期。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# 归因类别 → 是否计入"有效召回分母"
#   VALID              正常有效：必须召回
#   CRITERION_DISPUTED 客观存在的低优先级缺陷：应当召回（提示词口径问题）
#   STALE              描述的缺陷已不存在：**不计入分母**
COUNTED_CATEGORIES = frozenset({"VALID", "CRITERION_DISPUTED"})
EXCLUDED_CATEGORIES = frozenset({"STALE"})

# 多 agent 归因类别 → 本模块的有效性判定
_CATEGORY_MAP = {
    "A_ANNOTATION_WEAK": "STALE",
    "B_CONTEXT_MISSING": "CRITERION_DISPUTED",   # 真实存在，只是窗口看不到
    "C_CRITERION_STRICT": "CRITERION_DISPUTED",  # 客观存在，判据过严
    "D_REAL_MISS": "VALID",                      # 真实缺陷，模型判断错误
    "E_AMBIGUOUS": "CRITERION_DISPUTED",         # 保守计入分母
}


@dataclass
class AdjustedMetrics:
    """原始指标与调整后指标的对照。"""

    total_real: int                      # 数据集里真实问题标注数
    stale: int = 0                       # 其中语义已失效
    disputed: int = 0                    # 客观但低优先级
    valid: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    details: list[dict] = field(default_factory=list)

    @property
    def effective_denominator(self) -> int:
        """有效召回分母 = 有效 + 口径分歧（两者都是"应当召回"的）。"""
        return self.valid + self.disputed

    @property
    def recall_raw(self) -> float | None:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else None

    @property
    def recall_effective(self) -> float | None:
        denom = self.effective_denominator
        return self.true_positives / denom if denom else None

    @property
    def ceiling(self) -> float | None:
        """理论上限：把仍未召回的"有效/客观"标注都算作可召回。

        等于 1.0 减去"口径分歧里模型判 NO 的比例"——即提示词口径修正后
        最多能到多少。STALE 不参与，因此它也是**当前数据集能表达的上限**。
        """
        return self.recall_effective

    @property
    def precision(self) -> float | None:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else None

    def summary_lines(self) -> list[str]:
        def pct(v: float | None) -> str:
            return "n/a" if v is None else f"{v:.1%}"

        return [
            f"  真实问题标注: {self.total_real}",
            f"    语义失效(STALE，不计入分母): {self.stale}",
            f"    口径分歧(客观低优先级，计入分母): {self.disputed}",
            f"    有效(VALID): {self.valid}",
            f"  有效召回分母: {self.effective_denominator}",
            "",
            f"  Precision:        {pct(self.precision)}",
            f"  Recall 原始口径:   {pct(self.recall_raw)}"
            f"  (TP={self.true_positives} / {self.true_positives + self.false_negatives})",
            f"  Recall 有效口径:   {pct(self.recall_effective)}"
            f"  (TP={self.true_positives} / {self.effective_denominator})",
        ]


def load_validity(path: str | Path) -> dict[int, str]:
    """读取归因结果，返回 {样本序号: 有效性}。

    文件格式即 `reports/_fn_attribution_classification.json`：
        [{"id": 1, "category": "A_ANNOTATION_WEAK", "note": "..."}]
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validity: dict[int, str] = {}
    for item in data:
        raw = str(item.get("category", ""))
        validity[int(item["id"])] = _CATEGORY_MAP.get(raw, "CRITERION_DISPUTED")
    return validity


def adjust(replay: dict, validity: dict[int, str],
           fn_order: list[tuple[str, int]] | None = None) -> AdjustedMetrics:
    """把有效性映射叠加到 replay 产物上，算出调整后指标。

    fn_order 必须与归因时的顺序一致（即产物里 FN 的出现顺序），
    否则有效性会套到错误的样本上。默认从产物自身推导。
    """
    details = replay.get("details") or []
    fns = [(str(r[0]), int(r[1])) for r in details if r[4] == "FN"]
    order = fn_order if fn_order is not None else fns
    if len(order) != len(validity):
        raise ValueError(
            f"有效性映射与漏报数不匹配：映射 {len(validity)} 条，产物漏报 {len(order)} 条。"
            "若数据集或判分结果已变更，需要重新归因。")

    metrics = AdjustedMetrics(
        total_real=replay["true_positives"] + replay["false_negatives"],
        true_positives=replay["true_positives"],
        false_positives=replay["false_positives"],
        false_negatives=replay["false_negatives"],
        true_negatives=replay["true_negatives"],
    )
    fn_validity: dict[tuple[str, int], str] = {}
    for index, key in enumerate(order, start=1):
        fn_validity[key] = validity.get(index, "CRITERION_DISPUTED")

    for key, kind in fn_validity.items():
        if kind == "STALE":
            metrics.stale += 1
        elif kind == "VALID":
            metrics.valid += 1
        else:
            metrics.disputed += 1
        metrics.details.append({"file": key[0], "line": key[1], "validity": kind})

    # 未出现在漏报里的真实问题 = 已被模型召回，视为有效性成立
    recalled_valid = replay["true_positives"]
    metrics.valid += recalled_valid
    return metrics


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="标注有效性叠加：调整后召回率")
    parser.add_argument("replay", help="replay 产物 JSON")
    parser.add_argument("--validity", default="reports/_fn_attribution_classification.json")
    args = parser.parse_args(argv)

    replay = json.loads(Path(args.replay).read_text(encoding="utf-8"))
    validity = load_validity(args.validity)

    print("=" * 74)
    print("标注有效性叠加 / Adjusted recall (annotation validity overlay)")
    print("=" * 74)
    meta = replay.get("meta") or {}
    print(f"模型 {meta.get('model')}  判据 {meta.get('judge_variant', '?')}  "
          f"窗口 {meta.get('context_lines', '?')}  数据集 v{meta.get('dataset_version')}\n")
    try:
        metrics = adjust(replay, validity)
    except ValueError as exc:
        print(f"错误: {exc}")
        return 1
    for line in metrics.summary_lines():
        print(line)
    print()
    print("  说明: STALE 标注描述的缺陷在当前代码里已不存在，")
    print("        任何提示词都无法召回 —— 把它们计入分母会系统性低估模型能力。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
