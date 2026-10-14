"""判定解析规则的回归测试。

为什么单独测解析：提示词与解析必须同源，否则"换了提示词却忘了换解析"
会把读数**静默**算错——那类比"模型判错"更难发现，因为它不报错，
只是数字看起来合理。`judge.parse_verdict` 是这两个变体读数的唯一入口。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.judge import (  # noqa: E402
    DEFAULT_VARIANT,
    JUDGE_PROMPTS,
    context_lines,
    judge_prompt,
    parse_verdict,
)


# ═══ 变体注册 ═══

def test_all_variants_expose_prompt_and_window():
    for name in JUDGE_PROMPTS:
        assert judge_prompt(name).strip()
        assert context_lines(name) > 0


def test_enumerate_shares_the_baseline_window():
    """enumerate 只改提问形状：窗口必须与 strict 相同，否则变量没隔离。"""
    assert context_lines("enumerate") == context_lines("strict")


def test_unknown_variant_falls_back_to_default_prompt():
    assert judge_prompt("does-not-exist") == judge_prompt(DEFAULT_VARIANT)


# ═══ 二值变体（strict / lenient）═══

def test_yes_prefix_counts_as_found():
    assert parse_verdict("strict", "YES — there is a bug") is True
    assert parse_verdict("strict", "yes, because ...") is True
    assert parse_verdict("lenient", "YES — ...") is True


def test_no_prefix_is_not_found():
    assert parse_verdict("strict", "NO — the code is correct") is False
    assert parse_verdict("strict", "No.") is False


def test_empty_response_is_not_found():
    assert parse_verdict("strict", "") is False
    assert parse_verdict("strict", "   \n ") is False


def test_binary_variants_do_not_treat_prose_as_found():
    """二值变体下，只有明确的 YES 才算发现——避免模糊文本抬高召回。"""
    assert parse_verdict("strict", "This might be a problem") is False


# ═══ 枚举变体 ═══

def test_enumerate_none_means_not_found():
    assert parse_verdict("enumerate", "NONE") is False
    assert parse_verdict("enumerate", "none") is False
    assert parse_verdict("enumerate", "None\n") is False


def test_enumerate_defect_line_means_found():
    assert parse_verdict(
        "enumerate",
        "DEFECT|ROBUSTNESS|self.llm.chat has no timeout") is True


def test_enumerate_multiple_defects_still_found():
    text = ("DEFECT|ROBUSTNESS|no timeout\n"
            "DEFECT|SECURITY|raw exception text leaks into the answer")
    assert parse_verdict("enumerate", text) is True


def test_enumerate_prose_without_defect_line_is_not_found():
    """格式外文本不算发现：宁可保守，也不要用模糊匹配抬高读数。"""
    assert parse_verdict("enumerate", "I could not find any defect") is False


def test_enumerate_defect_marker_is_case_insensitive():
    assert parse_verdict("enumerate", "defect|bug|something") is True


def test_enumerate_empty_is_not_found():
    assert parse_verdict("enumerate", "") is False


# ═══ 提示词与解析的一致性 ═══

def test_enumerate_prompt_documents_its_output_contract():
    """提示词必须写明 NONE / DEFECT| 两种输出，否则解析规则无从遵守。"""
    prompt = judge_prompt("enumerate")

    assert "NONE" in prompt
    assert "DEFECT|" in prompt


def test_enumerate_requires_a_reason_after_none():
    """NONE 必须带理由。

    实测教训：最初的格式只要求回 `NONE`，结果在口径分歧样本上拿到的
    就是光秃秃的 `NONE`，**无法区分**"模型漏掉真实缺陷"与"模型判断代码可接受"。
    另有二值变体只返回 YES/NO 时也发生过同类盲区。
    """
    prompt = judge_prompt("enumerate")

    assert "NONE|" in prompt
    assert "reason after NONE is required" in prompt


def test_none_with_reason_is_still_not_found():
    assert parse_verdict("enumerate", "NONE|the empty case is guarded") is False


def test_binary_prompts_document_yes_no_contract():
    for name in ("strict", "lenient"):
        prompt = judge_prompt(name)
        assert "YES" in prompt and "NO" in prompt
