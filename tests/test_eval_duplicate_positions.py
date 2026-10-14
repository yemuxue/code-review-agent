"""重复标注位置的检测与告警。

为什么需要：几乎所有分析脚本都用 `{(file, line): 值}` 建索引。同一位置有两条
标注时字典会**静默保留最后一条**——另一条消失，表现为共有样本数少 1、
翻转统计失真。这类问题不报错，只让读数偏离。

真实案例：`tools/calculator.py:105` 同时有
    #24 SECURITY/High「eval 允许 9**9**9 指数爆炸 DoS」
    #26 BUG/Low「eval 返回 inf/NaN 未处理」
两条都是真实问题，因此**保留**而记为 warning；但工具层必须能正确处理。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.datasets import (  # noqa: E402
    DatasetSpec,
    _duplicate_position_issues,
    preflight,
)


def _sample(file: str, line: int, is_bug: bool = True) -> tuple:
    return (file, line, "BUG", "Low", "en desc", "cn desc", is_bug)


# ═══ 单元：重复检测 ═══

def test_no_issue_when_positions_are_unique():
    issues = _duplicate_position_issues([_sample("a.py", 1), _sample("a.py", 2)])

    assert issues == []


def test_duplicate_position_is_reported_as_warning():
    issues = _duplicate_position_issues([_sample("a.py", 5), _sample("a.py", 5)])

    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "a.py:5" in issues[0].message
    assert "[1, 2]" in issues[0].message


def test_duplicate_warning_explains_the_double_count():
    """告警必须说明后果（一次检出计入多次），不能只说"重复"。"""
    issues = _duplicate_position_issues([_sample("a.py", 5), _sample("a.py", 5)])

    assert "2 次" in issues[0].message


def test_conflicting_labels_at_same_position_are_flagged():
    """同一行既算正例又算负例时，告警要额外点出矛盾。"""
    issues = _duplicate_position_issues(
        [_sample("a.py", 5, True), _sample("a.py", 5, False)])

    assert len(issues) == 1
    assert "矛盾" in issues[0].message


def test_same_labels_do_not_report_conflict():
    issues = _duplicate_position_issues(
        [_sample("a.py", 5, True), _sample("a.py", 5, True)])

    assert "矛盾" not in issues[0].message


def test_three_way_duplicate_reports_full_weight():
    issues = _duplicate_position_issues(
        [_sample("a.py", 5), _sample("a.py", 5), _sample("a.py", 5)])

    assert "3 条标注" in issues[0].message
    assert "3 次" in issues[0].message


# ═══ 集成：preflight 会把重复并进结果 ═══

def test_preflight_surfaces_duplicate_from_the_real_dataset():
    """真实数据集里的 calculator.py:105 重复必须出现在 preflight 结果中。

    这条是"防遗忘"：有人若把其中一条删掉，告警消失，本测试会失败，
    从而强制显式记录这个决定。
    """
    import importlib.util

    spec_path = Path("tests/eval_llm_agent_qa.py")
    module_spec = importlib.util.spec_from_file_location("ds_dup", spec_path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)

    issues = _duplicate_position_issues(list(module.EVAL_SAMPLES))
    messages = " ".join(i.message for i in issues)

    assert "tools/calculator.py:105" in messages


def _write_probe_dataset(root: Path, name: str, samples: list[tuple]) -> DatasetSpec:
    """在 tmp 目录里造一个可被 preflight 加载的最小数据集。

    必须走真实模块路径：preflight 从 spec.module_path 读取 EVAL_SAMPLES 与
    DATASET_META，直接传样本列表测不到"计数校验 + 重复检测"的真实链路。
    """
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")

    real = sum(1 for s in samples if s[6])
    module_path = root / f"probe_{name}.py"
    module_path.write_text(
        "DATASET_META = {\n"
        f"    'dataset_id': 'probe-{name}',\n"
        "    'version': '1.0.0',\n"
        f"    'sample_count': {len(samples)},\n"
        f"    'real_bug_count': {real},\n"
        f"    'fp_trap_count': {len(samples) - real},\n"
        "}\n"
        f"EVAL_SAMPLES = {samples!r}\n",
        encoding="utf-8")
    return DatasetSpec(
        dataset_id=f"probe-{name}",
        module_path=module_path.name,
        default_target=".",
        description="测试用探针数据集",
    )


def test_preflight_reports_warning_severity_for_duplicate(tmp_path):
    """preflight 对重复位置只报 warning，不报 error —— 重复本身不是错。"""
    samples = [_sample("a.py", 1), _sample("a.py", 1)]
    spec = _write_probe_dataset(tmp_path, "dup", samples)

    result = preflight(spec, tmp_path, repo_root=tmp_path)

    assert result.errors == []
    assert any("有 2 条标注" in i.message for i in result.warnings)


def test_preflight_is_clean_when_positions_and_counts_are_consistent(tmp_path):
    samples = [_sample("a.py", 1), _sample("a.py", 2, is_bug=False)]
    spec = _write_probe_dataset(tmp_path, "clean", samples)

    result = preflight(spec, tmp_path, repo_root=tmp_path)

    assert result.errors == []
    assert result.warnings == []


def test_preflight_detects_meta_count_mismatch(tmp_path):
    """计数元信息与实际样本不符时必须是 error（分母依赖它）。"""
    samples = [_sample("a.py", 1), _sample("a.py", 2)]
    spec = _write_probe_dataset(tmp_path, "mismatch", samples)
    # 手工篡改样本数
    path = tmp_path / "probe_mismatch.py"
    path.write_text(path.read_text(encoding="utf-8").replace(
        "'sample_count': 2", "'sample_count': 99"), encoding="utf-8")

    result = preflight(spec, tmp_path, repo_root=tmp_path)

    assert any("sample_count" in i.message for i in result.errors)
