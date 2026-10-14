"""标注锚点指纹 / Annotation anchors.

解决的问题
    `preflight()` 只校验「文件存在 + 行号在范围内」。2026-10-07 的漏报归因发现
    48% 的漏报属于"标注描述的缺陷在当前代码里已不存在"——标注行号仍然有效，
    但它指向的**内容**已经变了（缺陷被修掉，或代码重构后行号漂移）。

    仅靠人工比对 124 条不可持续。本模块为每条标注记录**内容指纹**：
        - `line_hash`    —— 标注行的规范化文本哈希
        - `context_hash` —— 标注行前后若干行的规范化文本哈希
    目标代码变化后即可机械判定：
        OK         内容与标注时一致 → 标注仍然有效
        RELOCATED  内容仍在，但在别的行 → 行号漂移，可自动修正
        CHANGED    该行内容变了 → 标注所描述的缺陷可能已被修复，**需人工复核**
        MISSING    文件或上下文整体找不到 → 需重新定位

设计取舍
    规范化必须宽容到"格式化/缩进/行尾空白变化不误报"，又要敏感到
    "语句被改写就报 CHANGED"。因此归一化：去首尾空白、压缩连续空白、
    丢弃纯注释与空行（注释改写不应触发复核）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# 锚点指纹的格式版本：归一化规则变化时必须升版本，否则旧指纹会被误判为 CHANGED
ANCHOR_VERSION = 1

# 搜索漂移时向上下各找多少行
RELOCATE_SEARCH_RADIUS = 30

# 参与 context_hash 的上下行数
CONTEXT_RADIUS = 3

_WHITESPACE = re.compile(r"\s+")


def normalize_line(text: str) -> str:
    """归一化：压缩空白 + 去首尾空白。缩进/对齐变化不应触发复核。"""
    return _WHITESPACE.sub(" ", text).strip()


def _is_significant(text: str) -> bool:
    """是否参与指纹：纯空行与纯注释行不参与（注释改写不应报警）。"""
    stripped = text.strip()
    if not stripped:
        return False
    return not stripped.startswith(("#", "//", "/*", "*", '"""', "'''"))


def _hash(parts: list[str]) -> str:
    payload = "\n".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprint(lines: list[str], line_no: int) -> dict | None:
    """计算标注行的锚点指纹（行号从 1 开始）。

    返回 {"version", "line_hash", "context_hash", "normalized"}。
    标注行本身不显著（空行/注释）时，用最近的显著行作为锚（等距时向下优先）——
    这与"标注行指向 docstring 或空行"的常见情形相符。
    """
    if not lines:
        return None
    index = line_no - 1
    if index < 0 or index >= len(lines):
        return None
    anchor_index = index
    if not _is_significant(lines[index]):
        # 标注行不显著（空行/注释/docstring）时，锚定到最近的显著行。
        # 等距时优先**向下**：注释与 docstring 描述的是其后的代码，
        # 锚到下方语句对"该缺陷是否已被修复"更敏感。
        for step in range(1, CONTEXT_RADIUS + 2):
            for candidate in (index + step, index - step):
                if 0 <= candidate < len(lines) and _is_significant(lines[candidate]):
                    anchor_index = candidate
                    break
            else:
                continue
            break

    context = []
    for offset in range(-CONTEXT_RADIUS, CONTEXT_RADIUS + 1):
        pos = anchor_index + offset
        if 0 <= pos < len(lines) and _is_significant(lines[pos]):
            context.append(normalize_line(lines[pos]))
    return {
        "version": ANCHOR_VERSION,
        "line_hash": _hash([normalize_line(lines[anchor_index])]),
        "context_hash": _hash(context),
        "anchor_line": anchor_index + 1,
    }


@dataclass
class AnchorCheck:
    """单条标注的锚点校验结果。"""

    sample_id: str
    file: str
    line: int
    status: str          # OK | RELOCATED | CHANGED | MISSING
    new_line: int = 0
    note: str = ""

    @property
    def needs_review(self) -> bool:
        return self.status in {"CHANGED", "MISSING"}


@dataclass
class AnchorReport:
    checks: list[AnchorCheck] = field(default_factory=list)

    @property
    def ok(self) -> list[AnchorCheck]:
        return [c for c in self.checks if c.status == "OK"]

    @property
    def relocated(self) -> list[AnchorCheck]:
        return [c for c in self.checks if c.status == "RELOCATED"]

    @property
    def changed(self) -> list[AnchorCheck]:
        return [c for c in self.checks if c.status == "CHANGED"]

    @property
    def missing(self) -> list[AnchorCheck]:
        return [c for c in self.checks if c.status == "MISSING"]

    @property
    def needs_review(self) -> list[AnchorCheck]:
        return [c for c in self.checks if c.needs_review]

    def summary_line(self) -> str:
        return (f"锚点校验: OK {len(self.ok)}、漂移 {len(self.relocated)}、"
                f"内容变化(需复核) {len(self.changed)}、缺失 {len(self.missing)}"
                f" / 共 {len(self.checks)}")


def build(samples: list[tuple], target_root: str | Path,
          resolve=None) -> dict:
    """为标注样本构建锚点清单（格式版本 + 每条指纹）。"""
    if resolve is None:
        from tests.eval.datasets import _build_file_index, resolve_sample_file
        index = _build_file_index(Path(target_root), "src")

        def resolve(root, name):
            return resolve_sample_file(Path(root), name, index)[0]

    target = Path(target_root)
    anchors: dict[str, dict] = {}
    for position, sample in enumerate(samples):
        file_name, line = str(sample[0]), int(sample[1])
        resolved = resolve(target, file_name)
        if resolved is None:
            continue
        try:
            lines = Path(resolved).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        fp = fingerprint(lines, line)
        if fp is None:
            continue
        anchors[f"#{position + 1}"] = {"file": file_name, "line": line, **fp}
    return {"anchor_version": ANCHOR_VERSION, "count": len(anchors),
            "anchors": anchors}


def verify(samples: list[tuple], target_root: str | Path, manifest: dict,
           resolve=None) -> AnchorReport:
    """按锚点清单校验当前代码，判定每条标注是否仍然有效。"""
    if manifest.get("anchor_version") != ANCHOR_VERSION:
        raise ValueError(
            f"锚点清单版本 {manifest.get('anchor_version')} 与当前 {ANCHOR_VERSION} 不一致，"
            "需重新生成（归一化规则已变更）")
    if resolve is None:
        from tests.eval.datasets import _build_file_index, resolve_sample_file
        index = _build_file_index(Path(target_root), "src")

        def resolve(root, name):
            return resolve_sample_file(Path(root), name, index)[0]

    target = Path(target_root)
    anchors = manifest.get("anchors") or {}
    report = AnchorReport()

    for position, sample in enumerate(samples):
        sample_id = f"#{position + 1}"
        file_name, line = str(sample[0]), int(sample[1])
        is_bug = bool(sample[6])
        expected = anchors.get(sample_id)
        if expected is None:
            # FP 陷阱故意用越界行号表示"问题不存在"（与 preflight 的约定一致），
            # 这类样本本就没有可锚定的代码，不应报为缺失。
            if not is_bug:
                report.checks.append(AnchorCheck(
                    sample_id, file_name, line, "OK",
                    note="FP 陷阱（越界行号约定），无需锚定"))
                continue
            report.checks.append(AnchorCheck(sample_id, file_name, line, "MISSING",
                                             note="清单中没有该标注的指纹"))
            continue
        resolved = resolve(target, file_name)
        if resolved is None:
            report.checks.append(AnchorCheck(sample_id, file_name, line, "MISSING",
                                             note="标注文件无法解析"))
            continue
        try:
            lines = Path(resolved).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError as exc:
            report.checks.append(AnchorCheck(sample_id, file_name, line, "MISSING",
                                             note=f"文件不可读: {exc}"))
            continue

        current = fingerprint(lines, line)
        if current and current["line_hash"] == expected["line_hash"]:
            report.checks.append(AnchorCheck(sample_id, file_name, line, "OK"))
            continue

        # 行号漂移：在该行附近搜索相同内容
        relocated = _find_relocated(lines, expected, line)
        if relocated is not None:
            report.checks.append(AnchorCheck(
                sample_id, file_name, line, "RELOCATED", new_line=relocated,
                note=f"内容仍在，行号已从 {line} 漂移到 {relocated}"))
            continue

        # 内容变了：判断是"同一位置的语句被改写"还是"整体结构变了"
        status = "CHANGED"
        note = "标注行内容已变化 —— 所描述的缺陷可能已被修复，需人工复核"
        if not expected.get("line_hash"):
            note = "指纹缺失"
        report.checks.append(AnchorCheck(sample_id, file_name, line, status, note=note))

    return report


def _find_relocated(lines: list[str], expected: dict, line: int) -> int | None:
    """在标注行附近寻找指纹一致的位置，返回新行号（找不到返回 None）。"""
    lo = max(0, line - 1 - RELOCATE_SEARCH_RADIUS)
    hi = min(len(lines), line - 1 + RELOCATE_SEARCH_RADIUS + 1)
    for index in range(lo, hi):
        if normalize_line(lines[index]) == "":
            continue
        candidate = fingerprint(lines, index + 1)
        if candidate and candidate["line_hash"] == expected["line_hash"]:
            return index + 1
    return None


def write_manifest(manifest: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    return out


def load_manifest(path: str | Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"锚点清单不存在: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from tests.eval.datasets import DATASETS, load_samples

    parser = argparse.ArgumentParser(description="标注锚点指纹：生成 / 校验")
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--dataset", default="llm-agent-qa-system-labeled")
    parser.add_argument("--target-dir", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--limit", type=int, default=0, help="只检查前 N 条")
    args = parser.parse_args(argv)

    spec = next((s for s in DATASETS if s.dataset_id == args.dataset), None)
    if spec is None:
        print(f"未知数据集: {args.dataset}", file=sys.stderr)
        return 1
    manifest_path = args.manifest or f"tests/eval/anchors_{spec.dataset_id}.json"
    samples, _ = load_samples(spec)
    if args.limit:
        samples = samples[:args.limit]

    if args.action == "build":
        manifest = build(samples, args.target_dir)
        path = write_manifest(manifest, manifest_path)
        print(f"[anchor] 已写入 {path}（{manifest['count']} 条指纹，"
              f"版本 {manifest['anchor_version']}）")
        return 0

    report = verify(samples, args.target_dir, load_manifest(manifest_path))
    print(f"[anchor] {report.summary_line()}")
    for check in report.relocated:
        print(f"  RELOCATED {check.sample_id} {check.file}:{check.line} "
              f"-> {check.new_line}")
    for check in report.needs_review:
        print(f"  {check.status} {check.sample_id} {check.file}:{check.line} — "
              f"{check.note}")
    # 漂移与内容变化都是"需要复核"，不是硬失败：代码变化本身是合法的，
    # 但必须让人看到"哪些标注的语义可能已过期"。
    return 0 if not report.needs_review else 1


if __name__ == "__main__":
    raise SystemExit(main())
