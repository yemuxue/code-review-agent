"""导出 STALE 标注及其所在函数全文，供逐条处置（重定位 / 翻负例）。

为什么需要"所在函数全文"：判断一条标注能否 RELOCATE，必须看整个文件里
是否还有同类的真实缺陷；只有 ±3 行片段无法判断，容易把"别处仍有缺陷"
误判成"已修复"从而错误地翻成负例。
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
CLASSIFICATION = "reports/_fn_attribution_classification.json"


def _enclosing_block(lines: list[str], line_no: int) -> tuple[int, int]:
    """找出标注行所在的顶层 def/class 块（缩进归零处）。"""
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
    out = Path(argv[0] if argv else "reports/_stale_samples.md")

    classified = json.loads(Path(CLASSIFICATION).read_text(encoding="utf-8"))
    stale_ids = [int(i["id"]) for i in classified
                 if i["category"] == "A_ANNOTATION_WEAK"]
    notes = {int(i["id"]): i.get("note", "") for i in classified}

    spec = next(s for s in DATASETS if s.dataset_id == "llm-agent-qa-system-labeled")
    samples, _ = load_samples(spec)
    index = _build_file_index(TARGET, spec.search_root)

    # 归因是按"产物里 FN 的顺序"编号的；这里重建同一顺序
    replay = json.loads(
        Path("reports/eval_replay_sol_high_full_replay.json").read_text(encoding="utf-8"))
    fn_keys = [(str(r[0]), int(r[1])) for r in replay["details"] if r[4] == "FN"]
    key_of_id = {i: fn_keys[i - 1] for i in range(1, len(fn_keys) + 1) if i <= len(fn_keys)}
    sample_by_key = {(str(s[0]), int(s[1])): (n, s) for n, s in enumerate(samples, start=1)}

    blocks: list[str] = [
        "# STALE 标注逐条处置清单",
        "",
        f"共 {len(stale_ids)} 条。每条需判定：RELOCATE / RELABEL_NEGATIVE / REPLACE。",
        "",
    ]
    for stale_id in stale_ids:
        key = key_of_id.get(stale_id)
        if key is None:
            continue
        entry = sample_by_key.get(key)
        if entry is None:
            continue
        position, sample = entry
        file_name, line, category, severity, desc_en, desc_cn, is_bug = sample
        resolved, reason = resolve_sample_file(TARGET, str(file_name), index)
        blocks.append(f"\n---\n\n## STALE-{stale_id}（样本 #{position}）"
                      f"  {file_name}:{line}  {category}/{severity}\n")
        blocks.append(f"- 标注中文: {desc_cn}")
        blocks.append(f"- 标注英文: {desc_en}")
        blocks.append(f"- 归因结论(待复核): {notes.get(stale_id, '')}")
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
        # 列出同文件里所有 def/class，便于判断"别处是否仍有同类缺陷"
        defs = [f"{i + 1}: {lines[i].strip()[:70]}"
                for i in range(len(lines))
                if lines[i].startswith(("def ", "class "))]
        if defs:
            blocks.append(f"\n同文件顶层定义（{len(defs)} 个）:")
            blocks.extend(f"  - {d}" for d in defs[:40])

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    print(f"[stale-dump] {out}（{len(stale_ids)} 条 STALE）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
