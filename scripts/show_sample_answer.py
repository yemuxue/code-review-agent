"""查看指定样本在既有产物中的模型判定（零 API）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ARTIFACT = Path("reports/eval_replay_sol_high_full_replay.json")


def main(argv: list[str]) -> int:
    numbers = [int(x) for x in argv] or [123, 124]
    data = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    details = data.get("details") or []
    for number in numbers:
        row = details[number - 1]
        found = bool(row[3])
        print(f"#{number}  {row[0]}:{row[1]}  found={found}  cell={row[4]}")
        print(f"   answer: {str(row[5])[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
