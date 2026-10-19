"""数据集事实画像：来源、长度、唯一性、构成。

用途：回答"数据集多大/多长/一条对应几个问题"这类必须给数字的问题。
所有口径都从代码与数据集本身算，不手填。
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

TARGET = Path("X:/VScode/llm-agent-qa-system")
DATASET = Path("tests/eval_llm_agent_qa.py")


def _load():
    spec = importlib.util.spec_from_file_location("ds_profile", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _approx_tokens(text: str) -> int:
    """粗估 token：CJK 约 1.5 字/token，ASCII 约 4 字符/token。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 1.5 + other / 4)


def main() -> int:
    from tests.eval.datasets import (
        DATASETS, _build_file_index, load_samples,
    )
    from tests.eval.judge import context_lines

    module = _load()
    samples = module.EVAL_SAMPLES
    meta = module.DATASET_META
    spec = next(s for s in DATASETS if s.dataset_id == meta["dataset_id"])
    loaded, _ = load_samples(spec)
    index = _build_file_index(TARGET, spec.search_root)

    window = context_lines("strict")
    print("=" * 74)
    print("数据集事实画像")
    print("=" * 74)
    print(f"dataset_id      : {meta['dataset_id']}")
    print(f"version         : {meta['version']}")
    print(f"样本总数        : {meta['sample_count']}  "
          f"（正例 {meta['real_bug_count']} / 负例 {meta['fp_trap_count']}）")
    print(f"目标项目        : {TARGET}")
    print(f"判分上下文窗口  : ±{window} 行（≤ {window * 2 + 1} 行/条）")

    # ── 类别与严重度 ──
    print("\n[1] 构成")
    categories = Counter(str(s[2]) for s in samples)
    severities = Counter(str(s[3]) for s in samples)
    print("  类别: " + "、".join(f"{k} {v}" for k, v in categories.most_common()))
    print("  严重度: " + "、".join(f"{k} {v}" for k, v in severities.most_common()))

    # ── 文件分布 ──
    files = Counter(str(s[0]) for s in samples)
    total_lines = 0
    print(f"\n[2] 覆盖文件 {len(files)} 个（样本数 / 该文件总行数）")
    for name, count in files.most_common(8):
        resolved = TARGET / "src" / name
        # 文件名可能是短名（如 react_agent.py），需按索引解析
        from tests.eval.datasets import resolve_sample_file
        found, _ = resolve_sample_file(TARGET, name, index)
        n = len(Path(found).read_text(encoding="utf-8", errors="replace").split("\n")) if found else 0
        total_lines += n
        print(f"    {name:<32} {count:>3} 条 / {n:>4} 行")
    print(f"    （其余 {max(0, len(files) - 8)} 个文件略）")
    print(f"  被标注文件总行数: {total_lines} 行")

    # ── 描述长度 ──
    desc_en = [len(str(s[4])) for s in samples]
    desc_cn = [len(str(s[5])) for s in samples]
    print("\n[3] 标注描述长度（字符）")
    for label, values in (("英文描述", desc_en), ("中文描述", desc_cn)):
        print(f"    {label}: 最短 {min(values)}、中位 {statistics.median(values):.0f}、"
              f"最长 {max(values)}、平均 {statistics.mean(values):.1f}")

    # ── 送入模型的上下文长度 ──
    from tests.eval.scoring import build_context
    from tests.eval.datasets import resolve_sample_file
    line_counts, char_counts, token_counts = [], [], []
    for sample in samples:
        resolved, _reason = resolve_sample_file(TARGET, str(sample[0]), index)
        if resolved is None:
            continue
        try:
            snippet = build_context(resolved, int(sample[1]), window)
        except OSError:
            continue
        line_counts.append(snippet.count("\n") + 1)
        char_counts.append(len(snippet))
        token_counts.append(_approx_tokens(snippet))
    print(f"\n[4] 实际送入判分模型的代码片段（±{window} 行，n={len(char_counts)}）")
    for label, values in (("行数", line_counts), ("字符数", char_counts),
                          ("估算 token", token_counts)):
        print(f"    {label}: 最短 {min(values)}、中位 {statistics.median(values):.0f}、"
              f"最长 {max(values)}、平均 {statistics.mean(values):.1f}")

    # ── 位置唯一性 ──
    print("\n[5] 位置唯一性（一条数据是否只对应一个问题）")
    positions = Counter((str(s[0]), int(s[1])) for s in samples)
    dupes = {k: v for k, v in positions.items() if v > 1}
    print(f"    样本 {len(samples)} 条，唯一位置 {len(positions)} 个")
    if dupes:
        print(f"    ⚠️ {len(dupes)} 个位置有重复标注：")
        for key, count in dupes.items():
            entries = [s for s in samples if (str(s[0]), int(s[1])) == key]
            print(f"      {key[0]}:{key[1]}  共 {count} 条")
            for e in entries:
                print(f"        - [{e[2]}/{e[3]}] {str(e[5])[:56]}")
    else:
        print("    ✅ 无重复位置")

    # ── 锚点覆盖 ──
    anchor_path = Path(f"tests/eval/anchors_{meta['dataset_id']}.json")
    if anchor_path.is_file():
        anchors = json.loads(anchor_path.read_text(encoding="utf-8"))
        print(f"\n[6] 锚点指纹: {anchors.get('count')} 条已记录"
              f"（版本 {anchors.get('anchor_version')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
