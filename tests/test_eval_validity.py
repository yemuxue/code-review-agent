"""标注有效性叠加层的回归测试。

锁定三件事：
    1. STALE（语义失效）标注**不计入**召回分母 —— 否则会系统性低估模型能力；
    2. CRITERION_DISPUTED（客观但低优先级）**计入**分母 —— 它们应当被召回，
       属提示词口径问题而非标注问题，把它排除掉会掩盖真实的提升空间；
    3. 有效性映射与产物不匹配时必须**报错**，而不是静默套错样本。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.validity import (  # noqa: E402
    COUNTED_CATEGORIES,
    EXCLUDED_CATEGORIES,
    adjust,
    load_validity,
)


def _replay(tp: int, fp: int, fn: int, tn: int, fn_keys: list[tuple[str, int]]) -> dict:
    details = [[f, ln, True, False, "FN", "NO"] for f, ln in fn_keys]
    details += [["a.py", 1, True, True, "TP", "YES"]] * tp
    details += [["b.py", 1, False, True, "FP", "YES"]] * fp
    details += [["c.py", 1, False, False, "TN", "NO"]] * tn
    return {"true_positives": tp, "false_positives": fp,
            "false_negatives": fn, "true_negatives": tn,
            "details": details, "meta": {"model": "m", "dataset_version": "1.0.0"}}


def _validity_file(tmp_path: Path, entries: list[tuple[int, str]]) -> Path:
    path = tmp_path / "validity.json"
    path.write_text(json.dumps([{"id": i, "category": c, "note": ""}
                                for i, c in entries]), encoding="utf-8")
    return path


# ═══ 类别映射 ═══

def test_stale_annotations_are_excluded_from_denominator():
    """A_ANNOTATION_WEAK → STALE → 不计入分母。"""
    replay = _replay(tp=1, fp=0, fn=1, tn=0, fn_keys=[("x.py", 10)])
    validity = {1: "STALE"}

    metrics = adjust(replay, validity)

    assert metrics.stale == 1
    assert metrics.effective_denominator == 1     # 只剩 TP 那一份
    assert metrics.recall_raw == 0.5
    assert metrics.recall_effective == 1.0


def test_disputed_annotations_are_counted_in_denominator():
    """C_CRITERION_STRICT → CRITERION_DISPUTED → 计入分母（应当被召回）。"""
    replay = _replay(tp=1, fp=0, fn=1, tn=0, fn_keys=[("x.py", 10)])
    validity = {1: "CRITERION_DISPUTED"}

    metrics = adjust(replay, validity)

    assert metrics.disputed == 1
    assert metrics.effective_denominator == 2
    assert metrics.recall_effective == 0.5


def test_real_miss_counts_as_valid_and_lowers_effective_recall():
    replay = _replay(tp=1, fp=0, fn=1, tn=0, fn_keys=[("x.py", 10)])
    validity = {1: "VALID"}

    metrics = adjust(replay, validity)

    assert metrics.valid == 2                     # 1 个漏报 + 1 个已召回
    assert metrics.recall_effective == 0.5


def test_all_stale_gives_full_effective_recall():
    """极端情形：所有漏报都是失效标注 → 有效召回率 100%，这才是真实能力。"""
    keys = [("x.py", i) for i in range(5)]
    replay = _replay(tp=3, fp=0, fn=5, tn=0, fn_keys=keys)
    validity = {i: "STALE" for i in range(1, 6)}

    metrics = adjust(replay, validity)

    assert metrics.stale == 5
    assert metrics.recall_raw == pytest.approx(3 / 8)
    assert metrics.recall_effective == 1.0


# ═══ 映射来源与一致性 ═══

def test_load_validity_maps_multi_agent_categories(tmp_path):
    path = _validity_file(tmp_path, [
        (1, "A_ANNOTATION_WEAK"),
        (2, "B_CONTEXT_MISSING"),
        (3, "C_CRITERION_STRICT"),
        (4, "D_REAL_MISS"),
        (5, "E_AMBIGUOUS"),
    ])

    validity = load_validity(path)

    assert validity[1] == "STALE"
    assert validity[4] == "VALID"
    # 上下文不足与口径分歧都属"应当召回"
    assert validity[2] == validity[3] == "CRITERION_DISPUTED"
    # 无法判定时保守计入分母，避免用"不确定"去抬高指标
    assert validity[5] == "CRITERION_DISPUTED"


def test_unknown_source_category_defaults_to_counted(tmp_path):
    """未知归因类别必须保守计入分母，不能默默变成 STALE 抬高读数。"""
    path = _validity_file(tmp_path, [(1, "SOME_NEW_CATEGORY")])

    assert load_validity(path)[1] == "CRITERION_DISPUTED"


def test_counted_and_excluded_sets_are_disjoint():
    assert not (COUNTED_CATEGORIES & EXCLUDED_CATEGORIES)


def test_mismatched_validity_size_raises_instead_of_misapplying():
    """映射与漏报数不一致时必须报错：静默套错会把 STALE 算到别的样本上。"""
    replay = _replay(tp=0, fp=0, fn=2, tn=0, fn_keys=[("x.py", 1), ("y.py", 2)])

    with pytest.raises(ValueError, match="不匹配"):
        adjust(replay, {1: "STALE"})


def test_summary_lines_report_both_denominators():
    replay = _replay(tp=1, fp=0, fn=1, tn=0, fn_keys=[("x.py", 10)])

    text = "\n".join(adjust(replay, {1: "STALE"}).summary_lines())

    assert "原始口径" in text and "有效口径" in text
    assert "语义失效" in text
