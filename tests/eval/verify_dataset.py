"""重建后的标注测试集：一条命令完成体检 / Rebuilt-dataset health check.

## 为什么需要这个文件

2026-10-07 的漏报归因发现：61 个漏报里 29 条（48%）标注描述的问题在当前代码里
**已不存在**。数据集因此在 2026-10-13 重建为 v1.2.0：

- 2 条行号漂移 → 重定位；
- 27 条「已修复」→ 翻成负例（同时成为新的 FP 陷阱）；
- 其中 3 条发现位置另有真实缺陷 → 改回正例并换描述；
- 真实问题分母 108 → 84。

重建之后有两件事必须能**反复验证**，否则过一段时间又会悄悄失效：

1. **标注锚点是否还对得上当前代码** —— `tests/eval/anchors.py` 给每条标注记
   「该行的规范化文本哈希」。目标仓库改一次代码，锚点就可能从 OK 变 CHANGED。
2. **对外引用的指标是不是当前版本的读数** —— 旧产物里没有版本号，导致
   「模型变好了」和「标注集变了」无法区分（历史上出过三份互相矛盾的读数）。

本文件把这两件事变成一条命令，输出人类可读报告 + 机器可读 JSON，
并可用退出码做门禁。

## 用法

    python -m tests.eval.verify_dataset --target-dir X:/VScode/llm-agent-qa-system
    python -m tests.eval.verify_dataset --target-dir ... --replay reports/xxx_replay.json --json out.json

退出码：0 = 可以用于评测；1 = 存在必须处理的问题（锚点失效 / 预检错误）。

## 边界（不要误读）

- 锚点 OK **不等于**标注语义一定正确，只说明「这一行的内容自标注以来没变」。
  语义正确性依赖 2026-10-13 的人工复核，无法机械验证。
- 本文件**不调用模型**，完全离线，可进 CI。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.eval import anchors as anchors_mod
from tests.eval import validity as validity_mod
from tests.eval.datasets import DATASETS, load_samples, preflight

DATASET_ID = "llm-agent-qa-system-labeled"
DEFAULT_MANIFEST = Path("tests/eval") / f"anchors_{DATASET_ID}.json"
DEFAULT_VALIDITY = Path("reports/_fn_attribution_classification.json")


def _get_spec():
    spec = next((s for s in DATASETS if s.dataset_id == DATASET_ID), None)
    if spec is None:
        raise SystemExit(f"数据集未注册: {DATASET_ID}")
    return spec


def check_anchor(manifest_path: Path, target_dir: str) -> dict:
    """标注锚点校验：当前代码与标注时是否一致。"""
    manifest = anchors_mod.load_manifest(manifest_path)
    samples, _ = load_samples(_get_spec())
    if len(manifest.get("anchors", {})) != len(samples):
        return {
            "status": "fail",
            "reason": (f"锚点清单条目数 {len(manifest.get('anchors', {}))} "
                       f"与样本数 {len(samples)} 不一致——样本已变更，需重建锚点"),
        }
    report = anchors_mod.verify(samples, target_dir, manifest)
    drifted = [f"{c.sample_id} {c.file}:{c.line}->{c.new_line}" for c in report.relocated]
    stale = [f"{c.sample_id} {c.file}:{c.line}" for c in report.needs_review]
    return {
        "status": "pass" if not report.needs_review else "fail",
        "summary": report.summary_line(),
        "ok": len(report.ok),
        "relocated": drifted,
        "needs_review": stale,
    }


def check_preflight(target_dir: str) -> dict:
    """版本元信息 + 文件/行号/枚举 + 重复位置。"""
    result = preflight(_get_spec(), target_dir)
    return {
        "status": "pass" if result.passed else "fail",
        "summary": result.summary_line(),
        "version": result.version,
        "matched": result.matched,
        "total": result.total,
        "errors": [f"{i.sample_id} {i.message}" for i in result.errors],
        "warnings": [f"{i.sample_id} {i.message}" for i in result.warnings],
    }


def check_replay_version(replays: list[Path], current_version: str) -> dict:
    """对外引用的指标是否当前版本的读数。

    旧产物没有 dataset_version 时视为「不可归属」，必须重新运行才可引用。
    """
    entries = []
    for path in replays:
        if not path.is_file():
            entries.append({"path": str(path), "status": "missing"})
            continue
        meta = (json.loads(path.read_text(encoding="utf-8")).get("meta") or {})
        version = meta.get("dataset_version")
        entries.append({
            "path": str(path),
            "dataset_version": version,
            "model": meta.get("model"),
            "run_at": meta.get("run_at"),
            "current": version == current_version,
        })
    usable = [e for e in entries if e.get("current")]
    return {
        "current_version": current_version,
        "status": "pass" if usable else "stale",
        "entries": entries,
        "note": ("存在与当前数据集版本一致的读数，可直接引用"
                 if usable else
                 "没有与当前数据集版本一致的读数——发布任何指标前必须重跑 replay"),
    }


def check_effective_recall(replay_path: Path, validity_path: Path) -> dict:
    """有效召回叠加：把 STALE 标注从分母里拿掉。

    注意：有效性映射来自一次性归因，**只对 1.0.0 的漏报集合成立**。
    数据集已重建，映射需要重新归因后才能用在新版本上（本函数会拒绝套用）。
    """
    if not replay_path.is_file() or not validity_path.is_file():
        return {"status": "skip", "reason": "缺少 replay 产物或有效性映射"}
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    meta = replay.get("meta") or {}
    if meta.get("dataset_version") != "1.0.0":
        return {
            "status": "skip",
            "reason": (f"有效性映射是按数据集 1.0.0 的漏报顺序归因的，"
                       f"当前产物是 {meta.get('dataset_version')}——套用会错位，需重新归因"),
        }
    validity = validity_mod.load_validity(validity_path)
    try:
        metrics = validity_mod.adjust(replay, validity)
    except ValueError as exc:
        return {"status": "skip", "reason": str(exc)}
    return {
        "status": "ok",
        "summary_lines": metrics.summary_lines(),
    }


def report(target_dir: str, manifest: Path, replay: list[Path],
           validity: Path) -> dict:
    pf = check_preflight(target_dir)
    an = check_anchor(manifest, target_dir)
    rv = check_replay_version(replay, pf.get("version", "unknown"))
    er = check_effective_recall(replay[0], validity) if replay else {"status": "skip",
                                                                    "reason": "未提供 replay"}
    ready = pf["status"] == "pass" and an["status"] == "pass"
    return {
        "dataset_id": DATASET_ID,
        "dataset_version": pf.get("version"),
        "ready_for_evaluation": ready,
        "preflight": pf,
        "anchor": an,
        "replay_versions": rv,
        "effective_recall": er,
    }


def _print(res: dict) -> None:
    print("=" * 76)
    print(f"测试集体检 / dataset health check — {res['dataset_id']} v{res['dataset_version']}")
    print("=" * 76)

    pf = res["preflight"]
    print(f"[{'PASS' if pf['status'] == 'pass' else 'FAIL'}] 预检  {pf['summary']}")
    for e in pf["errors"][:10]:
        print(f"        error   {e}")
    for w in pf["warnings"][:10]:
        print(f"        warning {w}")

    an = res["anchor"]
    if an["status"] == "pass":
        print(f"[PASS] 锚点  {an['summary']}")
    else:
        print(f"[FAIL] 锚点  {an.get('summary', an.get('reason'))}")
        for r in an.get("relocated", [])[:10]:
            print(f"        漂移    {r}")
        for s in an.get("needs_review", [])[:10]:
            print(f"        需复核  {s}")

    rv = res["replay_versions"]
    tag = {"pass": "PASS", "stale": "STALE", "skip": "SKIP"}.get(rv["status"], rv["status"])
    print(f"[{tag}] 读数版本  当前数据集 v{rv['current_version']}")
    for e in rv["entries"]:
        print(f"        {Path(e['path']).name}: dataset_version={e.get('dataset_version')} "
              f"model={e.get('model')} run_at={e.get('run_at')}")
    print(f"        → {rv['note']}")

    er = res["effective_recall"]
    if er["status"] == "ok":
        print("[OK  ] 有效召回叠加")
        for line in er["summary_lines"]:
            print("        " + line)
    else:
        print(f"[SKIP] 有效召回叠加  {er.get('reason')}")

    print("-" * 76)
    verdict = "可以用于评测" if res["ready_for_evaluation"] else "不可用于评测（见上）"
    print(f"结论：{verdict}")
    if rv["status"] != "pass":
        print("  ⚠ 注意：即使数据集本身可用，对外引用任何指标前都必须用当前版本重跑 replay。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建后测试集体检")
    parser.add_argument("--target-dir", required=True,
                        help="标注指向的外部仓库根目录")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--replay", action="append", default=[],
                        help="可重复传入；用于核对指标归属的产物")
    parser.add_argument("--validity", default=str(DEFAULT_VALIDITY))
    parser.add_argument("--json", default=None, help="把报告写入 JSON")
    args = parser.parse_args(argv)

    res = report(args.target_dir, Path(args.manifest),
                 [Path(p) for p in args.replay], Path(args.validity))
    _print(res)
    if args.json:
        Path(args.json).write_text(
            json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n报告已写入 {args.json}")
    return 0 if res["ready_for_evaluation"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
