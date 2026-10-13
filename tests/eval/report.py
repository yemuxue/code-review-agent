"""统一 runner 的报告附加内容：用例结果、稳定性和门禁状态。

⚠️ 口径说明（本轮修正的核心问题）
    报告里出现的指标必须只有一个口径，否则同一次运行会出现
    "第 2 节工具调用成功率 37.5% ❌ / 第 8 节门禁 100% 通过" 这种自相矛盾。

    因此本模块把两类数据分开呈现，并在标题上写明口径：
      - **健康链路数据**（无注入故障）→ 主指标表与门禁判定都用它；
      - **故障注入数据**（含 10 类预期故障）→ 单独成节，只作为"预期信号分布"，
        绝不参与成功率门禁（注入故障本来就会把成功率拉低）。
"""

from __future__ import annotations

from datetime import datetime

from src.eval.baseline import IndicatorResult, RegressionResult, render_gate_section
from src.eval.metrics import EvalReportData
from src.eval.report import render_report
from tests.eval.faults import FaultResult


def render_evaluation_report(health_data: EvalReportData, fault_data: EvalReportData,
                             results: list[FaultResult], repeat: int,
                             gate: dict[str, float | bool], *,
                             indicators: list[IndicatorResult] | None = None,
                             regression: RegressionResult | None = None,
                             baseline_path: str | None = None) -> str:
    """渲染评测报告。

    主指标表来自 health_data（与门禁同源）；fault_data 单独成节，明确标注
    "预期故障数据，不参与门禁"。这样读者不需要交叉比对两张表去猜哪个是基准。
    """
    lines = [render_report(health_data, source="健康链路（无注入故障）— 门禁与基线口径",
                           generated_at=datetime.now()),
             "",
             "> **口径提示**：以上指标全部来自健康链路。第 8 节是故障注入链路的",
             "> 数据分布，两者不可直接比较，门禁只判定健康链路的数值。",
             "",
             "## 8. 故障注入链路数据（预期故障，非门禁口径）", "",
             f"- 健康链路运行数: {health_data.coverage['runs']}",
             f"- 故障注入运行数: {fault_data.coverage['runs']}",
             "- 故障注入会主动制造 F-01/F-02/F-04/F-05/F-07 等失败，",
             "  因此该链路的成功率天然偏低，**不作为任何门禁阈值**。", "",
             "### 8.1 离线故障注入结果", "",
             f"- 重复次数: {repeat}", "",
             "| 用例 | 结果 | 说明 |", "|------|------|------|"]
    for result in results:
        lines.append(f"| {result.case_id} | {'通过' if result.passed else '失败'} | {result.detail} |")

    lines += ["", "### 8.2 故障注入链路的失败分布", ""]
    from src.eval.metrics import failure_summary
    summary = failure_summary(fault_data.failures)
    if summary:
        lines += ["| 代码 | 类型 | 次数 |", "|------|------|------|"]
        for code, name, count in summary:
            lines.append(f"| {code} | {name} | {count} |")
    else:
        lines.append("未检出分类失败。")
    lines.append("")

    lines += ["## 9. 健康链路成功率门禁", "",
              f"- 健康工具调用成功率: {gate['tool_success_rate']:.1%}",
              f"- 健康端到端成功率: {gate['e2e_success_rate']:.1%}",
              f"- 门禁结果: {'通过' if gate['passed'] else '失败'}", ""]

    if indicators is not None:
        lines += render_gate_section(indicators, regression, baseline_path)
    else:
        lines += ["## 10. 指标门禁", "", "- 本次运行未启用指标门禁判定。", ""]
    return "\n".join(lines)
