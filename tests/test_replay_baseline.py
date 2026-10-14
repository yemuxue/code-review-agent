"""真实 replay 基线门禁的回归测试。

为什么单独测门禁：基线的作用是"下次改动退化时自动发现"。如果门禁本身会
**误报**（把噪声当回归）或**漏报**（把不可比的读数当可比），它就比没有更糟——
因为它会给人一种被守护着的错觉。

本组测试锁定三件事：
    1. 可比性判断：配置/版本不一致时必须拒绝比较，而不是给一个看似合理的差值；
    2. 容差：真实模型在同一配置下有轻微波动，容差内不报回归；
    3. 方向：精确率/召回率下降是回归，上升是改善。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.replay_baseline import (  # noqa: E402
    COMPARABILITY_KEYS,
    build_record,
    compare,
)


def _artifact(meta: dict | None = None, **metrics) -> dict:
    """构造 replay 产物。meta 覆盖走 meta=...，指标覆盖走关键字参数。"""
    merged_meta = {
        "dataset_version": "1.2.0",
        "model": "gpt-6-sol",
        "judge_variant": "strict",
        "context_lines": 15,
        "scoring": "per_sample_llm_judgement",
        "run_at": "2026-10-13T00:00:00+08:00",
    }
    merged_meta.update(meta or {})
    artifact = {
        "precision": "94.0%", "recall": "56.0%", "f1_score": "0.70",
        "true_positives": 47, "false_positives": 3,
        "false_negatives": 37, "true_negatives": 37,
        "meta": merged_meta,
    }
    artifact.update(metrics)
    return artifact


@pytest.fixture
def record() -> dict:
    built = build_record(_artifact())
    # 推断字段在测试里显式给出，避免依赖仓库里真实记录的措辞
    built["inferred_fields"] = ["judge_variant（测试用）", "context_lines（测试用）"]
    return built


# ═══ 记录内容 ═══

def test_record_uses_dataset_module_version_not_artifact_meta():
    """标签版本必须以数据集模块为准。

    重算产物的 meta 带的是**原始运行**时的旧版本号；若照抄，基线会标错版本，
    后续所有可比性判断都建立在错误前提上。
    """
    built = build_record(_artifact(meta={"dataset_version": "1.0.0"}))

    assert built["dataset_version"] != "1.0.0"
    assert built["dataset_version_source"]


def test_record_marks_inferred_fields():
    """产物没记录的字段必须显式标为推断，不能假装是记录到的。"""
    built = build_record({
        "precision": "94.0%", "recall": "56.0%", "f1_score": "0.70",
        "meta": {"model": "m", "dataset_version": "1.2.0"},
    })

    assert any("judge_variant" in item for item in built["inferred_fields"])
    assert any("context_lines" in item for item in built["inferred_fields"])


def test_record_carries_rationale_and_limitations():
    """基线必须自带"为什么接受这个数"和已知局限，否则数字会被误读。"""
    built = build_record(_artifact())

    assert built["rationale"]
    assert built["known_limitations"]


def test_record_includes_confusion_matrix():
    built = build_record(_artifact())

    assert built["metrics"]["true_positives"] == 47
    assert built["metrics"]["false_negatives"] == 37


# ═══ 可比性 ═══

def test_identical_run_is_comparable_and_unchanged(record):
    incomparable, _notes, regressions, improvements = compare(
        record, _artifact(), tolerance_pp=2.0)

    assert incomparable == []
    assert regressions == []
    assert improvements == []


@pytest.mark.parametrize("field,other", [
    ("dataset_version", "1.1.0"),
    ("model", "gpt-5.5"),
    ("judge_variant", "lenient"),
    ("context_lines", 40),
    ("scoring", "offline_stub_judge"),
])
def test_any_comparability_field_mismatch_refuses_comparison(record, field, other):
    """版本/模型/判据/窗口/判分方式任一不同都不许相减。"""
    incomparable, _notes, regressions, _improvements = compare(
        record, _artifact(meta={field: other}), tolerance_pp=2.0)

    assert incomparable, f"{field} 不一致时应当拒绝比较"
    assert regressions == [], "不可比时不应给出回归结论"


def test_missing_inferred_field_is_tolerated_with_note(record):
    """基线自己也是推断的字段，产物缺失时按一致处理并给提示。"""
    artifact = _artifact()
    del artifact["meta"]["judge_variant"]

    incomparable, notes, _regressions, _improvements = compare(
        record, artifact, tolerance_pp=2.0)

    assert incomparable == []
    assert any("judge_variant" in note for note in notes)


def test_missing_non_inferred_field_refuses(record):
    """非推断字段缺失时必须拒绝：那是"不知道"，不是"一致"。"""
    record["inferred_fields"] = []
    artifact = _artifact()
    del artifact["meta"]["judge_variant"]

    incomparable, _notes, _regressions, _improvements = compare(
        record, artifact, tolerance_pp=2.0)

    assert incomparable


def test_comparability_keys_cover_the_identifying_config():
    assert set(COMPARABILITY_KEYS) == {
        "dataset_version", "model", "judge_variant", "context_lines", "scoring"}


# ═══ 容差与方向 ═══

def test_small_drop_within_tolerance_is_not_a_regression(record):
    """真实模型有轻微波动，容差内不应报回归。"""
    _inc, _n, regressions, _imp = compare(
        record, _artifact(recall="55.0%"), tolerance_pp=2.0)

    assert regressions == []


def test_recall_drop_beyond_tolerance_is_a_regression(record):
    _inc, _n, regressions, _imp = compare(
        record, _artifact(recall="50.0%"), tolerance_pp=2.0)

    assert any("recall" in item for item in regressions)


def test_precision_drop_beyond_tolerance_is_a_regression(record):
    _inc, _n, regressions, _imp = compare(
        record, _artifact(precision="85.0%"), tolerance_pp=2.0)

    assert any("precision" in item for item in regressions)


def test_improvement_is_reported_not_as_regression(record):
    _inc, _n, regressions, improvements = compare(
        record, _artifact(recall="70.0%"), tolerance_pp=2.0)

    assert regressions == []
    assert any("recall" in item for item in improvements)


def test_zero_tolerance_reports_any_change(record):
    _inc, _n, regressions, _improvements = compare(
        record, _artifact(recall="55.0%"), tolerance_pp=0.0)

    assert regressions, "容差为 0 时任何下降都算回归"


# ═══ CLI 与磁盘记录 ═══

def test_repo_baseline_record_is_valid_json_and_self_consistent():
    """仓库里的基线记录必须可解析、且元信息与数据集一致。"""
    path = Path("tests/eval/replay_baseline.json")
    if not path.is_file():
        pytest.skip("仓库内尚无基线记录")

    record = json.loads(path.read_text(encoding="utf-8"))

    assert record["record_version"] == 1
    assert record["dataset_version"]
    assert record["model"]
    assert record["decision"]
    breakdown = record.get("miss_breakdown") or {}
    if breakdown:
        metrics = record["metrics"]
        # 漏报构成必须与混淆矩阵的 FN 相加一致（否则数字自相矛盾）
        assert sum(breakdown.values()) == metrics["false_negatives"]
