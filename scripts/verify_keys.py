"""核对子集 keys 与产物计数是否自洽（离线校验工具）。

这类校验的意义：子集实验的基线是"从全量产物里挑出同位置样本"，
如果 keys 与产物的 (file, line) 对不上，基线会静默少算若干条，
对比结果就没有意义。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

REPLAY = "reports/eval_replay_sol_high_full_replay.json"


def main() -> int:
    replay = json.loads(Path(REPLAY).read_text(encoding="utf-8"))
    details = replay["details"]

    cells = Counter(str(r[4]) for r in details)
    print(f"全量产物: {len(details)} 条，单元分布 {dict(cells)}")

    for cell in ("TP", "FP", "FN", "TN"):
        keys = [(str(r[0]), int(r[1])) for r in details if r[4] == cell]
        unique = len(set(keys))
        dup = [k for k, v in Counter(keys).items() if v > 1]
        note = f"，重复键 {dup}" if dup else ""
        print(f"  {cell}: 行数 {len(keys)}，去重 {unique}{note}")

    for name in ("disputed", "flipped", "tp", "fn"):
        path = Path(f"reports/_keys_{name}.json")
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = {(str(k[0]), int(k[1])) for k in data["keys"]}
        in_replay = {(str(r[0]), int(r[1])) for r in details}
        absent = keys - in_replay
        print(f"  _keys_{name}.json: {len(keys)} 条"
              + (f"，⚠️ {len(absent)} 条不在产物中: {sorted(absent)[:3]}" if absent else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
