"""校验审计上下文导出：每条块必须包含其目标行且标记唯一。"""
from __future__ import annotations

import re
from pathlib import Path

TEXT = Path("reports/_audit_context.md")


def main() -> int:
    text = TEXT.read_text(encoding="utf-8")
    blocks = re.split(r"\n## #", text)[1:]
    bad: list[tuple[int, int, list[int]]] = []
    for block in blocks:
        number = int(block.split()[0])
        # 标题形如  ## #85  `config/settings.py:65`  BUG/Low  正例
        match = re.search(r"`([^`]+):(\d+)`", block)
        if not match:
            bad.append((number, -1, []))
            continue
        want = int(match.group(2))
        marks = [int(x) for x in re.findall(r"^>>>\s+(\d+)\|", block, re.M)]
        if marks != [want]:
            bad.append((number, want, marks))
    print(f"  块数: {len(blocks)}")
    print(f"  标记异常: {bad if bad else '无'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
