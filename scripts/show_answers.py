"""查看 replay 产物里每条样本的原始模型回复（排查解析器误判）。

用途：换了提示词/解析规则后，先确认"没检出"是模型真没发现，还是解析器
把合法但格式不同的回复丢掉了。后者是工具缺陷，不是模型能力问题。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _unwrap(raw: dict, variant: str | None) -> tuple[str, dict]:
    variants = raw.get("variants")
    if isinstance(variants, dict):
        key = variant or next(iter(variants), "")
        return key, variants.get(key, {})
    return variant or "?", raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="查看产物中的原始模型回复")
    parser.add_argument("artifact")
    parser.add_argument("--variant", default=None)
    parser.add_argument("--only", choices=("FN", "FP", "TP", "TN", "all"), default="all")
    parser.add_argument("--chars", type=int, default=300)
    args = parser.parse_args(argv)

    raw = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    name, data = _unwrap(raw, args.variant)
    print(f"产物 {args.artifact}  变体 {name}  "
          f"判据={data.get('meta', {}).get('judge_variant')}\n")

    shown = 0
    for row in data.get("details") or []:
        cell = str(row[4])
        if args.only != "all" and cell != args.only:
            continue
        answer = str(row[5])
        print(f"[{cell}] {row[0]}:{row[1]}")
        print(f"      {answer[:args.chars]}")
        shown += 1
    print(f"\n共显示 {shown} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
