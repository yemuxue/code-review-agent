"""逐样本判分的离线回归测试（第 3 项：结果层进入可离线门禁的链路）。

锁定两件事：
    1. 混淆矩阵与 P/R/F1 的算法本身正确——这部分此前和真实 API 调用耦合，
       完全没有单元测试；
    2. **跳过与异常必须显式记账**：旧实现文件找不到就 `continue`，
       只让 `scanned < total` 偷偷暴露（历史事故 16/124）。
       `healthy=False` 时 precision/recall 只是部分样本的读数，不能当整体质量。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.scoring import (  # noqa: E402
    ScoreResult,
    build_context,
    score_samples,
)

# 固定样本：3 真 + 1 假（FP 陷阱）
SAMPLES = [
    ("a.py", 2, "BUG", "High", "en1", "cn1", True),
    ("a.py", 4, "BUG", "Medium", "en2", "cn2", True),
    ("b.py", 1, "STYLE", "Low", "en3", "cn3", True),
    ("a.py", 6, "BUG", "Low", "en4", "cn4", False),
]


@pytest.fixture()
def target(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("l1\nl2\nl3\nl4\nl5\nl6\nl7\n", encoding="utf-8")
    (src / "b.py").write_text("only line\n", encoding="utf-8")
    return tmp_path


def _judge_all_yes(*_args):
    return True


def _judge_all_no(*_args):
    return False


def _judge_from_map(mapping):
    def judge(file_name, line, _snippet):
        return mapping.get((file_name, line), False)
    return judge


# ═══ 混淆矩阵算法 ═══

def test_all_yes_gives_tp_for_bugs_and_fp_for_traps(target):
    result = score_samples(SAMPLES, "ds", target, _judge_all_yes)

    assert (result.true_positives, result.false_positives) == (3, 1)
    assert (result.false_negatives, result.true_negatives) == (0, 0)
    assert result.precision == 3 / 4
    assert result.recall == 1.0


def test_all_no_gives_fn_for_bugs_and_tn_for_traps(target):
    result = score_samples(SAMPLES, "ds", target, _judge_all_no)

    assert (result.true_positives, result.false_positives) == (0, 0)
    assert (result.false_negatives, result.true_negatives) == (3, 1)
    assert result.precision is None      # 没有预测为正 → 分母为 0
    assert result.recall == 0.0
    assert result.f1 is None


def test_mixed_verdicts_compute_expected_metrics(target):
    # 命中 #1、#3（TP=2），漏 #2（FN=1），误报陷阱 #4（FP=1）
    judge = _judge_from_map({("a.py", 2): True, ("b.py", 1): True, ("a.py", 6): True})

    result = score_samples(SAMPLES, "ds", target, judge)

    assert (result.true_positives, result.false_positives) == (2, 1)
    assert (result.false_negatives, result.true_negatives) == (1, 0)
    assert result.precision == pytest.approx(2 / 3)
    assert result.recall == pytest.approx(2 / 3)
    assert result.f1 == pytest.approx(2 / 3)


def test_f1_harmonic_mean_is_correct(target):
    # 只命中 3 个真问题中的 1 个，且误报 1 个陷阱
    judge = _judge_from_map({("a.py", 2): True, ("a.py", 6): True})

    result = score_samples(SAMPLES, "ds", target, judge)

    precision, recall = 1 / 2, 1 / 3
    expected_f1 = 2 * precision * recall / (precision + recall)
    assert result.f1 == pytest.approx(expected_f1)


def test_sample_cells_are_recorded_individually(target):
    judge = _judge_from_map({("a.py", 2): True, ("a.py", 6): True})

    result = score_samples(SAMPLES, "ds", target, judge)
    cells = [o.cell for o in result.outcomes]

    assert cells == ["TP", "FN", "FN", "FP"]
    assert [o.sample_id for o in result.outcomes] == [f"ds#{i}" for i in range(1, 5)]


# ═══ 跳过与异常必须记账（核心回归点） ═══

def test_missing_file_is_counted_as_skipped_not_silently_dropped(target):
    samples = SAMPLES + [("ghost.py", 1, "BUG", "High", "en", "cn", True)]

    result = score_samples(samples, "ds", target, _judge_all_yes)

    assert result.total == 5
    assert result.scored == 4
    assert result.skipped == 1
    assert not result.healthy, "存在跳过样本时数据集不健康，读数分母不完整"
    skipped = [o for o in result.outcomes if o.cell == "SKIPPED"]
    assert skipped and skipped[0].file == "ghost.py"


def test_judge_exception_is_counted_as_error_and_does_not_become_fn(target):
    """判分异常不能算成"模型没发现问题"，否则 API 故障会被当成漏报。"""
    calls = {"n": 0}

    def flaky(file_name, line, snippet):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("API HTTP 429")
        return False

    result = score_samples(SAMPLES, "ds", target, flaky)

    assert result.errored == 1
    assert result.false_negatives == 2      # 只有真正判"否"的两条算 FN
    assert not result.healthy
    errored = [o for o in result.outcomes if o.cell == "ERROR"]
    assert "API HTTP 429" in errored[0].reason


def test_healthy_requires_every_sample_scored(target):
    clean = score_samples(SAMPLES, "ds", target, _judge_all_no)

    assert clean.healthy
    assert clean.match_ratio == 1.0


def test_empty_dataset_is_not_healthy():
    assert not ScoreResult(dataset_id="empty", total=0).healthy


def test_retry_errored_recovers_samples_and_fixes_matrix(target):
    """批末重试成功必须**改写** ERROR 并修正混淆矩阵，而不是留成坏账。"""
    attempts = {"n": 0}

    def flaky(file_name, line, snippet):
        attempts["n"] += 1
        if attempts["n"] <= 2:            # 前两条首次失败
            raise RuntimeError("API HTTP 502")
        return True

    result = score_samples(SAMPLES, "ds", target, flaky, retry_errored=1)

    assert result.errored == 0
    assert result.scored == 4, "重试成功后分母应完整"
    assert result.healthy
    assert result.true_positives == 3   # 全判 YES：3 真 + 1 FP
    assert result.false_positives == 1


def test_circuit_breaker_stops_batch_when_gateway_is_down(target):
    """网关整体不可用时必须快速停下，而不是把整批跑到数小时。"""
    def always_fails(*_args):
        raise RuntimeError("API HTTP 502")

    result = score_samples(SAMPLES + SAMPLES, "ds", target, always_fails,
                           circuit_breaker=3)

    assert result.errored == 3
    assert result.skipped == 5, "熔断后剩余样本应记为 SKIPPED 并说明原因"
    assert not result.healthy
    breaker = [o for o in result.outcomes if o.cell == "SKIPPED"]
    assert "熔断" in breaker[0].reason


def test_circuit_breaker_resets_after_a_success(target):
    """中间有一次成功说明网关可用，计数必须归零，不能累积误熔断。"""
    calls = {"n": 0}

    def intermittent(*_args):
        calls["n"] += 1
        if calls["n"] % 2 == 1:           # 交替失败/成功
            raise RuntimeError("API HTTP 502")
        return True

    result = score_samples(SAMPLES, "ds", target, intermittent, circuit_breaker=2)

    assert result.scored == 2, "不应熔断（失败后有成功重置计数）"
    assert result.errored == 2
    assert all(o.cell != "SKIPPED" for o in result.outcomes)


def test_circuit_breaker_can_be_disabled(target):
    def always_fails(*_args):
        raise RuntimeError("boom")

    result = score_samples(SAMPLES, "ds", target, always_fails, circuit_breaker=0)

    assert result.errored == 4
    assert result.skipped == 0


def test_max_samples_limits_total(target):
    result = score_samples(SAMPLES, "ds", target, _judge_all_yes, max_samples=2)

    assert result.total == 2
    assert result.scored == 2
    assert result.healthy


# ═══ 序列化（replay 产物） ═══

def test_to_dict_exposes_skips_and_healthiness(target):
    samples = SAMPLES + [("ghost.py", 1, "BUG", "High", "en", "cn", True)]

    payload = score_samples(samples, "ds", target, _judge_all_yes).to_dict()

    assert payload["total"] == 5
    assert payload["scored"] == 4
    assert payload["skipped"] == 1
    assert payload["healthy"] is False
    assert payload["skipped_samples"][0]["file"] == "ghost.py"
    assert payload["precision"] and payload["recall"] and payload["f1_score"]


def test_to_dict_never_invents_metrics_for_empty_prediction(target):
    payload = score_samples(SAMPLES, "ds", target, _judge_all_no).to_dict()

    assert payload["precision"] == "n/a"
    assert payload["f1_score"] == "n/a"
    assert payload["recall"] == "0.0%"


# ═══ 上下文构造 ═══

def test_context_marks_the_annotation_line():
    path = Path(__file__).parent / ".." / "tests" / "eval" / "scoring.py"
    snippet = build_context(path, 30, context_lines=2)
    lines = snippet.split("\n")

    assert any(l.startswith(">>>") for l in lines)
    assert sum(1 for l in lines if l.startswith(">>>")) == 1
    assert "30" in snippet


def test_context_clamps_at_file_boundaries(target):
    snippet = build_context(target / "src" / "b.py", 1, context_lines=15)

    assert snippet.strip()
    assert ">>>" in snippet


# ═══ 真实数据集（离线，固定判分） ═══

def test_self_dataset_scores_offline_with_injected_judge():
    """整条结果层链路离线可跑：真实标注集 + 注入判分，不触网。"""
    from tests.eval.datasets import DATASETS, load_samples
    from tests.eval.scoring import score_samples as score

    spec = next(s for s in DATASETS if s.dataset_id == "code-review-agent-self")
    samples, _meta = load_samples(spec, Path(__file__).parent.parent)
    repo_root = Path(__file__).parent.parent

    # 判分器永远说"有问题"：真问题全 TP、FP 陷阱全 FP
    result = score(samples, spec.dataset_id, repo_root, lambda *_: True)

    assert result.total == 33
    assert result.healthy, "自评集应能 100% 解析（preflight 已保证）"
    assert result.true_positives == 30
    assert result.false_positives == 3
    assert result.recall == 1.0


def test_self_dataset_all_no_verdict_is_fully_offline():
    from tests.eval.datasets import DATASETS, load_samples
    from tests.eval.scoring import score_samples as score

    spec = next(s for s in DATASETS if s.dataset_id == "code-review-agent-self")
    samples, _meta = load_samples(spec, Path(__file__).parent.parent)

    result = score(samples, spec.dataset_id, Path(__file__).parent.parent, lambda *_: False)

    assert result.false_negatives == 30
    assert result.true_negatives == 3
    assert result.healthy
