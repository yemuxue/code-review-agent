"""真实 replay 基线的记录与门禁 / Replay baseline record & gate.

为什么需要它
    "接受 56.0% 作为基线"如果只写在文档里，就无法在下次改动时自动发现退化。
    本脚本把该基线变成**机器可读的记录 + 可执行的门禁**：

        record  从一次 replay 产物生成基线记录（含出处与漏报构成）
        check   用新产物比对基线，发现回归时以非零码退出

关键设计：**不可比的运行必须拒绝比较**，而不是给出一个看似合理的差值。
    数据集版本、模型、判据变体、上下文窗口、判分方式任一不同，读数就不可比 ——
    例如 v1.1.0 与 v1.2.0 的召回分母是 81 与 84，直接相减毫无意义。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

RECORD_VERSION = 1
DEFAULT_RECORD = "tests/eval/replay_baseline.json"

# 需要完全一致的"可比性字段"：任一不同即拒绝比较
COMPARABILITY_KEYS = ("dataset_version", "model", "judge_variant",
                      "context_lines", "scoring")

# 判定回归的容差（百分点）。真实模型在同一配置下也会有轻微波动，
# 容差过紧会把噪声报成回归，过松则漏掉真退化。
DEFAULT_TOLERANCE_PP = 2.0


def _load_dataset_module():
    path = Path("tests/eval_llm_agent_qa.py")
    spec = importlib.util.spec_from_file_location("ds_baseline", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pct(value: object) -> float | None:
    text = str(value).strip()
    if not text or text.lower() == "n/a":
        return None
    try:
        return float(text.rstrip("%"))
    except ValueError:
        return None


def _miss_breakdown(artifact: dict | None = None) -> dict[str, int] | None:
    """漏报构成（按归因类别），用于让 Recall 这个数字可解释。

    缺任一输入文件时返回 None —— 宁可不写，也不要编一个构成出来。

    ⚠️ **版本校验**：`_fn_attribution_classification.json` 是对 **v1.0.0 的 61 条漏报**
    做的归因。数据集升到 v1.3.x 后漏报数已变（37 条），这份归因**不能再用来解释
    当前读数**。因此这里会核对条数：条数与产物 FN 不一致就返回 None，
    让基线记录显式写明"当前版本未重新归因"，而不是把过期构成写进去。
    """
    classification = Path("reports/_fn_attribution_classification.json")
    stale_originals = Path("reports/_stale_originals.json")
    if not classification.is_file():
        return None
    try:
        classified = json.loads(classification.read_text(encoding="utf-8"))
        categories = {int(i["id"]): str(i["category"]) for i in classified}
        relocated = set()
        if stale_originals.is_file():
            originals = json.loads(stale_originals.read_text(encoding="utf-8"))
            relocated = {int(e["stale_id"]) for e in originals
                         if e.get("action") == "RELOCATE"}
    except (OSError, ValueError, KeyError):
        return None

    # 归因条数必须与当前产物的漏报数一致，否则说明归因已过期
    if artifact is not None:
        current_fn = int(artifact.get("false_negatives") or 0)
        if len(categories) != current_fn:
            return None

    breakdown = {
        "口径分歧（客观低优先级维护性问题）": sum(
            1 for i, c in categories.items()
            if c in {"C_CRITERION_STRICT", "B_CONTEXT_MISSING", "E_AMBIGUOUS"}),
        "真实漏报（模型判断错误）": sum(
            1 for c in categories.values() if c == "D_REAL_MISS"),
        "重定位标注后仍漏": len(relocated),
    }
    return breakdown


def build_record(artifact: dict) -> dict:
    """从 replay 产物生成基线记录。

    两个字段不能直接取产物：
    - `dataset_version`：标签修正后重算的产物，其 meta 仍带**原始运行**的版本号，
      而记录的必须是**标签当时**的版本。所以以数据集模块为准。
    - `judge_variant` / `context_lines`：基线那次运行发生在这些字段落盘之前，
      产物里没有。它们实际取的是 strict 提示词与 CONTEXT_LINES=15，
      但这是**推断**而非记录 —— 必须显式列进 inferred_fields，不能让读者
      误以为产物里写着。
    """
    meta = artifact.get("meta") or {}
    dataset = _load_dataset_module().DATASET_META
    inferred: list[str] = []

    variant = meta.get("judge_variant")
    if variant is None:
        variant = "strict"
        inferred.append("judge_variant（基线运行早于该字段落盘；"
                        "当时用的是 strict 提示词）")
    window = meta.get("context_lines")
    if window is None:
        window = 15
        inferred.append("context_lines（基线运行早于该字段落盘；"
                        "当时 CONTEXT_LINES 硬编码为 15）")

    record = {
        "record_version": RECORD_VERSION,
        "accepted_at": str(meta.get("run_at") or ""),
        "decision": "严格标准下的精确优先基线",
        # 标签版本以数据集模块为准：重算产物的 meta 带的是原始运行的版本号
        "dataset_version": str(dataset.get("version")),
        "dataset_version_source": "tests/eval_llm_agent_qa.py::DATASET_META"
                                  "（产物 meta 里是原始运行时的旧版本号）",
        "metrics_source": str(meta.get("recomputed_with_dataset")
                              or "replay 产物原始读数"),
        "inferred_fields": inferred,
        "dataset_id": dataset.get("dataset_id"),
        "sample_count": dataset.get("sample_count"),
        "real_bug_count": dataset.get("real_bug_count"),
        "fp_trap_count": dataset.get("fp_trap_count"),
        "model": meta.get("model"),
        "judge_variant": variant,
        "context_lines": window,
        "scoring": meta.get("scoring"),
        "quality_claim": meta.get("quality_claim"),
        "metrics": {
            "precision": artifact.get("precision"),
            "recall": artifact.get("recall"),
            "f1_score": artifact.get("f1_score"),
            "true_positives": artifact.get("true_positives"),
            "false_positives": artifact.get("false_positives"),
            "false_negatives": artifact.get("false_negatives"),
            "true_negatives": artifact.get("true_negatives"),
        },
        "rationale": (
            "模型只报它有把握的缺陷：精确率高于召回率。"
            "剩余漏报以客观但低优先级的维护性问题为主，模型判为『不值得报』。"
            "两条提示词杠杆（放宽判据、改开放式枚举）经实验证伪："
            "放宽判据反而丢掉 8 个正确检出，枚举提问同样无法召回这些样本。"
            "因此接受该召回率，而不是用放宽标准去换取数字。"
        ),
        "known_limitations": [
            "tools/calculator.py:105 有两条标注共用同一位置，一次检出会计入 2 次",
            f"【已修复】负例只排除了原标注所指的问题；转负例共 "
            f"{(dataset.get('fp_trap_count') or 0) - 16} 条（其中 3 条曾因"
            "『位置另有真缺陷』被收回，故当前计数已含该修正）",
            "判分按【位置】匹配而非按缺陷内容匹配：模型在该位置报出另一个"
            "真实缺陷也会计为 TP，这是一处已知的宽松",
            "未标注的真问题会被计为 FP（标签偏差），系统性低估精确率",
            f"审计范围：{dataset.get('audit_scope', '未声明')}",
            "样本量有限：正例较少，点估计的 95% 置信区间较宽，"
            "不足以支撑对外承诺或与竞品做差值比较",
        ],
    }
    breakdown = _miss_breakdown(artifact)
    if breakdown:
        record["miss_breakdown"] = breakdown
    else:
        record["miss_breakdown_note"] = (
            "当前数据集版本的漏报未重新归因；"
            "`reports/_fn_attribution_classification.json` 是对旧版（61 条漏报）"
            "做的归因，条数与当前不符，故不写入构成 —— "
            "避免用过期归因解释当前读数。")
    return record


def _inferred_names(record: dict) -> set[str]:
    """从 inferred_fields 的说明文本里取回字段名（取"（"之前的部分）。"""
    names = set()
    for item in record.get("inferred_fields") or []:
        names.add(str(item).split("（")[0].strip())
    return names


def _comparability(record: dict, artifact: dict) -> tuple[list[str], list[str]]:
    """返回 (不可比原因, 提示)。

    对**基线自己也是推断**的字段（见 inferred_fields），若产物缺失该字段，
    按"与基线同一时代、配置一致"处理并给提示 —— 而不是拒绝比较。
    否则基线会与自己的产物都不可比，门禁形同虚设。
    但若产物**明确记了不同的值**，仍然拒绝。
    """
    meta = artifact.get("meta") or {}
    inferred = _inferred_names(record)
    mismatches: list[str] = []
    notes: list[str] = []
    for key in COMPARABILITY_KEYS:
        expected = record.get(key)
        actual = meta.get(key)
        if actual is None:
            if key in inferred:
                notes.append(f"{key}: 产物未记录（基线该字段亦为推断，按一致处理）")
                continue
            if key == "dataset_version":
                actual = _load_dataset_module().DATASET_META.get("version")
            else:
                mismatches.append(f"{key}: 基线 {expected!r}，但产物未记录该字段")
                continue
        if str(expected) != str(actual):
            mismatches.append(f"{key}: 基线 {expected!r} ≠ 当前 {actual!r}")
    return mismatches, notes


def compare(record: dict, artifact: dict,
            tolerance_pp: float) -> tuple[list[str], list[str], list[str], list[str]]:
    """返回 (不可比原因, 提示, 回归项, 改善项)。"""
    mismatches, notes = _comparability(record, artifact)
    if mismatches:
        return mismatches, notes, [], []

    base = record.get("metrics") or {}
    regressions: list[str] = []
    improvements: list[str] = []
    for name, higher_better in (("precision", True), ("recall", True), ("f1_score", False)):
        base_value = _pct(base.get(name)) if name != "f1_score" else None
        if name == "f1_score":
            base_value = float(base.get("f1_score") or 0) * 100
            current_value = float(artifact.get("f1_score") or 0) * 100
        else:
            base_value = _pct(base.get(name))
            current_value = _pct(artifact.get(name))
        if base_value is None or current_value is None:
            continue
        delta = current_value - base_value
        if abs(delta) <= tolerance_pp:
            continue
        worse = (delta < 0) if higher_better else (delta < 0)
        rendered = f"{name}: {base_value:.1f} → {current_value:.1f}（{delta:+.1f}pp）"
        (regressions if worse else improvements).append(rendered)
    # notes 必须一路带出来：它是"该字段产物未记录、按一致处理"的唯一提示，
    # 在可比路径上丢掉会让门禁显得比实际更有把握。
    return [], notes, regressions, improvements


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实 replay 基线记录与门禁")
    parser.add_argument("action", choices=("record", "check", "show"))
    parser.add_argument("artifact", nargs="?", help="replay 产物 JSON")
    parser.add_argument("--record", default=DEFAULT_RECORD)
    parser.add_argument("--tolerance-pp", type=float, default=DEFAULT_TOLERANCE_PP)
    args = parser.parse_args(argv)

    record_path = Path(args.record)

    if args.action == "show":
        if not record_path.is_file():
            print(f"基线记录不存在: {record_path}", file=sys.stderr)
            return 1
        record = json.loads(record_path.read_text(encoding="utf-8"))
        metrics = record.get("metrics") or {}
        print(f"数据集 {record.get('dataset_id')} v{record.get('dataset_version')}")
        print(f"模型 {record.get('model')}  判据 {record.get('judge_variant')}  "
              f"窗口 ±{record.get('context_lines')}")
        print(f"决策: {record.get('decision')}")
        print(f"读数: P={metrics.get('precision')} R={metrics.get('recall')} "
              f"F1={metrics.get('f1_score')}")
        breakdown = record.get("miss_breakdown") or {}
        if breakdown:
            print("漏报构成:")
            for name, count in breakdown.items():
                print(f"  {count:>3} 条  {name}")
        return 0

    if not args.artifact:
        print(f"{args.action} 需要提供 replay 产物路径", file=sys.stderr)
        return 2
    artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    if "variants" in artifact:      # judge_ab 的产物形态
        first = next(iter(artifact["variants"].values()), {})
        artifact = first

    if args.action == "record":
        record = build_record(artifact)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        metrics = record["metrics"]
        print(f"[baseline] 已记录 -> {record_path}")
        print(f"  数据集 v{record['dataset_version']}  模型 {record['model']}  "
              f"判据 {record['judge_variant']}  窗口 ±{record['context_lines']}")
        print(f"  P={metrics['precision']} R={metrics['recall']} "
              f"F1={metrics['f1_score']}")
        return 0

    if not record_path.is_file():
        print(f"基线记录不存在: {record_path}（先用 record 生成）", file=sys.stderr)
        return 1
    record = json.loads(record_path.read_text(encoding="utf-8"))
    incomparable, notes, regressions, improvements = compare(
        record, artifact, args.tolerance_pp)

    print(f"基线: 数据集 v{record.get('dataset_version')}  "
          f"模型 {record.get('model')}  判据 {record.get('judge_variant')}  "
          f"窗口 ±{record.get('context_lines')}")
    for note in notes:
        print(f"  ℹ️ {note}")
    if incomparable:
        print("\n⚠️ 不可比较（读数不能直接对比）:")
        for item in incomparable:
            print(f"  - {item}")
        return 3
    print(f"容差 ±{args.tolerance_pp}pp")
    if not regressions and not improvements:
        print("✅ 无显著变化")
        return 0
    for item in improvements:
        print(f"  ✅ 改善 {item}")
    for item in regressions:
        print(f"  ❌ 回归 {item}")
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
