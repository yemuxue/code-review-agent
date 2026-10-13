"""漏洞注入基线样本的完整性与"不被流程改写"守卫。

背景（整改清单 P2#16）：tests/bug_injection_sample.py 曾在真实 Fixer 流程中
被就地修改（`users[user_id]` → `users.get(...)`、`eval()` → `ast.literal_eval`），
并被提交进仓库，于是"漏洞基线"变成了"被修好一半的混合样本"，
docstring 里的 bug 清单与实际内容不再一致。

这里用断言把基线钉住：
  - 它必须仍是"未修复"状态（含真实漏洞、不含 FIXED: 标记）
  - Bug 清单声明的数量与分类必须与文件内容一致
  - 期望发现清单（expected findings）必须与 docstring 清单同步

任何后续 E2E 流程再次就地改写该文件时，这些测试会立即失败。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SAMPLE = Path("tests/bug_injection_sample.py")
EXPECTED = Path("tests/fixtures/bug_injection_expected.json")

# Bug 清单行形如："    16. eval() 执行不可信输入                          (L126)"
_ENTRY_RE = re.compile(r"^\s*(\d+)\.\s+(.*?)\s*\(L(\d+)\)\s*$")
_SECTION_RE = re.compile(r"^\s*(BUG|SECURITY|PERF|STYLE)\s*\((\d+)\s*个\)")


def _parse_manifest(text: str) -> list[dict]:
    """解析 docstring 里的 bug 清单，返回 [{id, title, line, category}]。"""
    entries: list[dict] = []
    category = ""
    in_manifest = False
    for line in text.split("\n"):
        if "Bug 清单" in line:
            in_manifest = True
            continue
        if in_manifest and line.strip() == '"""':
            break
        section = _SECTION_RE.match(line)
        if section:
            category = section.group(1)
            continue
        entry = _ENTRY_RE.match(line)
        if entry and in_manifest:
            entries.append({
                "id": int(entry.group(1)),
                "title": entry.group(2),
                "line": int(entry.group(3)),
                "category": category,
            })
    return entries


def test_sample_is_still_an_unfixed_baseline():
    text = SAMPLE.read_text(encoding="utf-8")

    # ① 流程不得把"修复结果"留在基线里
    assert "FIXED:" not in text, (
        "bug_injection_sample.py 已被修复流程就地改写；基线样本必须保持未修复状态。"
        "请从 tests/fixtures 取期望结果，而不是修改被测样本。"
    )
    # ② 两个真实事故点的原始漏洞必须还在
    assert "return users[user_id]" in text, "Bug 1（KeyError）基线被改写"
    assert "return eval(expression)" in text, "Bug 16（eval 注入）基线被改写"


def test_manifest_matches_expected_findings_fixture():
    entries = _parse_manifest(SAMPLE.read_text(encoding="utf-8"))
    assert entries, "未能解析出 bug 清单，docstring 格式可能已变更"

    fixture = json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert fixture["bug_count"] == len(entries), (
        f"期望清单声明 {fixture['bug_count']} 个 bug，实际解析到 {len(entries)} 个")
    assert fixture["findings"] == entries, (
        "docstring 清单与 tests/fixtures/bug_injection_expected.json 不同步；"
        "修改清单时必须同步更新期望文件")


def test_manifest_categories_and_line_numbers_are_consistent():
    entries = _parse_manifest(SAMPLE.read_text(encoding="utf-8"))
    fixture = json.loads(EXPECTED.read_text(encoding="utf-8"))
    lines = SAMPLE.read_text(encoding="utf-8").split("\n")

    assert [e["id"] for e in entries] == list(range(1, len(entries) + 1))

    by_category: dict[str, int] = {}
    for entry in entries:
        by_category[entry["category"]] = by_category.get(entry["category"], 0) + 1
        # 清单声明的行号必须落在文件范围内（防止插入/删除行后清单失同步）
        assert 1 <= entry["line"] <= len(lines), (
            f"Bug {entry['id']} 声明行号 {entry['line']} 超出文件范围 {len(lines)}")

    assert by_category == fixture["by_category"]


def test_expected_fixture_declares_bug_categories_for_eval():
    """期望清单必须可用于评测：每条都要有可判定的类别与标题。"""
    fixture = json.loads(EXPECTED.read_text(encoding="utf-8"))

    assert set(fixture["by_category"]) <= {"BUG", "SECURITY", "PERF", "STYLE"}
    for finding in fixture["findings"]:
        assert finding["category"] in {"BUG", "SECURITY", "PERF", "STYLE"}
        assert finding["title"].strip()
        assert finding["line"] > 0
