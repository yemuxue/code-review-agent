"""导出待审计样本的完整代码上下文，供逐条人工审计。

## 为什么需要它

审计要求"读**完整函数/类**源码后判定，且必须引用实际行号与代码片段"。
只看 ±15 行是不够的：很多"缺陷"要看到函数边界、调用方、装饰器才能判断真假。
本脚本按"所属 def/class + 前后若干行"导出，并给出该条样本全部字段。

输出为 Markdown，便于分批阅读与逐条记录结论。
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET = Path("tests/eval_llm_agent_qa.py")
TARGET = Path("X:/VScode/llm-agent-qa-system")

DEF_RE = re.compile(r"^(\s*)(async\s+def|def|class)\s")


def _load():
    spec = importlib.util.spec_from_file_location("ds_audit", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve(file_name: str) -> Path | None:
    """文件名 -> 实际路径（样本里用的是短名或相对路径）。"""
    root = TARGET / "src"
    direct = root / file_name
    if direct.is_file():
        return direct
    hits = list(root.rglob(Path(file_name).name))
    return hits[0] if len(hits) == 1 else (hits[0] if hits else None)


def _enclosing(lines: list[str], line_no: int) -> tuple[int, int]:
    """返回 (起始行, 结束行) 的 1-based 闭区间。

    硬约束：**必须包含目标行**，且块长不超过 60 行。审计看的是"这一行有没有
    描述的那个缺陷"，因此宁可多给上下文，也不能让目标行落到窗口外
    （上一版就出现过导出块 [1-21] 却不含第 65 行的情况）。
    """
    index = line_no - 1
    if not 0 <= index < len(lines):
        return 1, min(len(lines), 1)

    # 向上找最近的 def/class（任意缩进，作为起点参考）
    anchor = index
    for probe in range(index, -1, -1):
        if DEF_RE.match(lines[probe]):
            anchor = probe
            break

    # 起点离目标太远时退回固定窗口，保证可读性
    start = anchor - 2
    if index - start > 45:
        start = index - 35
    start = max(0, start)

    end = min(len(lines) - 1, index + 30, start + 59)
    # 兜底：确保目标行在区间内
    start = min(start, index)
    end = max(end, index)
    return start + 1, end + 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出待审计样本的代码上下文")
    parser.add_argument("--start", type=int, default=85)
    parser.add_argument("--end", type=int, default=124)
    parser.add_argument("--out", default="reports/_audit_context.md")
    args = parser.parse_args(argv)

    module = _load()
    samples = module.EVAL_SAMPLES
    meta = module.DATASET_META

    out: list[str] = [
        f"# 待审计样本 #{args.start}-{args.end}",
        "",
        f"数据集 v{meta['version']}｜目标项目 `{TARGET}`",
        "",
    ]
    missing: list[int] = []
    for number in range(args.start, args.end + 1):
        if number > len(samples):
            break
        file_name, line, category, severity, desc_en, desc_cn, is_bug = \
            samples[number - 1]
        out.append(f"## #{number}  `{file_name}:{line}`  "
                   f"{category}/{severity}  "
                   f"{'正例' if is_bug else '负例'}")
        out.append("")
        out.append(f"- EN: {desc_en}")
        out.append(f"- CN: {desc_cn}")
        out.append("")
        path = _resolve(str(file_name))
        if path is None:
            missing.append(number)
            out.append("> ⚠️ 找不到源文件")
            out.append("")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        start, end = _enclosing(lines, int(line))
        rel = path.relative_to(TARGET)
        out.append(f"```python  # {rel}  [{start}-{end}] 共 {len(lines)} 行")
        for probe in range(start, end + 1):
            if probe > len(lines):
                break
            mark = ">>>" if probe == int(line) else "   "
            out.append(f"{mark} {probe:>4}| {lines[probe - 1]}")
        out.append("```")
        out.append("")

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"已写出 {target}（{len(out)} 行）")
    if missing:
        print(f"⚠️ 找不到源文件的样本: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
