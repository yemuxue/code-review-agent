"""按严重度分层的召回率统计的回归测试。

为什么值得单独测：产品文档会直接引用"高危问题召回率"这类数字。
如果分层统计与自己漏掉样本（例如重定位样本因行号变了没被统计），
就会得到一组**看似合理但总数对不上**的数字，而"少 2 条"很容易被
当成四舍五入忽略过去。这里把"分层正例数之和必须等于 real_bug_count"
变成硬断言。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")


def _load_dataset():
    spec = importlib.util.spec_from_file_location("ds_sev_test", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stratify(artifact: dict) -> dict[str, list[int]]:
    """镜像 scripts/recall_by_severity.py 的分层逻辑。"""
    from scripts.recall_by_severity import _load_relocations

    module = _load_dataset()
    severity: dict[tuple[str, int], list[str]] = {}
    for sample in module.EVAL_SAMPLES:
        severity.setdefault((str(sample[0]), int(sample[1])), []).append(str(sample[3]))
    for (file_name, old_line), new_line in _load_relocations().items():
        target = severity.get((file_name, int(new_line)))
        if target is not None:
            severity.setdefault((file_name, int(old_line)), []).extend(target)

    consumed: dict[tuple[str, int], int] = {}
    stats: dict[str, list[int]] = {}
    for row in artifact.get("details") or []:
        key = (str(row[0]), int(row[1]))
        bucket = severity.get(key)
        if not bucket:
            continue
        index = consumed.get(key, 0)
        level = bucket[min(index, len(bucket) - 1)]
        consumed[key] = index + 1
        if not bool(row[2]):
            continue
        stats.setdefault(level, [0, 0])       # [TP, FN]
        stats[level][0 if str(row[4]) == "TP" else 1] += 1
    return stats


def test_severity_strata_sum_to_declared_real_bug_count():
    """分层后的正例总数必须等于数据集声明的正例数。

    这是本脚本唯一踩过的真坑：重定位样本在产物里是旧行号、在数据集里是新行号，
    不补映射就会静默漏统计，导致 82 ≠ 84。
    """
    artifact_path = Path("reports/eval_replay_v120_recomputed.json")
    if not artifact_path.is_file():
        pytest.skip("缺少重算产物，跳过（该产物不入库）")

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    stats = _stratify(artifact)
    total = sum(sum(pair) for pair in stats.values())

    assert total == _load_dataset().DATASET_META["real_bug_count"]


def test_high_severity_recall_is_fully_covered():
    """高危问题不能有漏报：这是产品文档里唯一可以承诺的召回结论。"""
    artifact_path = Path("reports/eval_replay_v120_recomputed.json")
    if not artifact_path.is_file():
        pytest.skip("缺少重算产物，跳过（该产物不入库）")

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    stats = _stratify(artifact)

    assert "High" in stats, "数据集里存在 High 严重度正例"
    assert stats["High"][1] == 0, f"高危问题出现漏报：{stats['High'][1]} 条"


def test_severity_recall_decreases_with_severity():
    """召回率应随严重度下降而下降（模型优先报重要问题）。

    这不是必然规律，但在本数据集上成立；若某次改动打破它，值得人看一眼
    而不是静默通过。
    """
    artifact_path = Path("reports/eval_replay_v120_recomputed.json")
    if not artifact_path.is_file():
        pytest.skip("缺少重算产物，跳过（该产物不入库）")

    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    stats = _stratify(artifact)

    def recall(level: str) -> float:
        tp, fn = stats[level]
        return tp / (tp + fn) if (tp + fn) else 0.0

    assert recall("High") >= recall("Medium") >= recall("Low")
