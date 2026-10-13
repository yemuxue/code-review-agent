"""标注锚点指纹的回归测试。

锚点要解决的是 `preflight()` 的盲区：行号仍有效，但**内容已变**
（缺陷被修复，或重构导致行号漂移）。测试必须锁住这三类可机械判定的情形：
    OK        内容未变
    RELOCATED 内容仍在，行号漂移（应给出新行号）
    CHANGED   标注行内容被改写（需人工复核）
以及两类必须容错的情形：格式化差异不报警、注释改写不报警。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.anchors import (  # noqa: E402
    ANCHOR_VERSION,
    build,
    fingerprint,
    load_manifest,
    normalize_line,
    verify,
    write_manifest,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _samples(*pairs: tuple[str, int]) -> list[tuple]:
    return [(f, ln, "BUG", "High", "en", "cn", True) for f, ln in pairs]


# ═══ 归一化与指纹 ═══

def test_normalize_collapses_whitespace():
    assert normalize_line("    return   x  + 1   ") == "return x + 1"


def test_fingerprint_ignores_indent_and_trailing_space():
    """格式化差异不应触发复核 —— 否则重构一次就报满屏。"""
    a = fingerprint(["def f():", "    return 1", ""], 2)
    b = fingerprint(["def f():", "        return    1   ", ""], 2)

    assert a["line_hash"] == b["line_hash"]


def test_fingerprint_of_blank_line_anchors_to_nearest_significant():
    """标注行是空行/注释时，锚点应落到最近的显著行（常见于标注指向 docstring）。"""
    lines = ["def f():", "    # 注释", "    return 1"]

    from_comment = fingerprint(lines, 2)

    assert from_comment["anchor_line"] == 3        # 落到 `return 1`
    assert from_comment["line_hash"] == fingerprint(lines, 3)["line_hash"]


def test_fingerprint_passes_through_comment_only_lines():
    lines = ["x = 1"]
    assert fingerprint(lines, 1)["anchor_line"] == 1


def test_fingerprint_returns_none_out_of_range():
    assert fingerprint(["x = 1"], 99) is None
    assert fingerprint([], 1) is None


# ═══ 校验：OK / RELOCATED / CHANGED ═══

def test_unchanged_code_verifies_ok(tmp_path):
    _write(tmp_path, "a.py", "def f():\n    return 1\n")
    samples = _samples(("a.py", 2))

    manifest = build(samples, tmp_path)
    report = verify(samples, tmp_path, manifest)

    assert [c.status for c in report.checks] == ["OK"]
    assert not report.needs_review


def test_inserted_line_is_reported_as_relocated_with_new_line(tmp_path):
    """插入代码导致的行号漂移必须被识别，并给出新行号（可自动修正）。"""
    path = _write(tmp_path, "a.py", "def f():\n    return 1\n")
    samples = _samples(("a.py", 2))
    manifest = build(samples, tmp_path)

    path.write_text("import os\n\ndef f():\n    return 1\n", encoding="utf-8")
    report = verify(samples, tmp_path, manifest)

    assert report.checks[0].status == "RELOCATED"
    assert report.checks[0].new_line == 4
    assert not report.needs_review          # 漂移可自动修正，不阻塞


def test_rewritten_statement_is_reported_as_changed(tmp_path):
    """标注行被改写（缺陷可能已修复）→ CHANGED，必须人工复核。"""
    path = _write(tmp_path, "a.py", "def f():\n    return 1\n")
    samples = _samples(("a.py", 2))
    manifest = build(samples, tmp_path)

    path.write_text("def f():\n    return 2  # 已修复\n", encoding="utf-8")
    report = verify(samples, tmp_path, manifest)

    assert report.checks[0].status == "CHANGED"
    assert report.needs_review
    assert "复核" in report.checks[0].note


def test_missing_file_is_reported(tmp_path):
    _write(tmp_path, "a.py", "x = 1\n")
    samples = _samples(("a.py", 1))
    manifest = build(samples, tmp_path)

    (tmp_path / "src" / "a.py").unlink()
    report = verify(samples, tmp_path, manifest)

    assert report.checks[0].status == "MISSING"
    assert report.needs_review


def test_comment_only_edit_does_not_trigger_review(tmp_path):
    """仅注释改写不应报警：注释不参与指纹。"""
    path = _write(tmp_path, "a.py", "# old comment\nx = 1\n")
    samples = _samples(("a.py", 1))
    manifest = build(samples, tmp_path)

    path.write_text("# a completely new comment\nx = 1\n", encoding="utf-8")
    report = verify(samples, tmp_path, manifest)

    assert report.checks[0].status in {"OK", "RELOCATED"}
    assert not report.needs_review


# ═══ 清单与版本 ═══

def test_manifest_roundtrip(tmp_path):
    _write(tmp_path, "a.py", "x = 1\n")
    samples = _samples(("a.py", 1))

    path = write_manifest(build(samples, tmp_path), tmp_path / "m.json")
    manifest = load_manifest(path)

    assert manifest["anchor_version"] == ANCHOR_VERSION
    assert manifest["count"] == 1


def test_version_mismatch_raises(tmp_path):
    """归一化规则变化后旧指纹必须作废，不能静默误判为 CHANGED。"""
    _write(tmp_path, "a.py", "x = 1\n")
    samples = _samples(("a.py", 1))
    manifest = build(samples, tmp_path)
    manifest["anchor_version"] = ANCHOR_VERSION + 1

    with pytest.raises(ValueError, match="版本"):
        verify(samples, tmp_path, manifest)


def test_summary_line_counts_each_status(tmp_path):
    _write(tmp_path, "a.py", "x = 1\ny = 2\n")
    samples = _samples(("a.py", 1), ("a.py", 2))
    manifest = build(samples, tmp_path)

    summary = verify(samples, tmp_path, manifest).summary_line()

    assert "OK 2" in summary
