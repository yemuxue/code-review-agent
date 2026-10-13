"""指标门禁与基线回归的自测（全程离线、确定性）。

覆盖本轮补上的两个闭环缺口：
    1. 阈值只有一份来源，且必需指标无样本时门禁 fail-closed；
    2. 基线快照可写可读，指标变差 / 失败计数增加会被判为回归。
"""
from __future__ import annotations

import json

import pytest

from src.eval.baseline import (
    BASELINE_VERSION,
    REQUIRED_METRICS,
    compare_to_baseline,
    evaluate_targets,
    gate_passed,
    load_baseline,
    metrics_snapshot,
    render_gate_section,
    write_baseline,
)
from src.eval.metrics import EvalReportData, Metric
from src.eval.report import TARGETS


def _data(*metrics: Metric) -> EvalReportData:
    return EvalReportData(runs=[], trajectory=list(metrics))


def _metric(name: str, value: float | None, num: float = 1, den: float = 1,
            note: str = "") -> Metric:
    return Metric(name, value, num, den, note=note)


# ═══ 目标值判定 ═══

def test_gate_passes_when_all_measurable_metrics_meet_targets():
    data = _data(_metric("工具调用成功率", 1.0), _metric("空转率", 0.0),
                 _metric("端到端成功率", 1.0))

    results = evaluate_targets(data, TARGETS)

    assert gate_passed(results)
    assert all(r.status == "pass" for r in results)


def test_gate_fails_when_metric_misses_target():
    data = _data(_metric("工具调用成功率", 0.90, 9, 10), _metric("端到端成功率", 1.0))

    results = evaluate_targets(data, TARGETS)

    failed = [r for r in results if r.is_failure]
    assert [r.name for r in failed] == ["工具调用成功率"]
    assert not gate_passed(results)


def test_required_metric_without_sample_fails_closed():
    """必需指标无样本 = 采集退化，必须拦住，不能当作"跳过通过"。"""
    data = _data(_metric("工具调用成功率", None, 0, 0, ), _metric("端到端成功率", 1.0))

    results = evaluate_targets(data, TARGETS)

    assert not gate_passed(results)
    required = [r for r in results if r.name in REQUIRED_METRICS]
    assert any(r.status == "fail" and r.value is None for r in required)


def test_optional_metric_without_sample_is_not_a_failure():
    """fix 成功率在没有开启 auto_fix 时天然无样本，不应判门禁失败。"""
    data = _data(_metric("工具调用成功率", 1.0), _metric("端到端成功率", 1.0),
                 _metric("fix 成功率", None, 0, 0, note="需 auto_fix 运行"))

    results = evaluate_targets(data, TARGETS)

    assert gate_passed(results)
    fix = next(r for r in results if r.name == "fix 成功率")
    assert fix.status == "no_sample"


def test_lower_is_better_metric_fails_when_exceeded():
    data = _data(_metric("工具调用成功率", 1.0), _metric("端到端成功率", 1.0),
                 _metric("空转率", 0.25, 1, 4))

    results = evaluate_targets(data, TARGETS)

    idle = next(r for r in results if r.name == "空转率")
    assert idle.status == "fail"


def test_metric_without_target_is_reported_but_not_enforced():
    data = _data(_metric("工具调用成功率", 1.0), _metric("端到端成功率", 1.0),
                 _metric("some new metric", 0.0))

    results = evaluate_targets(data, TARGETS)

    new = next(r for r in results if r.name == "some new metric")
    assert new.status == "no_target"
    assert gate_passed(results)


# ═══ 快照与回归比对 ═══

def test_snapshot_roundtrip_is_strict_utf8_and_versioned(tmp_path):
    data = _data(_metric("工具调用成功率", 1.0), _metric("端到端成功率", 1.0))
    path = write_baseline(metrics_snapshot(data, model="m", repeat=3), tmp_path / "b.json")

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")            # 无 BOM
    raw.decode("utf-8")                                    # 严格可解码
    loaded = load_baseline(path)
    assert loaded["version"] == BASELINE_VERSION
    assert loaded["repeat"] == 3
    assert loaded["trajectory"]["工具调用成功率"]["value"] == 1.0


def test_load_baseline_rejects_missing_or_malformed_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_baseline(tmp_path / "nope.json")

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline(bad)

    no_version = tmp_path / "nover.json"
    no_version.write_text(json.dumps({"trajectory": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_baseline(no_version)


def test_no_regression_when_current_matches_baseline():
    data = _data(_metric("工具调用成功率", 1.0), _metric("端到端成功率", 1.0))
    snapshot = metrics_snapshot(data)

    result = compare_to_baseline(snapshot, snapshot)

    assert result.passed
    assert result.regressions == []


def test_higher_is_better_metric_drop_is_a_regression():
    base = metrics_snapshot(_data(_metric("工具调用成功率", 1.0)))
    cur = metrics_snapshot(_data(_metric("工具调用成功率", 0.5, 1, 2)))

    result = compare_to_baseline(cur, base)

    assert not result.passed
    assert result.regressions and "工具调用成功率" in result.regressions[0]


def test_lower_is_better_metric_increase_is_a_regression():
    base = metrics_snapshot(_data(_metric("空转率", 0.0, 0, 10)))
    cur = metrics_snapshot(_data(_metric("空转率", 0.3, 3, 10)))

    result = compare_to_baseline(cur, base)

    assert not result.passed
    assert "空转率" in result.regressions[0]


def test_improvement_is_recorded_but_does_not_fail_the_gate():
    base = metrics_snapshot(_data(_metric("工具调用成功率", 0.5, 1, 2)))
    cur = metrics_snapshot(_data(_metric("工具调用成功率", 1.0)))

    result = compare_to_baseline(cur, base)

    assert result.passed
    assert result.improvements and "工具调用成功率" in result.improvements[0]


def test_more_failures_than_baseline_is_a_regression():
    base = metrics_snapshot(_data(_metric("工具调用成功率", 1.0)))
    cur = metrics_snapshot(_data(_metric("工具调用成功率", 1.0)))
    base["failures"] = {"F-04": 0}
    cur["failures"] = {"F-04": 2}

    result = compare_to_baseline(cur, base)

    assert not result.passed
    assert any("F-04" in item for item in result.regressions)


def test_ratio_tolerance_absorbs_small_drops():
    base = metrics_snapshot(_data(_metric("工具调用成功率", 1.0)))
    cur = metrics_snapshot(_data(_metric("工具调用成功率", 0.995, 199, 200)))

    strict = compare_to_baseline(cur, base)
    tolerant = compare_to_baseline(cur, base, ratio_tolerance=0.01)

    assert not strict.passed
    assert tolerant.passed


def test_version_mismatch_makes_comparison_explicitly_incomparable():
    base = metrics_snapshot(_data(_metric("工具调用成功率", 1.0)))
    base["version"] = BASELINE_VERSION + 1
    cur = metrics_snapshot(_data(_metric("工具调用成功率", 1.0)))

    result = compare_to_baseline(cur, base)

    # 版本不一致时不假装"无回归"，而是显式声明无法比较
    assert result.passed is True
    assert result.incomparable and "版本" in result.incomparable[0]


def test_missing_value_on_either_side_is_incomparable_not_regression():
    base = metrics_snapshot(_data(_metric("fix 成功率", 0.8, 4, 5)))
    cur = metrics_snapshot(_data(_metric("fix 成功率", None, 0, 0)))

    result = compare_to_baseline(cur, base)

    assert result.passed
    assert result.incomparable and "fix 成功率" in result.incomparable[0]


# ═══ 报告渲染与端到端接线 ═══

def test_gate_section_states_missing_baseline_explicitly(tmp_path):
    data = _data(_metric("工具调用成功率", 1.0), _metric("端到端成功率", 1.0))
    lines = render_gate_section(evaluate_targets(data, TARGETS), None, None)

    text = "\n".join(lines)
    assert "未提供基线" in text
    assert "必需指标" in text


def test_runner_gate_writes_baseline_and_compares_it(tmp_path):
    """端到端：先写基线，再用同一基线跑门禁，必须通过且报告含门禁章节。"""
    baseline = tmp_path / "baseline.json"
    report = tmp_path / "report.md"

    assert run_cli(["--suite", "offline", "--work-dir", str(tmp_path / "w1"),
                    "--report", str(report), "--gate",
                    "--write-baseline", str(baseline)]) == 0

    assert run_cli(["--suite", "offline", "--work-dir", str(tmp_path / "w2"),
                    "--report", str(report), "--gate",
                    "--baseline", str(baseline)]) == 0

    body = report.read_text(encoding="utf-8")
    assert "## 10. 指标门禁" in body
    assert "基线回归" in body
    assert "无回归" in body


def test_runner_gate_fails_on_regressed_baseline(tmp_path):
    baseline = tmp_path / "baseline.json"
    run_cli(["--suite", "offline", "--work-dir", str(tmp_path / "w1"),
             "--write-baseline", str(baseline)])

    # 人为把基线的空转率调成"更低更好"的负值 → 当前 0.0 变成回归
    data = json.loads(baseline.read_text(encoding="utf-8"))
    data["trajectory"]["空转率"] = {"value": -0.1, "numerator": 0, "denominator": 10}
    baseline.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    exit_code = run_cli(["--suite", "offline", "--work-dir", str(tmp_path / "w2"),
                         "--gate", "--baseline", str(baseline)])

    assert exit_code == 1


def run_cli(argv: list[str]) -> int:
    from tests.eval.runner import main
    return main(argv)
