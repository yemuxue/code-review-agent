"""真实 replay 产物必须可复核，不依赖终端保留输出。"""

from __future__ import annotations

import json

from tests.eval.runner import _append_replay_report, _write_replay_artifact


def _result() -> dict:
    return {"total": 2, "scanned": 2, "true_positives": 1, "false_positives": 0,
            "false_negatives": 0, "true_negatives": 1, "precision": "100.0%",
            "recall": "100.0%", "f1_score": "1.00", "details": []}


def test_replay_artifact_and_report_are_persisted(tmp_path):
    report = tmp_path / "full.md"
    report.write_text("# eval\n", encoding="utf-8")
    artifact = _write_replay_artifact(_result(), report)
    _append_replay_report(report, _result(), artifact, "X:/target")
    assert json.loads(artifact.read_text(encoding="utf-8"))["scanned"] == 2
    assert "真实 API Replay" in report.read_text(encoding="utf-8")
