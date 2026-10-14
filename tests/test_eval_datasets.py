"""标注数据集注册表与预检的回归测试（全程离线）。

这些测试锁定的是"标注集能不能用"的前置条件，而不是模型质量：
    - 版本元信息与实际样本数必须一致（改了标注必须升版本）；
    - 每个标注点必须真实存在（文件 + 行号），行号失效直接失败；
    - 同名文件不得被静默解析到错误位置（历史上 tools/base.py 与 llm/base.py
      同名，旧实现按 basename 兜底会指错文件）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.datasets import (  # noqa: E402
    DATASETS,
    DatasetSpec,
    _build_file_index,
    load_samples,
    preflight,
    preflight_all,
    resolve_sample_file,
)

REPO_ROOT = Path(__file__).parent.parent
EXTERNAL_TARGET = Path("X:/VScode/llm-agent-qa-system")
SELF_SPEC = next(s for s in DATASETS if s.dataset_id == "code-review-agent-self")
EXTERNAL_SPEC = next(s for s in DATASETS if s.dataset_id == "llm-agent-qa-system-labeled")


def _spec(tmp_path: Path, samples_source: str) -> DatasetSpec:
    """造一个指向临时模块的数据集声明。"""
    module = tmp_path / "dataset_mod.py"
    module.write_text(samples_source, encoding="utf-8")
    return DatasetSpec(dataset_id="tmp", module_path=module.name,
                       default_target=".", description="test")


def _sample_source(samples: list[tuple], **meta) -> str:
    base = {"dataset_id": "tmp", "version": "1.0.0"}
    base.update(meta)
    return f"DATASET_META = {base!r}\nEVAL_SAMPLES = {samples!r}\n"


# ═══ 真实数据集 ═══

def test_self_dataset_preflight_passes():
    result = preflight(SELF_SPEC, repo_root=REPO_ROOT)

    assert result.passed, [i.message for i in result.errors]
    assert result.matched == result.total == 33
    assert result.skipped == 0


@pytest.mark.skipif(not EXTERNAL_TARGET.is_dir(),
                    reason="外部项目 llm-agent-qa-system 不在本机")
def test_external_dataset_preflight_passes_with_target():
    result = preflight(EXTERNAL_SPEC, target_root=EXTERNAL_TARGET, repo_root=REPO_ROOT)

    assert result.passed, [i.message for i in result.errors]
    assert result.matched == result.total == 124
    assert result.skipped == 0
    assert result.match_ratio == 1.0


def test_external_dataset_without_target_is_an_error_not_a_pass():
    """外部数据集没有目标目录时必须报错，不能假装通过。"""
    result = preflight(EXTERNAL_SPEC, target_root=None, repo_root=REPO_ROOT)

    assert not result.passed
    assert any("未提供目标项目目录" in i.message for i in result.errors)


def test_dataset_versions_are_declared_and_consistent():
    for spec in DATASETS:
        _, meta = load_samples(spec, REPO_ROOT)
        assert meta.get("version"), f"{spec.dataset_id} 缺少 version"
        assert meta.get("dataset_id") == spec.dataset_id
        assert meta.get("sample_count"), f"{spec.dataset_id} 缺少 sample_count"


def test_sample_ids_are_stable_and_unique():
    result = preflight(SELF_SPEC, repo_root=REPO_ROOT)

    assert result.sample_ids == [f"code-review-agent-self#{i}" for i in range(1, 34)]
    assert len(set(result.sample_ids)) == len(result.sample_ids)


# ═══ 元信息与实际内容不一致必须失败 ═══

def test_sample_count_mismatch_fails(tmp_path):
    spec = _spec(tmp_path, _sample_source(
        [("a.py", 1, "BUG", "High", "en", "cn", True)],
        sample_count=5))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert not result.passed
    assert any("sample_count=5" in i.message for i in result.errors)


def test_real_bug_count_mismatch_fails(tmp_path):
    spec = _spec(tmp_path, _sample_source(
        [("a.py", 1, "BUG", "High", "en", "cn", True)],
        sample_count=1, real_bug_count=7))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert any("real_bug_count" in i.message for i in result.errors)


# ═══ 标注失效必须失败 ═══

def test_missing_annotation_file_fails(tmp_path):
    spec = _spec(tmp_path, _sample_source(
        [("nope.py", 1, "BUG", "High", "en", "cn", True)], sample_count=1))
    # 放一个无关文件，避免触发"目录里没有任何 .py"的另一条错误分支
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "other.py").write_text("x = 1\n", encoding="utf-8")

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert not result.passed
    assert result.skipped == 1
    assert any("无法解析" in i.message for i in result.errors)


def test_empty_target_tree_reports_layout_mismatch(tmp_path):
    """目标目录里没有任何 .py → 报"布局不对"，而不是把每条标注都算作缺失。"""
    spec = _spec(tmp_path, _sample_source(
        [("a.py", 1, "BUG", "High", "en", "cn", True)], sample_count=1))
    (tmp_path / "src").mkdir()

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert not result.passed
    assert any("未找到任何 .py 文件" in i.message for i in result.errors)


def test_out_of_range_line_for_real_bug_fails(tmp_path):
    """真问题的标注行号必须存在：越界会让上下文指向无关位置。"""
    spec = _spec(tmp_path, _sample_source(
        [("a.py", 999, "BUG", "High", "en", "cn", True)], sample_count=1))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert not result.passed
    assert any("超出" in i.message for i in result.errors)


def test_out_of_range_line_for_fp_trap_is_a_warning(tmp_path):
    """FP 陷阱约定用越界行号表示"问题不存在"，只提醒不失败。"""
    spec = _spec(tmp_path, _sample_source(
        [("a.py", 999, "BUG", "High", "en", "cn", False)], sample_count=1))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert result.passed
    assert result.matched == 1
    assert any("FP 陷阱" in i.message for i in result.warnings)


def test_invalid_enum_values_fail(tmp_path):
    spec = _spec(tmp_path, _sample_source(
        [("a.py", 1, "TYPO", "High", "en", "cn", True),
         ("a.py", 1, "BUG", "Critical", "en", "cn", True)], sample_count=2))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert not result.passed
    messages = " ".join(i.message for i in result.errors)
    assert "TYPO" in messages and "Critical" in messages


def test_non_boolean_is_bug_fails(tmp_path):
    spec = _spec(tmp_path, _sample_source(
        [("a.py", 1, "BUG", "High", "en", "cn", 1)], sample_count=1))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    result = preflight(spec, target_root=tmp_path, repo_root=tmp_path)

    assert any("is_real_bug" in i.message for i in result.errors)


def test_malformed_sample_tuple_is_rejected(tmp_path):
    module = tmp_path / "bad.py"
    module.write_text("EVAL_SAMPLES = [('a.py', 1, 'BUG')]\n", encoding="utf-8")
    spec = DatasetSpec("bad", "bad.py", ".", "test")

    with pytest.raises(ValueError, match="结构非法"):
        load_samples(spec, tmp_path)


# ═══ 同名文件解析 ═══

def test_ambiguous_basename_is_refused_not_guessed(tmp_path):
    """同名文件出现时必须拒绝解析——旧实现会取最后一个，静默指到错误文件。"""
    for sub in ("agent/tools", "llm", "memory"):
        (tmp_path / "src" / sub).mkdir(parents=True)
        (tmp_path / "src" / sub / "base.py").write_text("x = 1\n", encoding="utf-8")
    index = _build_file_index(tmp_path, "src")

    resolved, reason = resolve_sample_file(tmp_path, "base.py", index)

    assert resolved is None
    assert "同名文件有 3 个" in reason


def test_full_relative_path_disambiguates(tmp_path):
    for sub in ("agent/tools", "llm"):
        (tmp_path / "src" / sub).mkdir(parents=True)
        (tmp_path / "src" / sub / "base.py").write_text("x = 1\n", encoding="utf-8")
    index = _build_file_index(tmp_path, "src")

    resolved, reason = resolve_sample_file(tmp_path, "agent/tools/base.py", index)

    assert resolved is not None and resolved.parts[-3:] == ("agent", "tools", "base.py")
    assert reason == ""


def test_unique_basename_is_resolved(tmp_path):
    (tmp_path / "src" / "harness").mkdir(parents=True)
    target = tmp_path / "src" / "harness" / "only.py"
    target.write_text("x = 1\n", encoding="utf-8")
    index = _build_file_index(tmp_path, "src")

    resolved, _ = resolve_sample_file(tmp_path, "only.py", index)

    assert resolved == target


def test_index_excludes_worktree_and_cache_copies(tmp_path):
    """工作树副本会制造假歧义：.worktrees/ 与 __pycache__ 必须排除。"""
    for sub in ("src/harness", ".worktrees/other/src/harness", "src/harness/__pycache__"):
        (tmp_path / sub).mkdir(parents=True)
        (tmp_path / sub / "dup.py").write_text("x = 1\n", encoding="utf-8")
    index = _build_file_index(tmp_path, "src")

    assert len(index["dup.py"]) == 1


def test_index_scoped_to_declared_search_root(tmp_path):
    """索引只扫 search_root，重复登记会把所有标注误判为歧义。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    index = _build_file_index(tmp_path, "src")

    assert len(index["a.py"]) == 1


# ═══ 命令行行为 ═══

def test_preflight_all_reports_every_registered_dataset():
    results = preflight_all()

    assert {r.dataset_id for r in results} == {s.dataset_id for s in DATASETS}
    assert all(r.summary_line() for r in results)
