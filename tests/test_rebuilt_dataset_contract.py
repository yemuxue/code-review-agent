"""重建测试集的契约测试 / Contract tests for the rebuilt dataset.

与 `tests/eval/verify_dataset.py` 的分工：
- `verify_dataset.py` 是给人看的体检命令（输出报告 + JSON）；
- 本文件是给 CI 看的断言，保证「重建过的数据集不会被悄悄改回去」。

为什么需要它
    2026-10-07 的事故是：29 条标注描述的问题在当前代码里已不存在，但
    `preflight()` 只校验「文件存在 + 行号在范围内」，所以全部静默通过。
    数据集随后在 2026-10-13 重建为 v1.2.0，又在 2026-10-14 经 6 名审计员
    逐条独立复核后修正为 **v1.3.0**（6 条正例描述的缺陷根本不存在 → 转负例；
    3 条位置漂移；9 条描述与代码不符）。重建结果必须被**锁住**：

      1. 版本号与正/负例数量必须自洽（改标注必须同步升版本）；
      2. 每条标注的锚点指纹必须仍与当前代码一致（目标仓库改动即失败）；
      3. 对外引用的 replay 读数必须标注 dataset_version，且必须与当前版本一致
         —— 否则「模型变好了」和「标注集变了」无法区分。

前两项离线可跑（锚点项在目标仓库缺席时跳过）；第三项在产物不存在时跳过。

版本沿革（分母 = 正例数 = 召回分母）
    1.0.0  108  原始标注
    1.1.0   81  29 条失效标注处置（2 重定位 + 27 翻转负例）
    1.2.0   84  复核发现 3 条翻转负例位置另含真实缺陷，改回正例
    1.3.0   78  逐条独立审计：6 条正例描述的缺陷不存在 → 转负例
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval import anchors as anchors_mod
from tests.eval import verify_dataset as vd
from tests.eval.datasets import DATASETS, load_samples, preflight

DATASET_ID = "llm-agent-qa-system-labeled"

# 重建后的基准值：改动数据集时必须同步更新，并递增 DATASET_META.version。
# ⚠️ 这是**故意的绊线**，不是漏同步：版本或计数一变就必须有人显式改这里，
# 避免"标签被悄悄改动、读数却继续沿用旧口径"。
# 版本沿革：1.0.0(108) → 1.1.0(81) → 1.2.0(84) → 1.3.0(78) → 1.3.1(78，仅修描述)
EXPECTED_VERSION = "1.3.1"
EXPECTED_TOTAL = 124
EXPECTED_REAL_BUGS = 78
EXPECTED_FP_TRAPS = 46
MANIFEST = Path("tests/eval") / f"anchors_{DATASET_ID}.json"

# 外部目标仓库；不存在时锚点断言跳过（CI 上通常没有外部副本）
TARGET_DIR = Path("X:/VScode/llm-agent-qa-system")


def _spec():
    spec = next((s for s in DATASETS if s.dataset_id == DATASET_ID), None)
    assert spec is not None, f"数据集未注册: {DATASET_ID}"
    return spec


def test_version_and_counts_are_self_consistent():
    """版本号与样本计数必须自洽——改了标注就要升版本。"""
    samples, meta = load_samples(_spec())
    assert meta["version"] == EXPECTED_VERSION, (
        f"数据集版本变了（{meta['version']} != {EXPECTED_VERSION}）："
        "若确为有意变更，请同步更新本文件的基准值")
    assert len(samples) == EXPECTED_TOTAL
    real = sum(1 for s in samples if s[6])
    assert real == EXPECTED_REAL_BUGS, f"正例数 {real} != {EXPECTED_REAL_BUGS}"
    assert len(samples) - real == EXPECTED_FP_TRAPS, "负例数不符"
    # 元信息与实际必须一致（preflight 也会查，这里提前给出更清晰的失败信息）
    assert meta["real_bug_count"] == real
    assert meta["fp_trap_count"] == len(samples) - real


def test_anchor_manifest_covers_every_sample():
    """锚点清单必须覆盖全部样本——样本增删后必须重建锚点。"""
    manifest = anchors_mod.load_manifest(MANIFEST)
    samples, _ = load_samples(_spec())
    assert manifest.get("anchor_version") == anchors_mod.ANCHOR_VERSION
    assert len(manifest.get("anchors", {})) == len(samples), (
        "锚点条目数与样本数不一致：样本已变更，需重建锚点清单")


@pytest.mark.skipif(not TARGET_DIR.is_dir(),
                    reason="外部目标仓库不存在，跳过锚点比对")
def test_no_stale_annotations_in_target_repo():
    """核心契约：不得存在与当前代码不一致的标注。

    这条断言就是 2026-10-07 事故的回归测试——当时 29 条标注全部通过了
    preflight，却没有一条检查「该行是否仍是标注所描述的问题」。
    """
    manifest = anchors_mod.load_manifest(MANIFEST)
    samples, _ = load_samples(_spec())
    report = anchors_mod.verify(samples, TARGET_DIR, manifest)
    assert not report.needs_review, (
        "存在需人工复核的标注（描述的问题可能已被修复）：\n"
        + "\n".join(f"  {c.status} {c.sample_id} {c.file}:{c.line} — {c.note}"
                    for c in report.needs_review))


@pytest.mark.skipif(not TARGET_DIR.is_dir(),
                    reason="外部目标仓库不存在，跳过预检")
def test_preflight_passes_on_target_repo():
    """预检无 error 级问题（warning 允许，例如同一位置有两条标注）。"""
    result = preflight(_spec(), TARGET_DIR)
    assert result.passed, "预检存在 error：\n" + "\n".join(
        f"  {i.sample_id} {i.message}" for i in result.errors)
    assert result.matched == result.total, "存在被跳过的样本"


def test_health_report_flag_matches_reality():
    """体检结论必须与预检/锚点结果一致（防止报告层把 fail 说成 pass）。"""
    if not TARGET_DIR.is_dir():
        pytest.skip("外部目标仓库不存在")
    res = vd.report(str(TARGET_DIR), MANIFEST, [], vd.DEFAULT_VALIDITY)
    assert res["dataset_version"] == EXPECTED_VERSION
    assert res["ready_for_evaluation"] is True


def test_replay_artifacts_declare_dataset_version():
    """凡带 meta 的 replay 产物都必须声明 dataset_version。

    没有版本号的历史产物无法归属到某个数据集版本，因此不能与当前读数混用
    —— 这正是历史上出现三份矛盾读数的根因。

    约定：`meta` 完全为空的是"前版本时代"的历史文件（无法追溯，不强制）；
    一旦有 meta，就必须带上 dataset_version，否则新的产物会重蹈覆辙。
    """
    reports = Path("reports")
    if not reports.is_dir():
        pytest.skip("reports 目录不存在")
    artifacts = sorted(reports.glob("*_replay.json"))
    if not artifacts:
        pytest.skip("没有 replay 产物")
    offenders = []
    pre_versioning = []
    for path in artifacts:
        meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        if not meta:
            pre_versioning.append(path.name)
            continue
        if not meta.get("dataset_version"):
            offenders.append(path.name)
    assert not offenders, (
        "以下产物有 meta 但缺少 dataset_version，无法归属，不得对外引用：\n  "
        + "\n  ".join(offenders))
    if pre_versioning:
        print("前版本时代产物（无 meta，仅作历史留档）：" + ", ".join(pre_versioning))
