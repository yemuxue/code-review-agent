"""按严重度分层的召回率统计的回归测试。

为什么值得单独测：产品文档会直接引用"高危问题召回率"这类数字。
如果分层统计漏掉样本（例如重定位样本因行号变了没被统计），就会得到一组
**看似合理但总数对不上**的数字，而"少 2 条"很容易被当成四舍五入忽略过去。
这里把"分层正例数之和必须等于 real_bug_count"变成硬断言。

## 为什么不读 `reports/eval_replay_v120_recomputed.json`

那是一个 **v1.2.0 时代**的重算产物（正例 81 条）。数据集升到 v1.3.x 之后，
它既过期又不入库，用它做断言只会测出"历史文件与当前数据集不一致"——
不是产品行为，而是测试自己被冻结在旧版本上。

现在改为：读**已入库的原始产物**（`eval_replay_sol_high_full_replay.json`），
用 `scripts.recompute_by_index.aligned_rows()` 按样本顺序对齐重算。
顺序对齐天然消除重定位问题（直接取当前数据集的行号与严重度），
因此不再需要独立的 relocation 映射表。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")
# 已入库的原始 replay 产物（未按新标签重算）
RAW_ARTIFACT = Path("reports/eval_replay_sol_high_full_replay.json")


def _load_dataset():
    spec = importlib.util.spec_from_file_location("ds_sev_test", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stats() -> dict[str, list[int]]:
    """按当前标签重算并按严重度分层。"""
    if not RAW_ARTIFACT.is_file():
        pytest.skip("缺少原始 replay 产物，跳过")
    from scripts.recompute_by_index import aligned_rows, severity_stats

    module = _load_dataset()
    artifact = json.loads(RAW_ARTIFACT.read_text(encoding="utf-8"))
    details = artifact.get("details") or []
    assert len(details) == len(module.EVAL_SAMPLES), (
        f"产物 {len(details)} 条与数据集 {len(module.EVAL_SAMPLES)} 条不一致，"
        "顺序对齐不成立")
    return severity_stats(aligned_rows(module.EVAL_SAMPLES, details))


def test_severity_strata_sum_to_declared_real_bug_count(stats):
    """分层后的正例总数必须等于数据集声明的正例数。

    这是本脚本唯一踩过的真坑：重定位样本在产物里是旧行号、在数据集里是新行号，
    不补映射就会静默漏统计（曾出现 82 ≠ 84）。
    """
    total = sum(sum(pair) for pair in stats.values())

    assert total == _load_dataset().DATASET_META["real_bug_count"]


def test_high_severity_recall_is_fully_covered(stats):
    """高危问题不能有漏报：这是产品文档里唯一可以承诺的召回结论。

    ⚠️ 但分母很小（当前 4 条），所以这条只证明"本数据集上没漏"，
    不能外推为"高危可靠"。置信区间见 docs/eval-baseline.md。
    """
    assert "High" in stats, "数据集里存在 High 严重度正例"
    assert stats["High"][1] == 0, f"高危问题出现漏报：{stats['High'][1]} 条"


def test_severity_recall_decreases_with_severity(stats):
    """召回率应随严重度下降而下降（模型优先报重要问题）。

    这不是必然规律，但在本数据集上成立；若某次改动打破它，值得人看一眼
    而不是静默通过。
    """

    def recall(level: str) -> float:
        tp, fn = stats[level]
        return tp / (tp + fn) if (tp + fn) else 0.0

    assert recall("High") >= recall("Medium") >= recall("Low")
