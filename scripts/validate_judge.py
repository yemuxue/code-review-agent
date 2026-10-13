"""判据组件的离线自检（不调用任何 API）。

为什么需要：判据变体由"提示词 + 输出格式 + 解析规则 + 上下文窗口"四部分组成，
一旦其中一项与其它项不一致，读数就会被**静默**算错——
例如换了提示词却沿用旧的解析规则，模型明明报了缺陷却被记成"未发现"。

本脚本把这类一致性做成可执行断言，并打印每个变体的关键参数，
便于在跑真实评测前先确认配置无误。

用法：python scripts/validate_judge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.judge import (  # noqa: E402
    CONTEXT_LINES_BY_VARIANT,
    JUDGE_PROMPTS,
    context_lines,
    judge_prompt,
    parse_verdict,
)

# 每个变体必须满足的输出契约：
#   (提示词里必须出现的标记, 提示词里必须出现的反向标记)
REQUIRED_MARKERS = {
    "strict": (["YES", "NO"], []),
    "lenient": (["YES", "NO"], []),
    "enumerate": (["NONE|", "DEFECT|"], []),
}


def _check_prompt_contract(name: str) -> list[str]:
    prompt = judge_prompt(name)
    required, forbidden = REQUIRED_MARKERS.get(name, ([], []))
    problems = []
    for marker in required:
        if marker not in prompt:
            problems.append(f"提示词缺少输出契约标记 {marker!r}")
    for marker in forbidden:
        if marker in prompt:
            problems.append(f"提示词不应包含 {marker!r}")
    return problems


def _check_parse_roundtrip(name: str) -> list[str]:
    """用"模型应当给出的格式"测解析器，确认不会把发现当成未发现。"""
    problems = []
    if name == "enumerate":
        found_cases = ["DEFECT|BUG|concrete problem", "DEFECT|PERF|a\nDEFECT|BUG|b"]
        not_found_cases = ["NONE|code is sound", "NONE"]
    else:
        found_cases = ["YES — a problem", "yes"]
        not_found_cases = ["NO — fine", "no"]

    for text in found_cases:
        if parse_verdict(name, text) is not True:
            problems.append(f"应当判定为『发现』，却判为未发现: {text[:40]!r}")
    for text in not_found_cases:
        if parse_verdict(name, text) is not False:
            problems.append(f"应当判定为『未发现』，却判为发现: {text[:40]!r}")
    return problems


def _check_window_consistency() -> list[str]:
    """变体必须有显式窗口声明，否则不同变体会静默共用默认值。"""
    problems = []
    for name in JUDGE_PROMPTS:
        if name not in CONTEXT_LINES_BY_VARIANT:
            problems.append(f"变体 {name} 没有声明上下文窗口，会落到默认值")
    return problems


def main() -> int:
    print("=" * 70)
    print("判据组件自检 / Judge component self-check (offline)")
    print("=" * 70)

    failures = 0
    for name in sorted(JUDGE_PROMPTS):
        problems = (_check_prompt_contract(name)
                    + _check_parse_roundtrip(name))
        status = "OK" if not problems else "FAIL"
        print(f"\n[{status}] {name}")
        print(f"   提示词 {len(judge_prompt(name))} 字符  窗口 ±{context_lines(name)} 行")
        for problem in problems:
            print(f"   ✗ {problem}")
        failures += len(problems)

    window_problems = _check_window_consistency()
    print(f"\n[{'OK' if not window_problems else 'FAIL'}] 窗口声明一致性")
    for problem in window_problems:
        print(f"   ✗ {problem}")
    failures += len(window_problems)

    # enumerate 与 strict 必须同窗口，否则"只改提问形状"的前提不成立
    if context_lines("enumerate") != context_lines("strict"):
        print("\n[FAIL] enumerate 与 strict 的窗口不同 —— 变量没有隔离")
        failures += 1

    print(f"\n{'=' * 70}")
    print(f"结论: {'全部通过' if not failures else f'{failures} 处问题'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
