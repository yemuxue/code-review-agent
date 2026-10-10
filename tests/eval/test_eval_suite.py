"""Phase 2–5 自测：故障集、重复运行与 CI 门禁必须全程离线可复现。"""

from __future__ import annotations

from src.eval.pricing import estimate_cost_usd, set_model_price
from tests.eval.cases import OFFLINE_CASES, get_case
from tests.eval.runner import main, run_offline


def test_offline_fault_suite_has_ten_unique_cases():
    assert len(OFFLINE_CASES) == 10
    assert len({case.id for case in OFFLINE_CASES}) == len(OFFLINE_CASES)
    assert get_case("EV-010").expect["failure"] == "F-01"


def test_offline_runner_covers_faults_and_gate(tmp_path):
    results, data, gate = run_offline(tmp_path / "runs", repeat=2)
    assert len(results) == 20 and all(result.passed for result in results)
    assert gate["passed"] is True
    by_name = {metric.name: metric for metric in data.stability}
    assert by_name["pass@2"].value == 1.0
    assert by_name["flaky 率"].value == 0.0


def test_runner_writes_report_and_enforces_gate(tmp_path):
    report = tmp_path / "offline.md"
    assert main(["--suite", "offline", "--work-dir", str(tmp_path / "work"),
                 "--report", str(report), "--gate"]) == 0
    assert "EV-010" in report.read_text(encoding="utf-8")


def test_pricing_never_invents_unknown_model_cost():
    assert estimate_cost_usd("unknown-model", 100, 100) is None


def test_pricing_uses_explicit_auditable_rates():
    set_model_price("test-model", 2.0, 4.0)
    assert estimate_cost_usd("test-model", 1_000_000, 500_000) == 4.0
