# -*- coding: utf-8 -*-
"""标注数据集补丁引擎 / Annotation dataset patch engine.

## 为什么需要它

标注数据集重建过两次（v1.0.0 → v1.1.0 → v1.2.0），每次都是**手工编辑**
`tests/eval_llm_agent_qa.py` 里的 500 行字面量。手工编辑的问题是：

- 无法审计"这次到底改了什么"（版本说明靠人写，容易漏）；
- 极性翻转（正例↔负例）会改变**召回分母**，但分母变化没有落成可校验的记录；
- 改完忘了同步 `DATASET_META` 的计数，直到 preflight 报错才发现；
- 反复出现"只排除了原描述所指的问题，没排除该位置其它问题"——
  即 v1.2.0 变更说明里承认的那 3 条。

本引擎把"修数据集"变成**可复现的补丁应用**：

    补丁文件（JSON，声明每条样本的处置）
        ↓  apply
    tests/eval_llm_agent_qa.py（新版本号 + 变更记录 + 样本列表）
        ↓  自动校验
    DATASET_META 计数自洽 / 锚点重建 / 契约测试

附带的 `--dry-run` 可以在不写文件的情况下预览净变化（含召回分母变化）。

## 支持的处置动作

| action | 含义 | 对分母的影响 |
|---|---|---|
| `RELABEL_POSITIVE` | 转为正例（该位置确有真实缺陷） | 分母 +1 |
| `RELABEL_NEGATIVE` | 转为负例（该位置确实无问题，报出即误报） | 分母 −1 |
| `DESCRIBE` | 只更新描述（保留极性） | 不变 |
| `RELOCATE` | 行号漂移，改到新行 | 不变 |
| `MERGE_DUPLICATE` | 该位置与另一条重复，合并（并入 keep，删除本条） | 若删的是正例则分母 −1 |

## 用法

    python -m tests.eval.patch_dataset --patch tests/eval/patches/v1.3.0.json --dry-run
    python -m tests.eval.patch_dataset --patch tests/eval/patches/v1.3.0.json
    python -m tests.eval.patch_dataset --patch ... --target-dir X:/VScode/llm-agent-qa-system

补丁文件格式见 `tests/eval/patches/v1.3.0.json`。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MODULE = Path("tests/eval_llm_agent_qa.py")
PATCH_DIR = Path("tests/eval/patches")

ACTIONS = frozenset({
    "RELABEL_POSITIVE", "RELABEL_NEGATIVE", "DESCRIBE",
    "RELOCATE", "MERGE_DUPLICATE",
})


@dataclass
class Change:
    """单条样本的处置。"""

    sample_id: int
    action: str
    reason: str = ""
    evidence: str = ""
    desc_cn: str | None = None
    desc_en: str | None = None
    category: str | None = None
    severity: str | None = None
    new_line: int | None = None
    merge_into: int | None = None

    def validate(self) -> list[str]:
        errs = []
        if self.action not in ACTIONS:
            errs.append(f"未知 action: {self.action}")
        if self.action == "RELOCATE" and self.new_line is None:
            errs.append("RELOCATE 必须给 new_line")
        if self.action == "MERGE_DUPLICATE" and self.merge_into is None:
            errs.append("MERGE_DUPLICATE 必须给 merge_into")
        if self.action in {"RELABEL_POSITIVE", "RELABEL_NEGATIVE", "DESCRIBE"} \
                and not (self.desc_cn or self.desc_en):
            errs.append(f"{self.action} 建议给出新描述（desc_cn/desc_en）")
        if not self.reason:
            errs.append("必须给 reason（为什么这样处置）")
        return errs


@dataclass
class Patch:
    """一个补丁文件。"""

    from_version: str
    to_version: str
    summary: str = ""
    changes: list[Change] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Patch":
        data = json.loads(path.read_text(encoding="utf-8"))
        changes = [Change(**{k: v for k, v in c.items()
                            if k in Change.__dataclass_fields__})
                   for c in data.get("changes", [])]
        return cls(
            from_version=data["from_version"],
            to_version=data["to_version"],
            summary=data.get("summary", ""),
            changes=changes,
        )


def _load_module_samples(module_path: Path):
    """导入数据集模块，取回样本列表与元信息（只读，不改文件）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_dataset_module", module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.EVAL_SAMPLES), dict(mod.DATASET_META)


def _fmt_sample(s) -> str:
    """把一条样本渲染成与现有文件一致的源码文本。"""
    f, line, cat, sev, en, cn, is_bug = s
    return (
        f'    ({f!r}, {line!r}, {cat!r}, {sev!r},\n'
        f'     {en!r},\n'
        f'     {cn!r}, {is_bug!r}),'
    )


def apply_patch(samples: list[tuple], patch: Patch) -> tuple[list[tuple], list[dict]]:
    """应用补丁，返回 (新样本列表, 变更日志)。

    样本 id 是**应用前的 1-based 序号**（与审计报告一致）。
    MERGE_DUPLICATE 删除的条目在最后统一移除，避免删除导致后续 id 错位。
    """
    out = [list(s) for s in samples]
    log: list[dict] = []
    drop: set[int] = set()

    for ch in patch.changes:
        if not (1 <= ch.sample_id <= len(out)):
            raise SystemExit(f"样本 id 越界: {ch.sample_id}（共 {len(out)} 条）")
        errs = ch.validate()
        if errs:
            raise SystemExit(f"补丁 #{ch.sample_id} 非法: " + "；".join(errs))
        rec = out[ch.sample_id - 1]
        before = {"file": rec[0], "line": rec[1], "category": rec[2],
                  "severity": rec[3], "is_real_bug": rec[6]}
        note = f"{ch.action}: {ch.reason}"

        if ch.action == "RELABEL_POSITIVE":
            rec[6] = True
        elif ch.action == "RELABEL_NEGATIVE":
            rec[6] = False
        elif ch.action == "RELOCATE":
            rec[1] = int(ch.new_line)
        elif ch.action == "MERGE_DUPLICATE":
            if ch.merge_into == ch.sample_id:
                raise SystemExit(f"#{ch.sample_id} 不能并入自己")
            drop.add(ch.sample_id)
        # DESCRIBE 只改文本，下面统一处理

        if ch.desc_en:
            rec[4] = ch.desc_en
        if ch.desc_cn:
            rec[5] = ch.desc_cn
        if ch.category:
            rec[2] = ch.category
        if ch.severity:
            rec[3] = ch.severity

        after = {"file": rec[0], "line": rec[1], "category": rec[2],
                 "severity": rec[3], "is_real_bug": rec[6]}
        log.append({"sample_id": ch.sample_id, "action": ch.action,
                    "before": before, "after": after,
                    "reason": ch.reason, "evidence": ch.evidence,
                    "note": note})

    new_samples = [tuple(s) for i, s in enumerate(out, 1) if i not in drop]
    return new_samples, log


def _replace_block(text: str, start_marker: str, end_marker: str,
                   new_block: str) -> str:
    """替换 start_marker 到其后第一个 end_marker 之间的内容。

    end_marker 必须锚定在行首/行尾，否则会匹配到嵌套结构——例如 DATASET_META
    里的 `}` 之前就有 `]`，用裸 "\n]" 会截断到错误位置。
    """
    start = text.index(start_marker)
    end = text.index(end_marker, start + len(start_marker)) + len(end_marker)
    return text[:start] + new_block + text[end:]


def render_module(original: str, samples: list[tuple], meta_old: dict,
                  patch: Patch, log: list[dict]) -> str:
    """生成新的数据模块源码：样本块 + 元信息 + 变更记录。"""
    text = original

    # 1) 替换样本块。注意换回的块必须**包含** `EVAL_SAMPLES = [` 这一行，
    #    否则样本元组会变成裸缩进的语法错误。
    body = "\n".join(_fmt_sample(s) for s in samples)
    text = _replace_block(text, "EVAL_SAMPLES = [", "\n]\n",
                          "EVAL_SAMPLES = [\n" + body + "\n]\n")

    # 2) 更新元信息
    real = sum(1 for s in samples if s[6])
    meta_new = {
        "version": patch.to_version,
        "sample_count": len(samples),
        "real_bug_count": real,
        "fp_trap_count": len(samples) - real,
    }
    for key, val in meta_new.items():
        if isinstance(val, int):
            text = re.sub(rf'("{key}":\s*)\d+', rf'\g<1>{val}', text)
        else:
            text = re.sub(rf'("{key}":\s*)"[^"]*"', rf'\g<1>"{val}"', text)

    flips_pos = sum(1 for c in log if c["action"] == "RELABEL_POSITIVE")
    flips_neg = sum(1 for c in log if c["action"] == "RELABEL_NEGATIVE")
    merged = sum(1 for c in log if c["action"] == "MERGE_DUPLICATE")

    text = re.sub(
        r'("annotation_source":\s*)"[^"]*"',
        rf'\g<1>"人工标注 + {patch.to_version} 逐条独立审计修正'
        r'（由 tests/eval/patch_dataset.py 生成）"',
        text)

    notes = (f"{patch.summary}；本版处置："
             f"转正例 {flips_pos}、转负例 {flips_neg}、合并重复 {merged}、"
             f"其余描述/位置修正 {len(log) - flips_pos - flips_neg - merged}；"
             f"召回分母 {meta_old.get('real_bug_count')} → {real}")
    text = re.sub(r'("notes":\s*\()[^)]*\)', rf'\g<1>"{notes}")', text)

    # 3) 变更记录：作为注释块插在 EVAL_SAMPLES 之前。
    #    刻意不动 docstring——文件里有 DATASET_META 的多行字符串，
    #    按 """ 配对做手术会切错位置。
    lines = [f"# ═══ {patch.to_version} 变更记录（{patch.summary}）═══", "#"]
    for c in log:
        b, a = c["before"], c["after"]
        delta = []
        if b["is_real_bug"] != a["is_real_bug"]:
            delta.append("正例→负例" if b["is_real_bug"] else "负例→正例")
        if b["line"] != a["line"]:
            delta.append(f"行号 {b['line']}→{a['line']}")
        if b["category"] != a["category"]:
            delta.append(f"类别 {b['category']}→{a['category']}")
        if b["severity"] != a["severity"]:
            delta.append(f"严重度 {b['severity']}→{a['severity']}")
        lines.append(f"# #{c['sample_id']} {a['file']}:{a['line']} "
                     f"[{c['action']}] {'；'.join(delta) or '描述更新'}"
                     f"——{c['reason']}")
        if c.get("evidence"):
            lines.append(f"#   依据：{c['evidence']}")
    lines += ["#",
              "# ⚠️ 跨版本读数不可直接比较：分母已变化，历史 replay 必须标注 dataset_version。",
              "", ""]
    record = "\n".join(lines)
    # 以独立行插入到 EVAL_SAMPLES 定义之前（不用 str.replace，避免缩进异常）
    marker = "EVAL_SAMPLES = ["
    idx = text.index(marker)
    line_start = text.rindex("\n", 0, idx) + 1
    assert text[line_start:idx] == "", "EVAL_SAMPLES 定义前有意外缩进"
    text = text[:line_start] + record + text[line_start:]
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="标注数据集补丁引擎")
    parser.add_argument("--patch", required=True, help="补丁 JSON 路径")
    parser.add_argument("--module", default=str(MODULE))
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印净变化，不写文件")
    args = parser.parse_args(argv)

    module_path = Path(args.module)
    patch = Patch.load(Path(args.patch))
    original = module_path.read_text(encoding="utf-8")
    samples, meta = _load_module_samples(module_path)

    if meta.get("version") != patch.from_version:
        raise SystemExit(f"补丁声明 from_version={patch.from_version}，"
                         f"但模块实际是 {meta.get('version')}")

    new_samples, log = apply_patch(samples, patch)

    old_real = sum(1 for s in samples if s[6])
    new_real = sum(1 for s in new_samples if s[6])
    print("=" * 74)
    print(f"补丁 {patch.from_version} → {patch.to_version}：{patch.summary}")
    print("=" * 74)
    print(f"样本数 {len(samples)} → {len(new_samples)}")
    print(f"召回分母（正例数）{old_real} → {new_real}  ({new_real - old_real:+d})")
    print(f"负例数 {len(samples) - old_real} → {len(new_samples) - new_real}")
    print()
    print("逐条净变化：")
    for c in log:
        b, a = c["before"], c["after"]
        tag = []
        if b["is_real_bug"] != a["is_real_bug"]:
            tag.append("极性:正→负" if b["is_real_bug"] else "极性:负→正")
        if b["line"] != a["line"]:
            tag.append(f"行:{b['line']}→{a['line']}")
        print(f"  #{c['sample_id']:3d} {a['file']}:{a['line']:3d} "
              f"{c['action']:17s} {'、'.join(tag) or '仅描述'}"
              f"  —— {c['reason'][:60]}")

    if args.dry_run:
        print("\n[dry-run] 未写入任何文件。")
        return 0

    text = render_module(original, new_samples, meta, patch, log)
    module_path.write_text(text, encoding="utf-8")
    print(f"\n已写入 {module_path}")
    print("下一步：")
    print("  1) 更新 tests/test_rebuilt_dataset_contract.py 的基准值")
    print("  2) python -m tests.eval.anchors build --dataset llm-agent-qa-system-labeled "
          "--target-dir <TARGET>")
    print("  3) python -m tests.eval.verify_dataset --target-dir <TARGET>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
