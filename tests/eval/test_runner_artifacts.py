"""真实 replay 产物必须可复核，不依赖终端保留输出。"""

from __future__ import annotations

import json

from tests.eval.runner import _append_replay_report, _write_replay_artifact


def _result(**extra) -> dict:
    base = {"total": 2, "scanned": 2, "true_positives": 1, "false_positives": 0,
            "false_negatives": 0, "true_negatives": 1, "precision": "100.0%",
            "recall": "100.0%", "f1_score": "1.00", "details": []}
    base.update(extra)
    return base


def test_replay_artifact_and_report_are_persisted(tmp_path):
    report = tmp_path / "full.md"
    report.write_text("# eval\n", encoding="utf-8")
    artifact = _write_replay_artifact(_result(), report)
    _append_replay_report(report, _result(), artifact, "X:/target")
    assert json.loads(artifact.read_text(encoding="utf-8"))["scanned"] == 2
    assert "Replay 结果" in report.read_text(encoding="utf-8")


def test_report_is_created_when_it_does_not_exist_yet(tmp_path):
    """replay-only 运行没有 offline 报告可追加，此时必须自建文件。

    旧实现直接 open("a")，父目录不存在就失败；而 `--report` 在 replay-only
    路径上根本不会被赋值，导致产物落到默认文件名（`--report` 被静默忽略）。
    """
    report = tmp_path / "nested" / "replay.md"

    _append_replay_report(report, _result(), tmp_path / "a.json", "X:/target")

    body = report.read_text(encoding="utf-8")
    assert "运行元数据" in body
    assert "Replay 结果" in body


def test_report_title_reflects_real_vs_stub_judging(tmp_path):
    """桩判分器的产物不能被标成"真实评测报告"，否则会被误读成模型质量。"""
    real = tmp_path / "real.md"
    stub = tmp_path / "stub.md"
    stub_meta = {"quality_claim": "管道验证（固定判分器），不代表模型质量"}
    real_meta = {"quality_claim": "真实模型判分"}

    _append_replay_report(real, _result(meta=real_meta), tmp_path / "a.json", "X:/t")
    _append_replay_report(stub, _result(meta=stub_meta), tmp_path / "b.json", "X:/t")

    real_body = real.read_text(encoding="utf-8")
    stub_body = stub.read_text(encoding="utf-8")
    assert "真实评测报告" in real_body
    assert "不代表模型质量" not in real_body.split("## 1")[0].replace("质量声明", "")
    assert "评测管道验证报告" in stub_body
    assert "不代表模型质量" in stub_body


def test_report_records_metadata_for_cross_version_comparability(tmp_path):
    report = tmp_path / "meta.md"
    meta = {"dataset_id": "ds", "dataset_version": "1.2.3", "model": "m",
            "healthy": True, "skipped": 0, "errored": 0,
            "quality_claim": "真实模型判分"}

    _append_replay_report(report, _result(meta=meta), tmp_path / "a.json", "X:/t")

    body = report.read_text(encoding="utf-8")
    assert "1.2.3" in body and "| 模型 | m |" in body
