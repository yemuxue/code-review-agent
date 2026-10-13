"""持久性漏报的理由归因（离线，零 API 成本）。

用途：形成"下一步该试什么"的假设，而不是继续凭猜烧 API。

输入：候选产物（含模型理由）+ 基线产物（用于判定哪些漏报是"两轮都 NO"）。

输出：
    - 持久性漏报（两轮都判 NO）的条数
    - 按模型理由的关键词主题归类
    - 抽样原文，供人工判断"这是推理错误还是合理分歧"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

THEMES = {
    "A 认为代码正确/无问题": ["correct", "is fine", "valid", "no issue", "not a bug",
                             "properly", "correctly", "appropriate", "works"],
    "B 认为是有意设计": ["intentional", "intentionally", "by design", "deliberate",
                         "purpose", "expected"],
    "C 认为无关紧要/风格": ["cosmetic", "style", "minor", "trivial", "naming",
                            "subjective", "taste", "nitpick"],
    "D 证据不足/看不到": ["cannot", "can't", "not visible", "not shown", "insufficient",
                          "outside", "elsewhere", "unclear", "no evidence"],
    "E 标注行本身非代码": ["docstring", "comment", "separator", "blank", "only a",
                           "no executable"],
}


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print("用法: python scripts/fn_reasons.py <candidate.json> <baseline.json>",
              file=sys.stderr)
        return 2
    cand = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    base = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    base_cells = {(str(r[0]), int(r[1])): str(r[4]) for r in base.get("details") or []}

    persistent = [(str(r[0]), int(r[1]), str(r[5]))
                  for r in cand.get("details") or []
                  if r[4] == "FN" and base_cells.get((str(r[0]), int(r[1]))) == "FN"]
    print(f"候选产物: {argv[0]}")
    print(f"两轮都判 NO 的持久性漏报: {len(persistent)} 条\n")

    print("按模型理由的主题归类（一条可能命中多个主题）")
    for label, keywords in THEMES.items():
        hits = [p for p in persistent
                if any(k in p[2].lower() for k in keywords)]
        pct = f"{len(hits) / len(persistent):.0%}" if persistent else "n/a"
        print(f"  {label:<22} {len(hits):>3} 条 ({pct})")

    no_reason = [p for p in persistent if len(p[2]) < 8]
    print(f"\n  无理由记录: {len(no_reason)} 条"
          + ("（判据变更前的产物没有保存模型原话）" if no_reason else ""))

    print("\n--- 抽样（每条都需人工判断：推理错误 还是 合理分歧）---")
    for file_name, line, answer in persistent[:8]:
        print(f"  {file_name}:{line}")
        print(f"    {answer[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
