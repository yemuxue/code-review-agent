"""标注集标签一致性的回归测试（版本无关）。

## 为什么改成"从补丁文件推导"

旧版把 `84 / 40 / 24 / version == "1.2.0"` 写死在测试里。结果是：**每修一次数据集，
测试就失败一次**，然后必须手工改常量 —— 而手工改常量的过程本身就可能把
"标签被悄悄改回去"这件事掩盖掉。

现在改成断言**不变量**：
    - 元信息计数必须与实际样本一致（自洽）
    - 每个补丁声明的处置必须与当前标签一致（转负例的确实是负例、重定位的确实在新行）
    - 审计范围必须显式声明；补丁显示未完成时不得宣称 audit_complete=True
所以升版本时无需改测试，而"标签被改回去"仍然会失败。

## 为什么这些不变量重要

标签是评测的地面真值。它一旦与补丁记录不一致，所有基于它的 P/R/F1 都会失真，
而且**不会报错** —— 只会让读数悄悄偏离。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")
PATCHES_DIR = Path("tests/eval/patches")
FIXED_MARKER = "【已修复】"
# 最初就写成"看起来像 bug、实际无害"的陷阱数量（结构常量，不随版本变）
ORIGINAL_FP_TRAPS = 16


def _load():
    spec = importlib.util.spec_from_file_location("ds_labels", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_changes() -> list[dict]:
    """汇总所有补丁文件的 changes（旧版本在前）。"""
    changes: list[dict] = []
    if not PATCHES_DIR.is_dir():
        return changes
    for path in sorted(PATCHES_DIR.glob("v*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        changes.extend(data.get("changes") or [])
    return changes


def _samples() -> list[tuple]:
    return _load().EVAL_SAMPLES


def _by_index(sample_id: int) -> tuple:
    samples = _samples()
    assert 1 <= sample_id <= len(samples), f"补丁引用越界: #{sample_id}"
    return samples[sample_id - 1]


# ═══ 结构性不变量 ═══

def test_meta_counts_are_self_consistent():
    module = _load()
    meta, samples = module.DATASET_META, module.EVAL_SAMPLES
    positives = sum(1 for s in samples if s[6])

    assert meta["sample_count"] == len(samples)
    assert meta["real_bug_count"] == positives
    assert meta["fp_trap_count"] == len(samples) - positives


def test_sample_count_never_shrinks():
    """修标签不得删样本 —— 完备性是硬要求。"""
    assert len(_samples()) == 124


def test_negative_arithmetic_is_exact():
    """负例 = 原始 FP 陷阱 + 审计转负（带【已修复】标记）。"""
    samples = _samples()
    marked = sum(1 for s in samples if s[5].startswith(FIXED_MARKER))
    negatives = sum(1 for s in samples if not s[6])

    assert negatives == ORIGINAL_FP_TRAPS + marked


def test_marked_samples_are_negative():
    contradictory = [s for s in _samples() if s[6] and FIXED_MARKER in s[5]]

    assert not contradictory, [f"{s[0]}:{s[1]}" for s in contradictory]


def test_marked_descriptions_explain_why():
    """标记要说明"当前代码正确"，不能只留两个字让人无从判断。"""
    for sample in _samples():
        if not sample[5].startswith(FIXED_MARKER):
            continue
        assert "误报" in sample[5] or "正确" in sample[5], (
            f"{sample[0]}:{sample[1]} 的【已修复】标记缺少说明")
        assert "false positive" in sample[4] or "correct" in sample[4], (
            f"{sample[0]}:{sample[1]} 的英文描述缺少说明")


def test_original_annotations_are_traceable():
    """【已修复】标记之后必须保留原描述，便于追溯修正依据。"""
    for sample in _samples():
        if not sample[5].startswith(FIXED_MARKER):
            continue
        assert len(sample[5][len(FIXED_MARKER):].strip()) > 8, (
            f"{sample[0]}:{sample[1]} 的原始描述似乎被丢弃")


# ═══ 补丁声明与当前标签必须一致 ═══

def test_relocate_changes_point_to_new_line():
    """RELOCATE 的样本必须仍是正例，且行号已落到 new_line。"""
    relocations = [c for c in _patch_changes() if c.get("action") == "RELOCATE"]
    for change in relocations:
        sample = _by_index(int(change["sample_id"]))
        assert int(sample[1]) == int(change["new_line"]), (
            f"#{change['sample_id']} 应重定位到 {change['new_line']}，实际 {sample[1]}")
        assert sample[6] is True, f"#{change['sample_id']} 重定位后应为正例"
        assert FIXED_MARKER not in sample[5], (
            f"#{change['sample_id']} 不应带【已修复】标记")


def test_relabel_negative_changes_are_negative_and_marked():
    """RELABEL_NEGATIVE 的样本必须是负例 + 带标记（标记是可追溯的唯一线索）。"""
    changes = [c for c in _patch_changes() if c.get("action") == "RELABEL_NEGATIVE"]
    for change in changes:
        sample = _by_index(int(change["sample_id"]))
        assert sample[6] is False, f"#{change['sample_id']} 应为负例"
        assert FIXED_MARKER in sample[5], (
            f"#{change['sample_id']} 转负例但缺少【已修复】标记，无法追溯")


def test_relabel_positive_changes_are_positive():
    changes = [c for c in _patch_changes() if c.get("action") == "RELABEL_POSITIVE"]
    for change in changes:
        sample = _by_index(int(change["sample_id"]))
        assert sample[6] is True, f"#{change['sample_id']} 应为正例"
        assert FIXED_MARKER not in sample[5], (
            f"#{change['sample_id']} 已转正例却仍带【已修复】标记")


def test_describe_changes_do_not_flip_polarity():
    """DESCRIBE 只改描述，不得顺手改极性（否则记录与事实不符）。"""
    changes = [c for c in _patch_changes() if c.get("action") == "DESCRIBE"]
    if not changes:
        pytest.skip("当前补丁没有 DESCRIBE 动作")
    samples = _samples()
    positives = sum(1 for s in samples if s[6])
    assert positives == _load().DATASET_META["real_bug_count"]


# ═══ 审计完成度必须如实声明 ═══

def test_audit_status_is_declared():
    """审计覆盖范围必须写在元信息里。

    v1.3.0 的补丁只覆盖前 84 条（`_status` 字段写明），如果元信息不声明，
    读者会以为"124 条都逐条审计过"，从而把 78/46 当成最终计数。
    """
    meta = _load().DATASET_META

    assert "audit_scope" in meta and meta["audit_scope"], "必须声明审计覆盖范围"
    assert isinstance(meta.get("audit_complete"), bool), "audit_complete 必须是布尔值"


def test_partial_patch_forces_audit_incomplete():
    """补丁自述未完成时，元信息不得宣称审计完整。"""
    partial = False
    if PATCHES_DIR.is_dir():
        for path in PATCHES_DIR.glob("v*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            status = str(data.get("_status") or "")
            if "待" in status or "pending" in status.lower():
                partial = True
    meta = _load().DATASET_META
    if partial:
        assert meta.get("audit_complete") is False, (
            "补丁文件写明审计未完成，元信息却宣称 audit_complete=True")


# ═══ 数据卫生 ═══

def test_no_control_characters_in_descriptions():
    """描述里不得有控制字符。

    真实踩坑：正则反向引用 `\\1` 在源码里被写成 `\\x01`，解析后成了 U+0001。
    它不报错，只在报告/diff 里显示成不可见方块。
    """
    offenders: list[str] = []
    for position, sample in enumerate(_samples(), start=1):
        for text in (str(sample[4]), str(sample[5])):
            if any(unicodedata.category(ch) == "Cc" and ch not in "\n\t"
                   for ch in text):
                offenders.append(f"#{position}")
    assert not offenders, f"以下样本描述含控制字符: {offenders}"


def test_descriptions_are_non_empty_and_bilingual():
    """中英描述都不能是空串 —— 判分上下文与报告展示都依赖它们。"""
    for position, sample in enumerate(_samples(), start=1):
        assert str(sample[4]).strip(), f"#{position} 英文描述为空"
        assert str(sample[5]).strip(), f"#{position} 中文描述为空"
