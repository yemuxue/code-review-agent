"""评测门禁与基线回归比对 / Eval gate & baseline regression.

解决的问题（本轮补齐）：
    1. 阈值此前只存在于报告渲染层（`src/eval/report.py::TARGETS`），
       runner 的 `--gate` 只硬编码校验两个健康链路指标——其余指标
       "算出来了但永远不拦人"，指标层与门禁层是断开的。
    2. 历史报告只有 markdown，没有机器可读基线，两次运行之间
       "变好了还是变差了"只能靠人眼看报告，CI 无法做回归拦截。

设计原则：
    - 单一阈值来源：门禁直接消费报告层的 `TARGETS`，不复制一份。
    - 硬指标 fail-closed：标注为必需（required）的指标如果**没有样本**，
      门禁判定为失败而不是"跳过通过"——数据缺失是采集退化，必须拦住。
    - 软指标按需判定：只在有样本时对照目标，缺样本记为 n/a 不计失败
      （例如没有开启 auto_fix 时 fix 成功率天然无样本）。
    - 回归比对带容差：比例类指标允许 ratio_tolerance 的相对下降，
      避免把浮点噪声和极小样本波动当成回归。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from src.eval.metrics import EvalReportData, Metric

# 基线快照格式版本：字段含义变更时必须升版本，避免旧基线被误用。
BASELINE_VERSION = 1

# 必须存在的指标：缺失 = 采集链路退化 = 门禁失败。
# 只列"健康链路必然产生样本"的指标；故障注入链路的指标不在此处。
REQUIRED_METRICS: frozenset[str] = frozenset({
    "工具调用成功率",
    "端到端成功率",
    "工具事件配对率",
})


@dataclass
class IndicatorResult:
    """单个指标的判定结果。"""

    name: str
    value: float | None
    target: float | None
    direction: str
    status: str  # pass / fail / no_target / no_sample
    detail: str = ""

    @property
    def is_failure(self) -> bool:
        return self.status == "fail"


@dataclass
class RegressionResult:
    """与基线比对的结果。"""

    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    incomparable: list[str] = field(default_factory=list)
    baseline_meta: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.regressions


def evaluate_targets(data: EvalReportData, targets: dict) -> list[IndicatorResult]:
    """按目标值逐项判定轨迹 / 稳定性 / 采集健康度指标。

    targets 形如 report.TARGETS: {指标名: (阈值, "≥"|"≤")}。
    数据源缺失（value is None）时：必需指标判 fail，其余判 no_sample。

    三个分区都参与判定：稳定性（pass@k/flaky）与采集健康度（配对率/坏行率/
    未知事件率/未收尾率）此前完全不参与门禁，导致"采集退化"和"结果抖动"
    永远不会拦住发布。
    """
    results: list[IndicatorResult] = []
    for metric in [*data.trajectory, *data.stability, *data.collection]:
        entry = targets.get(metric.name)
        if entry is None:
            results.append(IndicatorResult(metric.name, metric.value, None, "", "no_target",
                                           detail=metric.note or "未定义目标值"))
            continue
        limit, direction = entry
        if metric.value is None:
            status = "fail" if metric.name in REQUIRED_METRICS else "no_sample"
            results.append(IndicatorResult(
                metric.name, None, limit, direction, status,
                detail=f"无样本（{metric.note or '数据源缺失'}）"))
            continue
        ok = metric.value >= limit if direction == "≥" else metric.value <= limit
        detail = (f"{metric.value:.1%} {direction} {limit:.1%}"
                  f"（{int(metric.denominator)} 个样本）")
        results.append(IndicatorResult(metric.name, metric.value, limit, direction,
                                       "pass" if ok else "fail", detail=detail))
    return results


def gate_passed(results: list[IndicatorResult]) -> bool:
    """门禁是否通过：任何 fail 都算不通过。"""
    return not any(r.is_failure for r in results)


def metrics_snapshot(data: EvalReportData, *, model: str | None = None,
                     suite: str = "offline", repeat: int = 1) -> dict:
    """把一次评测的结果压成机器可读基线快照。

    只存"可用于回归比对"的数值与样本量：不存证据明细（体积大且含日志片段）。
    """
    def _entry(metric: Metric) -> dict:
        return {"value": metric.value,
                "numerator": metric.numerator,
                "denominator": metric.denominator}

    return {
        "version": BASELINE_VERSION,
        "suite": suite,
        "repeat": repeat,
        "model": model,
        "trajectory": {m.name: _entry(m) for m in data.trajectory},
        "stability": {m.name: _entry(m) for m in data.stability},
        "collection": {m.name: _entry(m) for m in data.collection},
        "tokens": data.tokens,
        "parallel": data.parallel,
        "coverage": data.coverage,
        "failures": {code: len(items) for code, items in data.failures.items()},
    }


def compare_to_baseline(current: dict, baseline: dict,
                        ratio_tolerance: float = 0.0) -> RegressionResult:
    """逐个指标比对基线与当前快照。

    判定规则（见模块 docstring）：
        - 只比较两侧都有数值的指标；任一侧缺失记入 incomparable 并给出原因。
        - 指标方向按名称语义决定：成功率/解析率类越高越好（降 = 回归），
          空转率/循环率/回归引入率/预算耗尽率类越低越好（升 = 回归）。
        - 允许 ratio_tolerance 的相对下降（默认 0，即任何下降都算回归）。
    """
    result = RegressionResult(baseline_meta={
        "version": baseline.get("version"),
        "suite": baseline.get("suite"),
        "model": baseline.get("model"),
        "repeat": baseline.get("repeat"),
    })

    if baseline.get("version") != BASELINE_VERSION:
        result.incomparable.append(
            f"基线版本 {baseline.get('version')!r} 与当前 {BASELINE_VERSION} 不一致，跳过比对")
        return result

    for section in ("trajectory", "stability", "collection"):
        base_section = baseline.get(section) or {}
        cur_section = current.get(section) or {}
        for name, cur in cur_section.items():
            base = base_section.get(name)
            if base is None:
                result.incomparable.append(f"{name}: 基线缺少该指标")
                continue
            cur_value, base_value = cur.get("value"), base.get("value")
            if cur_value is None or base_value is None:
                result.incomparable.append(
                    f"{name}: 数值缺失（基线 {base_value!r} → 当前 {cur_value!r}）")
                continue
            delta = cur_value - base_value
            worse = _is_worse(name, delta)
            if abs(delta) <= max(ratio_tolerance * abs(base_value), 1e-12):
                continue
            rendered = (f"{name}: {base_value:.4f} → {cur_value:.4f}"
                        f"（{'+' if delta >= 0 else ''}{delta:.4f}，"
                        f"样本 {int(base.get('denominator', 0))} → {int(cur.get('denominator', 0))}）")
            (result.regressions if worse else result.improvements).append(rendered)

    _compare_coverage(current, baseline, result)
    _compare_failure_counts(current, baseline, result)
    return result


def _compare_coverage(current: dict, baseline: dict, result: RegressionResult) -> None:
    """采集覆盖度比对：覆盖运行的采集能力下降即视为回归。

    以前 coverage 只存不比，于是"遥测退化"（例如多 Agent 日志不再写 node_stats、
    或大量运行未收尾）永远不会被判成回归——而它会让所有依赖该数据源的指标
    静默变成 n/a，属于最隐蔽的一类退化。
    """
    keys = ("with_roles", "with_tool_start", "with_node_stats", "with_parse_results",
            "multi_agent")
    base_cov = baseline.get("coverage") or {}
    cur_cov = current.get("coverage") or {}
    base_runs = int(base_cov.get("runs", 0))
    cur_runs = int(cur_cov.get("runs", 0))
    if base_runs != cur_runs:
        # 运行数不同（例如 repeat 改了）时，绝对计数不可比，必须显式说明
        result.incomparable.append(
            f"采集覆盖度: 运行数不同（基线 {base_runs} → 当前 {cur_runs}），未做逐项比对")
        return
    for key in keys:
        base_value = int(base_cov.get(key, 0))
        cur_value = int(cur_cov.get(key, 0))
        if cur_value < base_value:
            result.regressions.append(f"采集覆盖 {key}: {base_value} → {cur_value} 个运行")
    base_incomplete = int(base_cov.get("incomplete", 0))
    cur_incomplete = int(cur_cov.get("incomplete", 0))
    if cur_incomplete > base_incomplete:
        result.regressions.append(
            f"未收尾运行: {base_incomplete} → {cur_incomplete} 个（上升即采集/中断退化）")


def _is_worse(name: str, delta: float) -> bool:
    """指标变大是变好还是变坏。

    低优指标（越低越好）显式列举，其余按"越高越好"处理——
    新增指标若方向特殊，必须加进这个集合，否则回归会被判成改进。
    """
    lower_is_better = ("空转率", "循环率", "回归引入率", "turn 预算耗尽率")
    if any(token in name for token in lower_is_better):
        return delta > 0
    return delta < 0


def _compare_failure_counts(current: dict, baseline: dict, result: RegressionResult) -> None:
    """失败分类计数：任一 F-xx 变多即视为回归信号。"""
    base_failures = baseline.get("failures") or {}
    cur_failures = current.get("failures") or {}
    for code in sorted(set(base_failures) | set(cur_failures)):
        base_count = int(base_failures.get(code, 0))
        cur_count = int(cur_failures.get(code, 0))
        if cur_count > base_count:
            result.regressions.append(f"失败分类 {code}: {base_count} → {cur_count}")
        elif cur_count < base_count:
            result.improvements.append(f"失败分类 {code}: {base_count} → {cur_count}")


def write_baseline(snapshot: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def load_baseline(path: str | Path) -> dict:
    """读取基线快照；文件不存在或格式非法时给出明确错误而不是静默空比对。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"基线文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"基线文件不是合法 JSON: {p} ({exc})") from exc
    if not isinstance(data, dict) or "version" not in data:
        raise ValueError(f"基线文件缺少 version 字段，无法确认比对口径: {p}")
    return data


def render_gate_section(results: list[IndicatorResult], regression: RegressionResult | None,
                        baseline_path: str | None) -> list[str]:
    """渲染门禁章节（追加到评测报告，让每次运行的判定都可审计）。"""
    lines = ["", "## 10. 指标门禁", "",
             "| 指标 | 数值 | 目标 | 状态 | 说明 |",
             "|------|------|------|------|------|"]
    for r in results:
        value = "n/a" if r.value is None else f"{r.value:.1%}"
        target = "—" if r.target is None else f"{r.direction} {r.target:.0%}"
        label = {"pass": "✅ 通过", "fail": "❌ 未通过",
                 "no_sample": "⚪ 无样本", "no_target": "—"}[r.status]
        lines.append(f"| {r.name} | {value} | {target} | {label} | {r.detail} |")

    lines += ["", "### 必需指标", "",
              "- " + "、".join(sorted(REQUIRED_METRICS))
              + "（无样本即判定门禁失败：数据缺失属采集退化）", ""]

    if baseline_path is None:
        lines += ["### 基线回归", "", "- 未提供基线，本次运行未做回归比对。", ""]
        return lines

    lines += ["### 基线回归", "", f"- 基线: `{baseline_path}`"]
    meta = (regression.baseline_meta if regression else {}) or {}
    if meta:
        lines.append(f"- 基线元信息: suite={meta.get('suite')} model={meta.get('model')} "
                     f"repeat={meta.get('repeat')}")
    if regression is None:
        lines.append("- 未执行比对。")
    else:
        lines.append(f"- 结论: {'✅ 无回归' if regression.passed else '❌ 检出回归'}")
        for item in regression.regressions:
            lines.append(f"  - ❌ 回归：{item}")
        for item in regression.improvements:
            lines.append(f"  - ✅ 改进：{item}")
        for item in regression.incomparable:
            lines.append(f"  - ⚪ 无法比较：{item}")
    lines.append("")
    return lines
