"""把 3 条已核验的翻转负例改回正例（v1.2.0）。

背景：v1.1.0 把 27 条"原标注所述缺陷已不存在"的样本翻成负例。但复核发现其中
**3 条位置仍含其它真实、当前、可验证的缺陷** —— 把它们当"应当沉默"的负例，
会让模型报出真问题时被计为误报，**低估精确率**。

3 条缺陷均由读源码独立核验（非因"模型报了它"而回填）：
    1. react_agent.py:222  超限强制总结的响应未经 _extract_final_answer 解析，
                           原始 <thinking>/<final_answer> 标签直接返回给用户
    2. rag/retriever.py:78 retrieve_and_format 中 source 为死存储（98 行赋值后
                           从未读取），且该方法全仓无引用（死代码）
    3. config/settings.py:65 current_api_key / current_base_url 定义后全仓无引用，
                           与 create_llm_adapter 的 provider 分发重复

⚠️ 诚实说明：**这会让召回率下降**（正例分母 81→84，而这 3 条模型都答了 NO）。
   这正是本脚本的目的 —— 修正标签不应以"让读数变好"为目标。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DATASET = Path("tests/eval_llm_agent_qa.py")

# (file, line, category, severity, desc_en, desc_cn) —— 全部为独立核验后的新描述
REPLACEMENTS = [
    (
        "react_agent.py", 222, "BUG", "Medium",
        "Max-iterations forced summary assigns the raw LLM response to final_answer "
        "without _extract_final_answer, exposing <thinking>/<final_answer> markup to the user",
        "超限强制总结的响应未经_extract_final_answer解析，原始XML标签直接作为最终答案返回给用户",
    ),
    (
        "rag/retriever.py", 78, "BUG", "Low",
        "retrieve_and_format assigns `source` at line 98 but never reads it (dead store) "
        "and the method itself is never referenced anywhere — dead code",
        "retrieve_and_format第98行赋值的source从未被读取（死存储），且该方法全仓无引用（死代码）",
    ),
    (
        "config/settings.py", 65, "BUG", "Low",
        "current_api_key/current_base_url are defined but referenced nowhere in the "
        "repository — dead duplicates of the provider dispatch in create_llm_adapter",
        "current_api_key/current_base_url定义后全仓无引用——与create_llm_adapter的provider分发重复的死代码",
    ),
]


def _tuple_pattern(file_name: str, line: int) -> re.Pattern:
    return re.compile(
        r'\(\s*"' + re.escape(file_name) + r'",\s*' + str(line) + r',.*?\),\n',
        re.DOTALL)


def _render(file_name: str, line: int, category: str, severity: str,
            desc_en: str, desc_cn: str) -> str:
    return (f'    ("{file_name}", {line}, "{category}", "{severity}",\n'
            f'     "{desc_en}",\n'
            f'     "{desc_cn}", True),\n')


def main() -> int:
    text = DATASET.read_text(encoding="utf-8")
    applied = 0
    for file_name, line, category, severity, desc_en, desc_cn in REPLACEMENTS:
        pattern = _tuple_pattern(file_name, line)
        matches = pattern.findall(text)
        if len(matches) != 1:
            print(f"  ✗ {file_name}:{line} 匹配 {len(matches)} 处", file=sys.stderr)
            return 1
        # 必须确认当前状态是负例：把正例"改回正例"说明处置对象有误
        current = matches[0]
        if not current.rstrip().endswith("False),"):
            print(f"  ✗ {file_name}:{line} 当前不是负例，跳过", file=sys.stderr)
            return 1
        replacement = _render(file_name, line, category, severity, desc_en, desc_cn)
        text = pattern.sub(lambda _m: replacement, text, count=1)
        applied += 1

    DATASET.write_text(text, encoding="utf-8", newline="\n")
    print(f"[flip-back] 已把 {applied}/{len(REPLACEMENTS)} 条改回正例")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
