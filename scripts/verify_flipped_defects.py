"""核验 agent 提出的 3 条"仍有其它真实缺陷"的翻转负例。

为什么必须自己核验：这 3 条一旦改回正例，**正例数从 81 变为 84**，
召回率的分母随之变化。把不成立的缺陷算成正例，等于人为抬高召回率——
这正是本次整轮整改要避免的错误。
"""

from __future__ import annotations

import pathlib

BASE = pathlib.Path("X:/VScode/llm-agent-qa-system/src")


def show(rel: str, lo: int, hi: int, title: str, marks: tuple[int, ...] = ()) -> None:
    path = BASE / rel
    lines = path.read_text(encoding="utf-8").splitlines()
    print(f"=== {title}  ({rel}, {len(lines)} 行) ===")
    for index in range(lo - 1, min(hi, len(lines))):
        mark = ">>>" if index + 1 in marks else "   "
        print(f"{mark}{index + 1:4d}| {lines[index]}")
    print()


def grep_usage(pattern: str, roots: list[str]) -> list[str]:
    hits: list[str] = []
    repo = BASE.parent
    for root in roots:
        for path in (repo / root).rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    hits.append(f"{path.relative_to(repo)}:{number}: {line.strip()[:80]}")
    return hits


def main() -> int:
    print("### 缺陷 1: react_agent.py:222 —— 强制总结的响应未经解析")
    show("agent/react_agent.py", 164, 176, "正常路径：走 _extract_final_answer")
    show("agent/react_agent.py", 216, 236, "超限路径：直接赋值给 final_answer", marks=(222, 223))
    show("agent/react_agent.py", 355, 365, "_extract_final_answer 定义")

    print("### 缺陷 2: rag/retriever.py:78 —— source 死存储 + 方法无引用")
    show("rag/retriever.py", 93, 106, "retrieve_and_format 尾部", marks=(98, 102))
    print("  retrieve_and_format 全仓引用:")
    for hit in grep_usage("retrieve_and_format", ["src", "tests", "scripts"]):
        print(f"    {hit}")
    print()

    print("### 缺陷 3: config/settings.py:65 —— 属性无引用")
    show("config/settings.py", 57, 78, "current_api_key / current_base_url", marks=(65,))
    for name in ("current_api_key", "current_base_url"):
        print(f"  {name} 全仓引用:")
        for hit in grep_usage(name, ["src", "tests", "scripts"]):
            print(f"    {hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
