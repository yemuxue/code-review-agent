"""从 A/B 运行日志恢复逐样本判定（details）。

背景：`judge_ab.py` 起初没有把 `details` 落盘，导致已经花掉 30 分钟的
真实评测**无法做逐样本翻转分析**——而"净变化"会把"多召回 8 条、
同时丢掉 3 条"掩盖成提升。

日志里每行形如：
    [ 12/40] rag/vector_store.py:54 -> FN  (LLM: NO — ...)
只要解析出 (file, line, cell) 就能重建 details，无需重跑 API。

用法：python scripts/ab_from_log.py <log> <out.json>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# [ 12/40] file.py:54 -> FN  (LLM: ...)
_LINE = re.compile(r"\[\s*\d+/\d+\]\s+(?P<file>\S+?):(?P<line>\d+)\s+->\s+"
                   r"(?P<cell>TP|FP|FN|TN|ERROR|SKIPPED)\b(?:\s+\(LLM:\s*(?P<answer>.*)\))?$")


def _read_log(log_path: Path) -> str:
    """读取日志文本，兼容 PowerShell `>` 重定向的 UTF-16 输出。

    ⚠️ Windows PowerShell 的 `>` 默认写 UTF-16LE。按 UTF-8 读会得到
    大量 NUL 字节、看起来"日志里没有匹配行"，从而误判为实验数据缺失。
    这里按 BOM 逐个尝试，最后回退到替换式 UTF-8。
    """
    raw = log_path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-8"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # UTF-16 误判 UTF-8 时会得到含 NUL 的字符串，用 NUL 比例判断
        if text.count("\x00") > len(text) // 100:
            continue
        return text
    return raw.decode("utf-8", errors="replace")


def parse(log_path: Path) -> list[list]:
    """解析日志里的逐样本判定，返回 details 形态的列表。"""
    details: list[list] = []
    seen: set[tuple[str, int]] = set()
    for raw in _read_log(log_path).splitlines():
        match = _LINE.search(raw.strip())
        if not match:
            continue
        file_name = match.group("file")
        line = int(match.group("line"))
        key = (file_name, line)
        cell = match.group("cell")
        answer = (match.group("answer") or "").strip()
        if cell in {"ERROR", "SKIPPED"}:
            continue
        if key in seen:
            # 重试轮会再打印同一行；以最后一次为准（即最终判定）
            for entry in details:
                if (entry[0], entry[1]) == key:
                    entry[4] = cell
                    entry[5] = answer
                    break
            continue
        is_bug = cell in {"TP", "FN"}
        found = cell in {"TP", "FP"}
        details.append([file_name, line, is_bug, found, cell, answer])
        seen.add(key)
    return details


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print("用法: python scripts/ab_from_log.py <log> <out.json>", file=sys.stderr)
        return 2
    log_path, out_path = Path(argv[0]), Path(argv[1])
    details = parse(log_path)
    payload = {"recovered_from": str(log_path), "count": len(details),
               "details": details}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    print(f"[recover] {log_path} -> {out_path}（{len(details)} 条）")
    print(f"[recover] 判定分布: {dict(Counter(d[4] for d in details))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
