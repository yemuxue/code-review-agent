"""按"归因类别 / 标签状态"挑选样本子集，生成 keys 文件。

为什么需要它：全量实验在当前模型/思考度下要 1.75 min/样本，124 条需 3.6 小时。
而关键问题往往只涉及一部分样本（例如"枚举提问能否召回那 30 条口径分歧"），
精准子集能在几十分钟内给出同样明确的答案。

kind 取值：
    disputed —— 判据口径分歧的漏报（不能被 STALE 解释的召回缺口）
    flipped  —— 2026-10-13 翻成负例的样本（用于检查它们是否真的"干净"）
    tp       —— 基线里已被正确检出的真实问题（用于检查回归）
    fn       —— 基线里全部漏报

用法：
    python scripts/select_samples.py disputed --out reports/_keys_disputed.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPLAY = "reports/eval_replay_sol_high_full_replay.json"
CLASSIFICATION = "reports/_fn_attribution_classification.json"
ORIGINALS = "reports/_stale_originals.json"

# 口径分歧：缺陷客观存在，模型却说 NO —— 这才是提示词有可能拿回的部分
DISPUTED_CATEGORIES = {"C_CRITERION_STRICT", "B_CONTEXT_MISSING", "E_AMBIGUOUS"}

# 每个 kind 最多取多少条（0 = 不限）。上限用于控制 API 实验时长。
DEFAULT_CAP = 0


def _load() -> tuple[list[tuple[str, int]], dict[int, str]]:
    replay = json.loads(Path(REPLAY).read_text(encoding="utf-8"))
    fn_keys = [(str(r[0]), int(r[1])) for r in replay["details"] if r[4] == "FN"]
    classified = json.loads(Path(CLASSIFICATION).read_text(encoding="utf-8"))
    categories = {int(i["id"]): str(i["category"]) for i in classified}
    return fn_keys, categories


def select(kind: str, cap: int) -> list[list[str]]:
    fn_keys, categories = _load()
    tp_keys = [(str(r[0]), int(r[1]))
               for r in json.loads(Path(REPLAY).read_text(encoding="utf-8"))["details"]
               if r[4] == "TP"]

    if kind == "disputed":
        keys = [fn_keys[i - 1] for i, cat in sorted(categories.items())
                if cat in DISPUTED_CATEGORIES]
    elif kind == "tp":
        keys = tp_keys
    elif kind == "fn":
        keys = fn_keys
    elif kind == "flipped":
        originals = json.loads(Path(ORIGINALS).read_text(encoding="utf-8"))
        keys = [(str(e["file"]), int(e["line"])) for e in originals
                if e["action"] != "RELOCATE"]
    elif kind == "mixed":
        # 口径分歧（待召回的缺口）+ 少量已检出的真问题（回归对照）。
        # 只有"召回涨了"而没有回归对照，无法判断改动是净收益还是此消彼长。
        disputed = [fn_keys[i - 1] for i, cat in sorted(categories.items())
                    if cat in DISPUTED_CATEGORIES]
        control = tp_keys[:cap] if cap else tp_keys
        keys = disputed + control
        # cap 在 mixed 语义下只约束对照集，不能再截断合并结果
        cap = 0
    else:
        raise ValueError(f"未知 kind: {kind}")

    # 去重并保持稳定顺序
    seen: set[tuple[str, int]] = set()
    ordered: list[list[str]] = []
    for file_name, line in keys:
        if (file_name, line) in seen:
            continue
        seen.add((file_name, line))
        ordered.append([file_name, line])
    return ordered[:cap] if cap else ordered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按归因类别挑选样本子集")
    parser.add_argument("kind", choices=("disputed", "flipped", "tp", "fn", "mixed"))
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP,
                        help="mixed 时表示取多少条 TP 作回归对照")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    keys = select(args.kind, args.cap)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"kind": args.kind, "count": len(keys), "keys": keys},
                             ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[select] {args.kind}: {len(keys)} 条 -> {out}")
    for file_name, line in keys[:6]:
        print(f"  {file_name}:{line}")
    if len(keys) > 6:
        print(f"  ...（共 {len(keys)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
