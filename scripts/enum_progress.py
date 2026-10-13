"""把 enumerate 实验的已完成样本分成"口径分歧"与"TP 对照"两组统计。

目的：实验太慢（枚举变体在高思考度下单样本 >4 分钟），需要在跑完之前
判断"它到底有没有召回那些口径分歧样本"。分组统计让部分结果的含义立刻可见：
    - 口径分歧组 FN → TP 的数量 = 本次改动的收益
    - TP 对照组 TP → FN 的数量 = 本次改动的代价
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

LINE = re.compile(r"\[\s*\d+/\d+\]\s+(?P<file>\S+?):(?P<line>\d+)\s+->\s+"
                  r"(?P<cell>TP|FP|FN|TN|ERROR|SKIPPED)")


def _read(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        if text.count("\x00") <= len(text) // 100:
            return text
    return raw.decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    log_path = Path(argv[0] if argv else "reports/_ab_enum_mixed.log")

    disputed = {(str(k[0]), int(k[1])) for k in json.loads(
        Path("reports/_keys_disputed.json").read_text(encoding="utf-8"))["keys"]}
    keys = json.loads(Path("reports/_keys_mixed.json").read_text(encoding="utf-8"))["keys"]
    mixed = {(str(k[0]), int(k[1])) for k in keys}

    seen: dict[tuple[str, int], str] = {}
    for raw in _read(log_path).splitlines():
        match = LINE.search(raw.strip())
        if not match:
            continue
        key = (match.group("file"), int(match.group("line")))
        if key in mixed:
            seen[key] = match.group("cell")

    print(f"已完成 {len(seen)}/{len(mixed)} 条\n")
    for label, group in (("口径分歧（待召回）", disputed),
                         ("TP 对照（查回归）", mixed - disputed)):
        done = {k: v for k, v in seen.items() if k in group}
        if not done:
            print(f"{label}: 尚未有已完成样本")
            continue
        from collections import Counter
        counts = Counter(done.values())
        print(f"{label}: 已完成 {len(done)}/{len(group)} 条  {dict(counts)}")
        for key, cell in sorted(done.items()):
            print(f"    [{cell}] {key[0]}:{key[1]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
