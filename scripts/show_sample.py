"""展示单条数据点长什么样：标注本体 + 模型实际收到的输入。

回答「每一条数据是什么样子」：数据点分两层——
  1) 标注层：数据集里的 7 元组（人写的）
  2) 输入层：真正送进判分模型的那段文本（系统提示词 + 问题 + 代码片段）

本脚本两层都打印真实内容，不示意。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TARGET = Path("X:/VScode/llm-agent-qa-system")
DATASET = Path("tests/eval_llm_agent_qa.py")
FIXED = ("【已修复】", "[FIXED]")


def _load():
    spec = importlib.util.spec_from_file_location("ds_show", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kind(sample) -> str:
    if bool(sample[6]):
        return "正例"
    text = str(sample[4]) + str(sample[5])
    return "负例·审计翻正为负" if any(m in text for m in FIXED) else "负例·原始 FP 陷阱"


def _print_tuple(index: int, sample) -> None:
    file_name, line, category, severity, desc_en, desc_cn, is_bug = sample
    print(f"\n  样本 #{index}  [{_kind(sample)}]")
    print("  (" )
    print(f"      {file_name!r}, {line}, {category!r}, {severity!r},")
    print(f"      {desc_en!r},")
    print(f"      {desc_cn!r},")
    print(f"      {is_bug}),")
    print("  )")


def main() -> int:
    module = _load()
    samples = module.EVAL_SAMPLES
    meta = module.DATASET_META

    print("=" * 76)
    print(f"数据集 v{meta['version']}｜{len(samples)} 条"
          f"（正例 {meta['real_bug_count']} / 负例 {meta['fp_trap_count']}）")
    print("=" * 76)

    print("\n[字段]  (文件名, 行号, 类别, 严重度, 英文描述, 中文描述, 是否真问题)")
    print("  文件+行号 = 指向目标项目里的哪一行")
    print("  是否真问题: True = 该位置确有缺陷（模型应答 YES）")
    print("              False = 该位置无问题（模型应答 YES 即误报）")

    # 各挑一条代表
    seen = set()
    for i, sample in enumerate(samples, 1):
        kind = _kind(sample)
        if kind in seen:
            continue
        # 跳过重复位置那条，单独展示
        if (str(sample[0]), int(sample[1])) == ("tools/calculator.py", 105):
            continue
        seen.add(kind)
        _print_tuple(i, sample)
        if len(seen) == 3:
            break

    # 重复位置
    dupes = [(i, s) for i, s in enumerate(samples, 1)
             if (str(s[0]), int(s[1])) == ("tools/calculator.py", 105)]
    print("\n  ── 唯一一处「同一行两条标注」（已知度量偏差）──")
    for i, s in dupes:
        _print_tuple(i, s)

    # ── 模型实际收到的输入 ──
    from tests.eval.datasets import DATASETS, _build_file_index, resolve_sample_file
    from tests.eval.judge import context_lines, judge_prompt
    from tests.eval.scoring import build_context

    spec = next(s for s in DATASETS if s.dataset_id == meta["dataset_id"])
    index = _build_file_index(TARGET, spec.search_root)
    demo_index, demo = next((i, s) for i, s in enumerate(samples, 1)
                            if bool(s[6]) and str(s[0]) == "react_agent.py")
    resolved, _ = resolve_sample_file(TARGET, str(demo[0]), index)
    window = context_lines("strict")
    snippet = build_context(resolved, int(demo[1]), window)
    prompt = judge_prompt("strict")

    print("\n" + "=" * 76)
    print(f"模型实际收到的输入（样本 #{demo_index}：{demo[0]}:{demo[1]}）")
    print("=" * 76)
    print(f"\n【system】判据提示词（{len(prompt)} 字符，此处截断展示）")
    for ln in prompt.split("\n")[:6]:
        print(f"  | {ln}")
    print("  | ...")

    question = f"Line {demo[1]} of {demo[0]} — bug?\n\n```\n{snippet}\n```"
    print(f"\n【user】问题 + 代码片段（共 {question.count(chr(10)) + 1} 行，"
          f"片段 ±{window} 行）")
    print("  " + "-" * 70)
    for ln in question.split("\n"):
        print(f"  {ln}")
    print("  " + "-" * 70)

    print("\n【模型输出】只要求 YES/NO + 一句理由，例如：")
    print("  YES — the forced-summary path assigns the raw response to final_answer ...")
    print("\n【判分】与标注的 is_real_bug 比对 → TP / FP / FN / TN，"
          "不计模型自我评价")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
