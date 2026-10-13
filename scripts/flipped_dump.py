"""导出翻转负例（v1.1.0 中被翻成【已修复】的样本）及其所在函数全文。

用途：复核这些位置**是否仍含其它真实缺陷**。

背景：2026-10-13 把 27 条"原标注所述缺陷已不存在"的样本翻成负例。
但枚举变体在 `react_agent.py:222` 报出了另一个真实缺陷（过宽的
`except Exception`）——说明翻转时只排除了**原标注所指**的问题，
没有排除该位置**其它**真实缺陷。

把这类样本当"应当沉默"的负例，会让模型报出真问题时被计为误报，
从而**低估精确率**。本脚本导出复核所需的全部上下文。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.datasets import (  # noqa: E402
    DATASETS, _build_file_index, load_samples, resolve_sample_file,
)

TARGET = Path("X:/VScode/llm-agent-qa-system")
KEYS = "reports/_keys_flipped.json"


def _enclosing_block(lines: list[str], line_no: int) -> tuple[int, int]:
    """标注行所在的顶层 def/class 块（缩进归零处）。"""
    index = min(max(line_no - 1, 0), len(lines) - 1)
    start = index
    while start > 0 and not lines[start].startswith(("def ", "class ", "@")):
        start -= 1
    end = start + 1
    while end < len(lines):
        if lines[end].startswith(("def ", "class ")) and end > start:
            break
        end += 1
    return start + 1, end


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    out = Path(argv[0] if argv else "reports/_flipped_review.md")

    keys = json.loads(Path(KEYS).read_text(encoding="utf-8"))["keys"]
    spec = next(s for s in DATASETS if s.dataset_id == "llm-agent-qa-system-labeled")
    samples, _ = load_samples(spec)
    index = _build_file_index(TARGET, spec.search_root)
    by_key = {(str(s[0]), int(s[1])): (n, s) for n, s in enumerate(samples, start=1)}

    blocks = [
        "# 翻转负例复核清单",
        "",
        f"共 {len(keys)} 条。每条的原标注所述缺陷**已被确认不存在**；",
        "需要判断的是：该位置**是否仍含其它真实、当前、可验证的缺陷**。",
        "",
    ]
    for position, key in enumerate(keys, start=1):
        entry = by_key.get((str(key[0]), int(key[1])))
        if entry is None:
            continue
        sample_no, sample = entry
        file_name, line = str(sample[0]), int(sample[1])
        resolved, reason = resolve_sample_file(TARGET, file_name, index)
        blocks.append(f"\n---\n\n## FLIP-{position}（样本 #{sample_no}）"
                      f"  {file_name}:{line}  {sample[2]}/{sample[3]}\n")
        blocks.append(f"- 原标注（已判定不成立）: {sample[5]}")
        blocks.append(f"- 原标注英文: {sample[4]}")
        if resolved is None:
            blocks.append(f"- ⚠️ 无法解析文件: {reason}")
            continue
        lines = Path(resolved).read_text(encoding="utf-8", errors="replace").split("\n")
        lo, hi = _enclosing_block(lines, line)
        blocks.append(f"- 文件: `{resolved}`（共 {len(lines)} 行）")
        blocks.append(f"- 所在块: 第 {lo}-{hi} 行\n")
        blocks.append("```python")
        for i in range(lo - 1, min(hi, len(lines))):
            mark = ">>>" if i + 1 == line else "   "
            blocks.append(f"{mark}{i + 1:4d}| {lines[i]}")
        blocks.append("```")
        defs = [f"{i + 1}: {lines[i].strip()[:70]}"
                for i in range(len(lines))
                if lines[i].startswith(("def ", "class "))]
        if defs:
            blocks.append(f"\n同文件顶层定义（{len(defs)} 个）:")
            blocks.extend(f"  - {d}" for d in defs[:40])

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    print(f"[flipped-dump] {out}（{len(keys)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
