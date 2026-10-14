"""FINDING 行解析回归测试。

覆盖：描述文本包含字面量 `|` 时不得错位；API 与 LangGraph 编排必须
共用同一份解析规则（历史上两份实现，其中一份按固定下标取值会损坏数据）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.multi_agent.finding_parser import (  # noqa: E402
    parse_finding_line,
    parse_finding_lines,
)


def test_parses_well_formed_line_with_language_prefixes():
    parsed = parse_finding_line(
        "FINDING|src/a.py|42|SECURITY|High|EN:SQL injection|CN:SQL 注入|use param binding"
    )

    assert parsed is not None
    assert parsed["file_path"] == "src/a.py"
    assert parsed["line"] == 42
    assert parsed["category"] == "SECURITY"
    assert parsed["severity"] == "High"
    assert parsed["description_en"] == "SQL injection"
    assert parsed["description_cn"] == "SQL 注入"
    assert parsed["suggestion"] == "use param binding"


def test_pipes_inside_english_description_keep_cn_and_fix_intact():
    """英文描述含 `|`（常见：联合类型、代码片段）时，CN 描述与 fix 不变。"""
    line = "FINDING|b.py|7|BUG|Medium|EN:value is str | None|CN:变量可能为空|加空值检查"
    parsed = parse_finding_line(line)

    assert parsed is not None
    assert parsed["description_en"] == "value is str | None"
    assert parsed["description_cn"] == "变量可能为空"
    assert parsed["suggestion"] == "加空值检查"


def test_old_fixed_index_parser_would_have_misaligned_tail():
    """反例：固定下标取值会把 fix 片段当成 CN 描述（旧实现的问题）。"""
    line = "FINDING|b.py|7|BUG|Medium|EN:value is str | None|CN:变量可能为空|加空值检查"
    parts = line.split("|")

    # 旧实现：parts[6]=CN 描述、parts[7]=fix。英文描述含 `|` 后下标整体
    # 右移，CN 描述取到 "None"，真正的 fix 建议被完全丢弃。
    assert parts[6].strip() == "None"
    assert parts[7].strip() == "CN:变量可能为空"

    parsed = parse_finding_line(line)
    assert parsed is not None
    assert parsed["description_cn"] == "变量可能为空"
    assert parsed["suggestion"] == "加空值检查"


def test_pipes_in_fix_suggestion_with_clean_english_description():
    """只有 fix 建议含 `|` 时，三个字段仍全部正确。"""
    line = "FINDING|a.py|9|BUG|High|EN:missing null check|CN:缺少空值检查|用 a | b 兜底"
    parsed = parse_finding_line(line)

    assert parsed is not None
    assert parsed["description_en"] == "missing null check"
    assert parsed["description_cn"] == "缺少空值检查"
    # 已知边界：本格式未转义自由文本，锚点后仍有 `|` 时 fix 只能保留最后一段
    assert parsed["suggestion"] == "b 兜底"


def test_right_side_fallback_still_works_without_cn_marker():
    """没有 `CN:` 标记（模型未遵循提示词）时退回右侧切分。"""
    parsed = parse_finding_line("FINDING|a.py|3|BUG|High|EN:x | y|中文说明|修复建议")
    assert parsed is not None
    assert parsed["description_cn"] == "中文说明"
    assert parsed["suggestion"] == "修复建议"


def test_non_finding_lines_are_ignored():
    assert parse_finding_line("# Code Analysis Report") is None
    assert parse_finding_line("FINDING|only|two") is None
    assert parse_finding_line("VERDICT|1|CONFIRMED|evidence") is None


def test_non_numeric_line_falls_back_to_zero():
    parsed = parse_finding_line("FINDING|a.py|N/A|STYLE|Low|EN:x|CN:y|z")
    assert parsed is not None
    assert parsed["line"] == 0
    assert parsed["line_text"] == "N/A"


def test_two_field_tail_leaves_suggestion_empty():
    parsed = parse_finding_line("FINDING|a.py|1|BUG|High|EN:only en|CN:只有中文")
    assert parsed is not None
    assert parsed["description_en"] == "only en"
    assert parsed["description_cn"] == "只有中文"
    assert parsed["suggestion"] == ""


def test_single_field_tail_only_sets_english_description():
    parsed = parse_finding_line("FINDING|a.py|1|BUG|High|EN:only one")
    assert parsed is not None
    assert parsed["description_en"] == "only one"
    assert parsed["description_cn"] == ""
    assert parsed["suggestion"] == ""


def test_ids_are_sequential_across_multiple_lines():
    findings = parse_finding_lines(
        "intro\n"
        "FINDING|a.py|1|BUG|High|EN:one|CN:一|fix1\n"
        "noise\n"
        "FINDING|b.py|2|STYLE|Low|EN:two|CN:二|fix2\n"
    )

    assert [f["id"] for f in findings] == [1, 2]
    assert [f["file_path"] for f in findings] == ["a.py", "b.py"]


def test_orchestrator_and_api_share_the_same_parser():
    """两个入口必须调用同一实现，禁止再出现第二份解析。"""
    orchestrator_src = Path("src/multi_agent/langgraph_orchestrator.py").read_text("utf-8")
    server_src = Path("src/api/server.py").read_text("utf-8")

    assert "parse_finding_lines" in orchestrator_src
    assert "parse_finding_lines" in server_src
    # 旧的固定下标解析特征不得残留
    assert 'parts[7].strip()' not in orchestrator_src
