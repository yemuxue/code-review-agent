"""导出 29 条 STALE 标注的原始文本，供生成新数据集条目。

同时给出建议的新标签（供人工确认）：
    RELOCATE          → 保持 is_bug=True，只改行号
    RELABEL_NEGATIVE  → is_bug=False，描述加 FIXED 前缀说明"该问题已修复"
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 人工确认后的处置（依据 stale-triage-v2 的逐条证据 + 我对 3 条 RELOCATE 的复核）
RELOCATE_TO = {9: 76, 11: 106}          # stale_id -> 新的真实行号


def _load_dataset_module():
    path = Path("tests/eval_llm_agent_qa.py")
    spec = importlib.util.spec_from_file_location("ds", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    classified = json.loads(
        Path("reports/_fn_attribution_classification.json").read_text(encoding="utf-8"))
    stale = sorted(int(i["id"]) for i in classified
                   if i["category"] == "A_ANNOTATION_WEAK")

    replay = json.loads(
        Path("reports/eval_replay_sol_high_full_replay.json").read_text(encoding="utf-8"))
    fn_keys = [(str(r[0]), int(r[1])) for r in replay["details"] if r[4] == "FN"]
    key_of_id = {i: fn_keys[i - 1] for i in range(1, len(fn_keys) + 1)}

    module = _load_dataset_module()
    by_key = {(str(s[0]), int(s[1])): s for s in module.EVAL_SAMPLES}

    out = []
    for stale_id in stale:
        sample = by_key[key_of_id[stale_id]]
        action = "RELOCATE" if stale_id in RELOCATE_TO else "RELABEL_NEGATIVE"
        out.append({
            "stale_id": stale_id,
            "file": sample[0],
            "line": sample[1],
            "category": sample[2],
            "severity": sample[3],
            "desc_en": sample[4],
            "desc_cn": sample[5],
            "is_bug": sample[6],
            "action": action,
            "new_line": RELOCATE_TO.get(stale_id, 0),
        })

    path = Path("reports/_stale_originals.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"导出 {len(out)} 条 -> {path}")
    print(f"  RELOCATE: {sum(1 for o in out if o['action'] == 'RELOCATE')} 条")
    print(f"  RELABEL_NEGATIVE: {sum(1 for o in out if o['action'] != 'RELOCATE')} 条")
    print()
    for entry in out:
        tag = (f"RELOCATE -> {entry['new_line']}" if entry["action"] == "RELOCATE"
               else "RELABEL_NEGATIVE")
        print(f"  id{entry['stale_id']:<3} {entry['file']}:{entry['line']}  {tag}")
        print(f"        {entry['desc_cn'][:66]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
