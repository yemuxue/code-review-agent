"""只重跑指定样本的判分，并把结果合并回既有产物。

为什么需要它：全量真实评测很贵（124 条约 75 分钟），
个别样本因网络超时失败时不应该重跑整批——那既浪费 token 也浪费时间。

用法：
    python scripts/replay_retry_samples.py <产物.json> [--max-rounds 2]

只重试产物里 recorded 为 ERROR 的样本；成功则修正混淆矩阵并更新 meta。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重跑产物中失败的样本并合并结果")
    parser.add_argument("artifact", help="replay 产物 JSON 路径")
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args(argv)

    artifact = Path(args.artifact)
    result = json.loads(artifact.read_text(encoding="utf-8"))
    errored = result.get("errored_samples") or []
    if not errored:
        print("[retry] 没有失败样本，无需重跑")
        return 0

    from src.llm_client import AnthropicClient
    from tests.eval.datasets import (
        DATASETS, _build_file_index, load_samples, resolve_sample_file,
    )
    from tests.eval.scoring import build_context
    from tests.eval_llm_agent_qa import DATASET_META, JUDGE_PROMPT

    meta = result.get("meta") or {}
    target = Path(str(meta.get("target_dir") or "."))
    dataset_id = str(meta.get("dataset_id") or DATASET_META["dataset_id"])

    spec = next((s for s in DATASETS if s.dataset_id == dataset_id), None)
    if spec is None:
        print(f"[retry] 未知数据集 {dataset_id}", file=sys.stderr)
        return 1
    samples, _ = load_samples(spec)
    by_key = {(str(s[0]), int(s[1])): s for s in samples}
    index = _build_file_index(target, spec.search_root)

    client = AnthropicClient(temperature=0.0)
    details = result.get("details") or []
    remaining = list(errored)

    for round_index in range(args.max_rounds):
        if not remaining:
            break
        still: list[dict] = []
        for item in remaining:
            time.sleep(args.delay)
            key = (item["file"], int(item["line"]))
            sample = by_key.get(key)
            if sample is None:
                still.append(item)
                continue
            resolved, _reason = resolve_sample_file(target, str(sample[0]), index)
            if resolved is None:
                still.append(item)
                continue
            try:
                snippet = build_context(resolved, int(sample[1]))
                answer = (client.chat([
                    {"role": "system", "content": JUDGE_PROMPT},
                    {"role": "user", "content":
                     f"Line {sample[1]} of {sample[0]} — bug?\n\n```\n{snippet}\n```"},
                ]).content or "").strip().upper()
                found = answer.startswith("YES")
            except Exception as exc:
                item["reason"] = f"{type(exc).__name__}: {exc}"
                still.append(item)
                continue

            is_bug = bool(sample[6])
            cell = ("TP" if found else "FN") if is_bug else ("FP" if found else "TN")
            # 修正混淆矩阵
            for name in ("true_positives", "false_positives", "false_negatives",
                         "true_negatives"):
                key_name = {"TP": "true_positives", "FP": "false_positives",
                            "FN": "false_negatives", "TN": "true_negatives"}[cell]
                if name == key_name:
                    result[name] = int(result.get(name, 0)) + 1
            result["scanned"] = int(result.get("scanned", 0)) + 1
            result["errored_samples"] = still if still else []
            result["details"] = [
                [item["file"], item["line"], is_bug, found, cell, answer[:200]]
                if (d[0], d[1]) == key else d
                for d in details
            ]
            details = result["details"]
            print(f"[retry] round{round_index + 1} {item['file']}:{item['line']} -> {cell}")

        remaining = still

    result["errored_samples"] = remaining
    result["meta"]["errored"] = len(remaining)
    result["meta"]["skipped"] = len(result.get("skipped_samples") or [])
    result["meta"]["healthy"] = (len(remaining) == 0
                                 and result["meta"]["skipped"] == 0
                                 and result["scanned"] == result["total"])
    tp, fp, fn = (result["true_positives"], result["false_positives"],
                  result["false_negatives"])
    if tp + fp:
        result["precision"] = f"{100 * tp / (tp + fp):.1f}%"
    if tp + fn:
        result["recall"] = f"{100 * tp / (tp + fn):.1f}%"
    if 2 * tp + fp + fn:
        result["f1_score"] = f"{2 * tp / (2 * tp + fp + fn):.2f}"
    result["meta"]["retry_note"] = f"失败样本经 {args.max_rounds} 轮补跑后合并"

    artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[retry] 剩余失败 {len(remaining)} 条；"
          f"P={result['precision']} R={result['recall']} F1={result['f1_score']} "
          f"healthy={result['meta']['healthy']}")
    return 0 if not remaining else 1


if __name__ == "__main__":
    raise SystemExit(main())
