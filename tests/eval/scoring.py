"""逐样本判分 / Per-sample scoring with explicit skip accounting.

解决的问题
    `run_per_sample_eval` 在文件找不到时 `continue`：既不计 skipped、也不进
    details，只让 `scanned < total` 偷偷暴露（历史上出现过 16/124 未被发现）。
    计分循环又和真实 API 调用耦合，导致精度/召回算法本身没有单元测试。

本模块把计分逻辑抽成纯函数：
    - `judge` 是一个 `(file, line, snippet) -> bool` 回调，可离线注入；
    - 找不到文件/读不出文件/判分抛异常都**显式记账**，绝不静默跳过；
    - 结果对象自带可判定的健康条件（`healthy`），供 CI 直接使用。

这样"精度/召回算得对不对"和"数据集能不能用"都能离线回归，
而真实 API 只负责提供一个 judge 实现。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# judge 签名：(file_name, line_no, code_snippet) -> bool
#   也允许返回 (found: bool, reason: str) —— 保留模型原话，用于归因。
#   此前只能返回布尔值，导致产物里 answer 恒为 "NO"，
#   "模型为什么答 NO"完全无法查证（漏报归因时才发现这个缺口）。
Judge = Callable[[str, int, str], object]

# 送入判分模型的上下文行数默认值（标注行前后各取多少行）。
# 可通过 score_samples(context_lines=...) 覆盖：召回率的受控对比实验需要
# 把"窗口大小"作为自变量，而不是硬编码在调用点。
CONTEXT_LINES = 15


def _split_judge_result(raw: object) -> tuple[bool, str]:
    """把 judge 的返回值统一成 (found, reason)。"""
    if isinstance(raw, tuple) and len(raw) >= 2:
        return bool(raw[0]), str(raw[1])
    return bool(raw), ""


@dataclass
class SampleOutcome:
    """单条样本的判分结果。"""

    sample_id: str
    file: str
    line: int
    is_bug: bool
    cell: str            # TP | FP | FN | TN | SKIPPED | ERROR
    found: bool | None = None
    reason: str = ""
    answer: str = ""

    @property
    def counted(self) -> bool:
        """是否计入混淆矩阵（SKIPPED/ERROR 不计入，但会被单独暴露）。"""
        return self.cell in {"TP", "FP", "FN", "TN"}


@dataclass
class ScoreResult:
    """一批样本的判分汇总。"""

    dataset_id: str
    total: int
    outcomes: list[SampleOutcome] = field(default_factory=list)
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    skipped: int = 0
    errored: int = 0

    @property
    def scored(self) -> int:
        return self.true_positives + self.false_positives + self.false_negatives + self.true_negatives

    @property
    def match_ratio(self) -> float:
        return self.scored / self.total if self.total else 0.0

    @property
    def precision(self) -> float | None:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else None

    @property
    def recall(self) -> float | None:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else None

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None or (precision + recall) == 0:
            return None
        return 2 * precision * recall / (precision + recall)

    @property
    def healthy(self) -> bool:
        """可用于决策的前提：全部样本都被判分，且没有任何判分异常。

        跳过或出错意味着分母不完整，此时 precision/recall 是"部分样本的读数"，
        不能当作整体质量——必须显式失败而不是给出一个看起来正常的数字。
        """
        return self.total > 0 and self.scored == self.total and self.errored == 0

    def summary_line(self) -> str:
        def _fmt(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.1%}"

        return (f"{self.dataset_id}: {self.scored}/{self.total} 已判分"
                + (f"，跳过 {self.skipped}" if self.skipped else "")
                + (f"，异常 {self.errored}" if self.errored else "")
                + f" | P={_fmt(self.precision)} R={_fmt(self.recall)} F1={_fmt(self.f1)}"
                + f" | TP={self.true_positives} FP={self.false_positives}"
                  f" FN={self.false_negatives} TN={self.true_negatives}")

    def to_dict(self) -> dict:
        """可序列化形式（供 replay 产物落盘，去掉逐样本明细以控制体积）。"""
        def _ratio(value: float | None) -> str:
            return "n/a" if value is None else f"{value:.1%}"

        return {
            "dataset_id": self.dataset_id,
            "total": self.total,
            "scored": self.scored,
            "skipped": self.skipped,
            "errored": self.errored,
            "healthy": self.healthy,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
            "precision": _ratio(self.precision),
            "recall": _ratio(self.recall),
            "f1_score": "n/a" if self.f1 is None else f"{self.f1:.2f}",
            "skipped_samples": [
                {"sample_id": o.sample_id, "file": o.file, "line": o.line, "reason": o.reason}
                for o in self.outcomes if o.cell == "SKIPPED"
            ],
            "errored_samples": [
                {"sample_id": o.sample_id, "file": o.file, "line": o.line, "reason": o.reason}
                for o in self.outcomes if o.cell == "ERROR"
            ],
        }


def build_context(file_path: Path, line: int, context_lines: int = CONTEXT_LINES) -> str:
    """构造标注行前后若干行的带标记上下文（>>> 指向标注行）。"""
    all_lines = file_path.read_text(encoding="utf-8", errors="replace").split("\n")
    start = max(0, line - 1 - context_lines)
    end = min(len(all_lines), line + context_lines)
    rendered = []
    for index in range(start, end):
        marker = ">>>" if index + 1 == line else "   "
        rendered.append(f"{marker} {index + 1:4d}| {all_lines[index]}")
    return "\n".join(rendered)


def _confusion_cell(is_bug: bool, found: bool) -> str:
    if is_bug:
        return "TP" if found else "FN"
    return "FP" if found else "TN"


def score_samples(samples: list[tuple], dataset_id: str, target_root: str | Path,
                  judge: Judge, *, resolve=None, max_samples: int | None = None,
                  on_progress=None, sample_delay_s: float = 0.0,
                  retry_errored: int = 0, circuit_breaker: int = 8,
                  context_lines: int = CONTEXT_LINES) -> ScoreResult:
    """对标注样本逐条判分。

    resolve：`(target_root, file_name) -> Path | None` 的文件解析器。
        默认使用 `tests.eval.datasets.resolve_sample_file`（唯一命中才接受），
        避免外部项目里同名文件被静默指到错误位置。
    judge：判分回调。离线测试注入固定答案；真实评测注入 LLM 判定。
    sample_delay_s：样本之间的最小间隔。真实 API 评测必须限速——
        实测网关在连续高频请求下会"第一条成功、后续大量 502"，
        而这类失败会被记成 ERROR（不计入混淆矩阵）后让整批读数不可用。
    retry_errored：批末对判分异常样本的重试轮数（网关抖动时非常有用）。
        写回时会修正混淆矩阵，而不是把一次抖动永久固定成 ERROR。
    circuit_breaker：连续多少个样本判分异常后停止整批（0 = 关闭）。
        网关整体不可用时，没有熔断会让整批跑到数小时才得出
        "读数无效"这个早已确定的结论。
    """
    if resolve is None:
        from tests.eval.datasets import _build_file_index, resolve_sample_file
        index = _build_file_index(Path(target_root), "src")

        def resolve(root, name):
            # 唯一命中才接受；同名歧义返回 None 并被记为 SKIPPED
            return resolve_sample_file(Path(root), name, index)[0]

    target = Path(target_root)
    selected = samples[:max_samples] if max_samples else samples
    result = ScoreResult(dataset_id=dataset_id, total=len(selected))

    # 熔断：连续 N 个样本都因判分异常失败时停止整批。
    # 没有它时，网关整体不可用会让 124 条样本 × 每条多次重试 ≈ 数小时，
    # 而结论早就可以确定（"网关不可用，读数无效"）。熔断让失败快速且明确。
    consecutive_errors = 0
    for position, sample in enumerate(selected):
        if circuit_breaker and consecutive_errors >= circuit_breaker:
            remaining = selected[position:]
            result.skipped += len(remaining)
            for offset, skipped_sample in enumerate(remaining):
                result.outcomes.append(SampleOutcome(
                    f"{dataset_id}#{position + offset + 1}",
                    str(skipped_sample[0]), int(skipped_sample[1]),
                    bool(skipped_sample[6]), "SKIPPED",
                    reason=f"熔断：连续 {consecutive_errors} 个样本判分异常，"
                           "已停止整批（网关很可能不可用）"))
            break
        if sample_delay_s and position:
            time.sleep(sample_delay_s)
        file_name, line, _category, _severity, _en, _cn, is_bug = sample
        sample_id = f"{dataset_id}#{position + 1}"
        resolved = resolve(target, file_name)

        if resolved is None:
            # 显式记账：静默跳过正是历史事故的成因
            result.skipped += 1
            result.outcomes.append(SampleOutcome(
                sample_id, str(file_name), int(line), bool(is_bug), "SKIPPED",
                reason="标注文件无法安全解析（不存在或同名歧义）"))
            continue

        try:
            snippet = build_context(Path(resolved), int(line), context_lines)
        except OSError as exc:
            result.skipped += 1
            result.outcomes.append(SampleOutcome(
                sample_id, str(file_name), int(line), bool(is_bug), "SKIPPED",
                reason=f"上下文不可读: {exc}"))
            continue

        try:
            found, model_reason = _split_judge_result(
                judge(str(file_name), int(line), snippet))
            answer = model_reason or ("YES" if found else "NO")
        except Exception as exc:  # 判分失败必须可归因，不能算成"未发现问题"
            result.errored += 1
            consecutive_errors += 1
            # 保留完整原因（含上游错误体）：只记异常类型会让 401/429/超时
            # 全部退化成同一个不可诊断的 ERROR。
            reason = f"{type(exc).__name__}: {exc}"
            result.outcomes.append(SampleOutcome(
                sample_id, str(file_name), int(line), bool(is_bug), "ERROR",
                reason=reason, answer=reason))
            continue

        consecutive_errors = 0      # 一旦有样本判分成功，视为网关恢复
        cell = _confusion_cell(bool(is_bug), found)
        if cell == "TP":
            result.true_positives += 1
        elif cell == "FP":
            result.false_positives += 1
        elif cell == "FN":
            result.false_negatives += 1
        else:
            result.true_negatives += 1
        result.outcomes.append(SampleOutcome(
            sample_id, str(file_name), int(line), bool(is_bug), cell,
            found=found, answer=answer))

        if on_progress:
            on_progress(position + 1, len(selected), file_name, line, cell, answer)

    if retry_errored and result.errored:
        _retry_errored_samples(result, selected, target, judge, resolve,
                               retry_errored, sample_delay_s, on_progress,
                               context_lines)

    return result


def _retry_errored_samples(result: "ScoreResult", selected: list[tuple], target: Path,
                           judge: Judge, resolve, rounds: int, sample_delay_s: float,
                           on_progress, context_lines: int = CONTEXT_LINES) -> None:
    """对被判分异常（通常是网关抖动）的样本重试若干轮。

    为什么需要它：真实网关的抖动表现为"成片 502，但过一会儿又能用"。
    只靠单次调用内的重试不够——那会把一次抖动直接变成不可用读数。
    重试成功的样本会**改写**原来的 ERROR 记录并修正混淆矩阵，
    避免"分母不完整"掩盖本可获得的读数。

    只重试 ERROR，不重试 SKIPPED：文件不存在是数据问题，重试无意义。
    """
    for round_index in range(rounds):
        pending = [o for o in result.outcomes if o.cell == "ERROR"]
        if not pending:
            return
        for outcome in pending:
            if sample_delay_s:
                time.sleep(sample_delay_s)
            sample = _find_sample(selected, outcome)
            if sample is None:
                continue
            file_name, line, _cat, _sev, _en, _cn, is_bug = sample
            resolved = resolve(target, file_name)
            if resolved is None:
                continue
            try:
                snippet = build_context(Path(resolved), int(line), context_lines)
                found, model_reason = _split_judge_result(
                    judge(str(file_name), int(line), snippet))
            except Exception as exc:
                outcome.reason = f"{type(exc).__name__}: {exc}"
                continue
            cell = _confusion_cell(bool(is_bug), found)
            _apply_cell(result, outcome, cell, found, model_reason)
            if on_progress:
                on_progress(0, len(selected), file_name, line, cell,
                            f"retry{round_index + 1}:{'YES' if found else 'NO'}")


def _find_sample(selected: list[tuple], outcome: "SampleOutcome") -> tuple | None:
    """按 (file, line) 找回原样本（样本 ID 里的序号不可靠：可能被 max_samples 截断）。"""
    for sample in selected:
        if str(sample[0]) == outcome.file and int(sample[1]) == outcome.line:
            return sample
    return None


def _apply_cell(result: "ScoreResult", outcome: "SampleOutcome", cell: str,
                found: bool, model_reason: str = "") -> None:
    """把重试结果写回：先撤销旧的 ERROR 计数，再计入新的混淆单元。"""
    result.errored = max(0, result.errored - 1)
    outcome.cell = cell
    outcome.found = found
    outcome.answer = model_reason or ("YES" if found else "NO")
    outcome.reason = ""
    if cell == "TP":
        result.true_positives += 1
    elif cell == "FP":
        result.false_positives += 1
    elif cell == "FN":
        result.false_negatives += 1
    else:
        result.true_negatives += 1
