"""数据集 v1.1.0 标签修正的回归测试。

背景：2026-10-13 逐条读源码发现 29 条标注描述的是**当前代码里已不存在的缺陷**
（占漏报的 48%）。处置为 2 条 RELOCATE + 27 条 RELABEL_NEGATIVE，
且**不删除任何样本**以保持测试集完备。

这些测试锁定三件事：
    1. 总数仍为 124（完备性）——修正标签不等于缩小测试集；
    2. 被翻转的 27 条带【已修复】标记，且都是负例；
    3. 元信息（version / 计数）与实际构成一致 —— 分母变了就必须升版本，
       否则跨版本读数会被误当成"模型变好/变差"。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")
FIXED_MARKER = "【已修复】"

# v1.2.0 的构成基线：写死是为了让任何无意的变动都失败，
# 而不是让"标签被悄悄改回去"不声不响地发生。
EXPECTED_TOTAL = 124
EXPECTED_REAL_BUGS = 84
EXPECTED_NEGATIVES = 40
EXPECTED_FIXED_MARKERS = 24
# RELOCATE 保持正例的 2 条（行号已修正到真实缺陷位置）
RELOCATED = {("prompt.py", 76), ("prompt.py", 106)}
# v1.2.0 复核后改回正例的 3 条（原标注失效，但位置仍有其它真实缺陷）
FLIPPED_BACK = {
    ("react_agent.py", 222),
    ("rag/retriever.py", 78),
    ("config/settings.py", 65),
}


def _load():
    spec = importlib.util.spec_from_file_location("ds_v110", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_total_sample_count_is_unchanged():
    """修正标签不得缩小测试集 —— 完备性是硬要求。"""
    samples = _load().EVAL_SAMPLES

    assert len(samples) == EXPECTED_TOTAL


def test_composition_matches_recorded_baseline():
    samples = _load().EVAL_SAMPLES
    real = sum(1 for s in samples if s[6])

    assert real == EXPECTED_REAL_BUGS
    assert len(samples) - real == EXPECTED_NEGATIVES


def test_meta_counts_and_version_match_reality():
    module = _load()
    meta = module.DATASET_META
    real = sum(1 for s in module.EVAL_SAMPLES if s[6])
    negatives = len(module.EVAL_SAMPLES) - real

    assert meta["version"] == "1.2.0", (
        "标签构成变化后必须升版本：召回分母 108 → 81 → 84，"
        "旧读数与新读数不可直接比较")
    assert meta["sample_count"] == len(module.EVAL_SAMPLES)
    assert meta["real_bug_count"] == real
    assert meta["fp_trap_count"] == negatives


def test_flipped_samples_are_marked_and_negative():
    """27 条翻转样本必须同时满足：带【已修复】标记 且 is_bug=False。"""
    samples = _load().EVAL_SAMPLES
    marked = [s for s in samples if s[5].startswith(FIXED_MARKER)]

    assert len(marked) == EXPECTED_FIXED_MARKERS
    assert all(s[6] is False for s in marked), "带【已修复】标记的必须是负例"


def test_no_positive_sample_is_marked_as_fixed():
    """正例不应带【已修复】标记：那说明标记与标签自相矛盾。"""
    samples = _load().EVAL_SAMPLES
    contradictory = [s for s in samples if s[6] and FIXED_MARKER in s[5]]

    assert not contradictory, [f"{s[0]}:{s[1]}" for s in contradictory]


def test_marked_descriptions_explain_why():
    """标记要说明"当前代码正确"，不能只写两个字让读者无从判断。"""
    samples = _load().EVAL_SAMPLES
    for sample in samples:
        if not sample[5].startswith(FIXED_MARKER):
            continue
        assert "误报" in sample[5] or "正确" in sample[5], (
            f"{sample[0]}:{sample[1]} 的【已修复】标记缺少说明")
        assert "false positive" in sample[4] or "correct" in sample[4], (
            f"{sample[0]}:{sample[1]} 的英文描述缺少说明")


def test_relocated_samples_stay_positive_at_new_lines():
    """RELOCATE 的样本必须仍是正例（缺陷仍在，只是漂移了）。"""
    samples = {(str(s[0]), int(s[1])): s for s in _load().EVAL_SAMPLES}

    for key in RELOCATED:
        assert key in samples, f"重定位后的 {key} 不在数据集中"
        assert samples[key][6] is True, f"{key} 应为正例（缺陷仍存在）"
        assert FIXED_MARKER not in samples[key][5], f"{key} 不应带【已修复】标记"


def test_original_annotations_are_traceable():
    """原始描述必须保留在【已修复】标记之后，便于追溯修正依据。"""
    samples = _load().EVAL_SAMPLES
    marked = [s for s in samples if s[5].startswith(FIXED_MARKER)]

    # 标记之后应还有实质内容（原描述），不是空串
    for sample in marked:
        rest = sample[5][len(FIXED_MARKER):].strip()
        assert len(rest) > 8, f"{sample[0]}:{sample[1]} 的原始描述似乎被丢弃"


def test_flipped_back_samples_are_positive_with_new_descriptions():
    """v1.2.0 复核后改回正例的 3 条：必须是正例、无【已修复】标记、描述已更新。

    这 3 条的关键在于"描述换成**新发现**的缺陷"，而不是恢复原标注 ——
    原标注已确认不成立，恢复它等于把失效标注放回数据集。
    """
    samples = {(str(s[0]), int(s[1])): s for s in _load().EVAL_SAMPLES}

    for key in FLIPPED_BACK:
        assert key in samples, f"{key} 不在数据集中"
        sample = samples[key]
        assert sample[6] is True, f"{key} 应为正例（位置仍有真实缺陷）"
        assert FIXED_MARKER not in sample[5], f"{key} 不应带【已修复】标记"
        # 新描述不能是原标注的照搬：原标注已确认与代码矛盾
        assert "未经" in sample[5] or "死代码" in sample[5] or "无引用" in sample[5], (
            f"{key} 的描述看起来没有换成新发现的缺陷")


def test_flipped_back_count_matches_negatives_arithmetic():
    """算术自洽：负例 = 原始 FP 陷阱 + 【已修复】标记数（不能被悄悄改数）。"""
    samples = _load().EVAL_SAMPLES
    marked = sum(1 for s in samples if s[5].startswith(FIXED_MARKER))
    negatives = sum(1 for s in samples if not s[6])

    # 16 条原始 FP 陷阱 + 被标记为【已修复】的负例 = 全部负例
    assert negatives == 16 + marked
