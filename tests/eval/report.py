"""统一 runner 的报告附加内容：用例结果、稳定性和门禁状态。"""

from __future__ import annotations

from datetime import datetime

from src.eval.metrics import EvalReportData
from src.eval.report import render_report
from tests.eval.faults import FaultResult


def render_evaluation_report(data: EvalReportData, results: list[FaultResult], repeat: int,
                             gate: dict[str, float | bool]) -> str:
    """在统一指标报告后追加离线用例与门禁事实，不把预期故障误报为产品回归。"""
    lines = [render_report(data, source="tests/eval 离线故障注入", generated_at=datetime.now()),
             "## 7. 离线故障注入结果", "",
             f"- 重复次数: {repeat}",
             "- 注入故障属于预期数据，不参与健康链路成功率门禁。", "",
             "| 用例 | 结果 | 说明 |", "|------|------|------|"]
    for result in results:
        lines.append(f"| {result.case_id} | {'通过' if result.passed else '失败'} | {result.detail} |")
    lines += ["", "## 8. CI 门禁", "",
              f"- 健康工具调用成功率: {gate['tool_success_rate']:.1%}",
              f"- 健康端到端成功率: {gate['e2e_success_rate']:.1%}",
              f"- 门禁结果: {'通过' if gate['passed'] else '失败'}", ""]
    return "\n".join(lines)
