"""判定提示词与上下文窗口的可配置定义。

为什么需要它：召回率提升只能靠**受控对比实验**（改一处、跑同一数据集、比读数）。
此前 `JUDGE_PROMPT` 与 `CONTEXT_LINES=15` 硬编码在 `eval_llm_agent_qa.py` 里，
想试另一个判据就得改源码，无法 A/B，也无法复现"哪一版更好"。

口径分歧的真实来源（2026-10-07 漏报归因实测）：
    strict 版本把"死代码、未使用导入、恒真分支、重复实现、微优化"一律归入
    "purely cosmetic style nitpick" 从而答 NO；而标注集把这些**客观可验证的
    代码缺陷**计为真实问题。27/61 个漏报属于这一类。

两版都保留：
    strict  — 历史口径，作为基线，保证旧读数可复现
    lenient — 把上述客观缺陷纳入 YES，用于提升召回率

⚠️ 提醒：另有 29/61 个漏报源于**标注与当前代码不符**（缺陷已被修复）。
   那部分任何提示词都无法召回 —— 让模型对已修复的旧问题答 YES 是假提升。
   因此提高召回率必须同时报告"标注有效性"，见 scripts/fn_attribution_summary.py。
"""

from __future__ import annotations

# 送入判分模型的上下文行数：标注行前后各取多少行。
# 15 → 40 的依据：漏报归因中 B_CONTEXT_MISSING 类的证据（如调用方、
# 异常兜底实现体）落在标注行 20-40 行之外，模型看不到。
CONTEXT_LINES_BY_VARIANT: dict[str, int] = {
    "strict": 15,
    "lenient": 40,
    # enumerate 与 strict 用同一窗口：本变体只改提问形状，
    # 不动判据也不动窗口，保证与基线严格可比（一次只动一个变量）。
    "enumerate": 15,
}

_STRICT = """You are an expert code reviewer performing a detailed code review.

Determine if the code around the marked line (>>>) has a bug or issue.

Answer "YES" if ANY of these apply:
- Functional bug (crash, wrong behavior, data loss, race condition)
- Security vulnerability (injection, unsafe eval, secret exposure)
- Performance problem (O(n²) loops, repeated work, unbounded growth)
- Maintainability issue that will cause bugs later (shared mutable state, unstable IDs, missing validation)
- Incorrect error handling (bare except, swallowed exceptions, missing timeout)

Answer "NO" only if:
- The code is fully correct AND the issue is a purely cosmetic style nitpick
- The issue is a missing feature that was never promised

Reply with exactly "YES" or "NO" followed by a one-sentence reason."""

_LENIENT = """You are an expert code reviewer. Decide whether the marked line (>>>)
contains a defect that a reviewer should report.

Answer "YES" if ANY of these apply:
- Functional bug: crash, wrong result, data loss, race condition, off-by-one
- Security: injection, unsafe eval/pickle, secret exposure, path traversal, missing validation
- Robustness: no error handling, swallowed exception, missing timeout, no fallback
  for an encoding/format that will realistically occur, unvalidated input used downstream
- Resource: connection/session/file not released on the error path, leak, unbounded growth
- Performance: repeated work in a hot loop, O(n²) on growing data, unnecessary full copy
- Maintainability defect that is OBJECTIVELY verifiable in this file, such as:
    * dead code (a function/constant/import defined but never referenced anywhere)
    * a branch that can never be taken (e.g. both arms return the same value)
    * duplicated implementation that must be kept in sync by hand
    * a hardcoded constant that contradicts another value in the same file
    * an API that silently accepts an invalid input (empty string, None) where a
      sibling method in the same class explicitly guards against it

Answer "NO" only if BOTH hold:
- the code behaves correctly for all realistic inputs, AND
- the only possible complaint is subjective taste (naming, spacing, comment wording,
  documentation language, or a purely hypothetical future model upgrade)

Important:
- Do NOT answer NO merely because the issue is "not a crash" or "low severity".
  A low-severity but objectively verifiable defect is still a defect.
- If the marked line itself is an empty line or a docstring, inspect the enclosing
  function/class and judge that.
- Prefer YES when the visible code supports a concrete, nameable problem.

Reply with exactly "YES" or "NO" followed by a one-sentence reason."""

_ENUMERATE = """You are an expert code reviewer. Inspect the marked line (>>>) and
report every concrete defect it contains.

A defect is any of:
- Functional: crash, wrong result, data loss, race condition, off-by-one
- Security: injection, unsafe eval/pickle, secret exposure, path traversal
- Robustness: missing error handling, swallowed exception, missing timeout,
  unvalidated input used downstream, no fallback for a realistic encoding/format
- Resource: connection/session/file not released on an error path, leak
- Performance: repeated work in a loop, O(n²) on growing data, needless full copy
- Objectively verifiable maintainability: dead code (defined but never referenced
  anywhere), a branch that can never be taken, duplicated logic that must be kept
  in sync by hand, a hardcoded constant contradicting another value in the same file

Do NOT report: naming, spacing, comment wording, documentation language,
or anything that depends on a hypothetical future change.

Output format — follow it exactly:

If the marked code contains no defect, reply with one line:
NONE|<one sentence saying why the code is sound>

Otherwise reply with one line per defect, each on its own line:
DEFECT|<CATEGORY>|<one sentence naming the concrete problem and the code that shows it>

Rules:
- Judge the enclosing function, not just the physical line. If the marked line is
  blank or a docstring, judge the code it belongs to.
- Every DEFECT line must name something specific in the visible code.
- It is correct and expected to answer NONE when the code is sound.
- The reason after NONE is required: it is how a reviewer later distinguishes
  "model missed a real defect" from "model judged the code acceptable".
- Do not add any other prose."""

JUDGE_PROMPTS: dict[str, str] = {
    "strict": _STRICT,
    "lenient": _LENIENT,
    "enumerate": _ENUMERATE,
}

DEFAULT_VARIANT = "strict"


def judge_prompt(variant: str = DEFAULT_VARIANT) -> str:
    """取指定变体的判定提示词；未知变体回退到默认，避免静默用错口径。"""
    return JUDGE_PROMPTS.get(variant, JUDGE_PROMPTS[DEFAULT_VARIANT])


def parse_verdict(variant: str, response: str) -> bool:
    """把模型回复解析成"是否发现问题"。

    不同变体的输出格式不同，**判定规则必须跟着变体走**：
    - strict / lenient：以 YES 开头即视为发现问题
    - enumerate：输出 NONE 表示无问题，否则出现 DEFECT| 行即视为发现问题

    把解析与提示词放在一起，避免"换了提示词却忘了换解析"导致读数被
    静默算错（那类错误比模型判错更难发现）。
    """
    text = (response or "").strip()
    if not text:
        return False
    upper = text.upper()
    if variant == "enumerate":
        if upper.startswith("NONE"):
            return False
        return "DEFECT|" in upper
    return upper.startswith("YES")


def context_lines(variant: str = DEFAULT_VARIANT) -> int:
    """取指定变体的上下文行数。

    可用环境变量 JUDGE_CONTEXT_LINES 覆盖：召回率的受控对比需要把
    "提示词"与"上下文窗口"**分别**作为自变量，否则无法判断提升来自哪一项。
    """
    import os

    override = os.environ.get("JUDGE_CONTEXT_LINES", "").strip()
    if override:
        try:
            value = int(override)
            if value > 0:
                return value
        except ValueError:
            pass
    return CONTEXT_LINES_BY_VARIANT.get(variant,
                                        CONTEXT_LINES_BY_VARIANT[DEFAULT_VARIANT])
