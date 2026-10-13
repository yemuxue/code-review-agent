"""标注数据集注册表与预检 / Labeled dataset registry & preflight.

解决的问题
    `tests/eval_llm_agent_qa.py` 与 `tests/eval_dataset.py` 都是裸的 7 元组列表：
    没有 id、没有版本、没有预检。后果是真实发生过的两类事故：

    1. **静默跳过**：文件找不到时 `continue`，只让 `scanned < total` 偷偷暴露
       （历史上出现过 16/124 未被发现）。
    2. **basename 兜底不安全**：外部项目里 `tools/base.py`、`llm/base.py`、
       `memory/base.py` 三个同名文件，用文件名兜底会指到错误的文件，
       而标注点看起来"匹配成功"。

本模块把"标注集能不能用"变成可机器判定的前置条件：
    - 每个样本有稳定的全局 ID（`<dataset_id>#<序号>`）与所属角色；
    - `preflight()` 逐条校验文件存在、行号在范围内、枚举合法、basename 不歧义；
    - 严重问题（error）在 CI 里直接失败，不靠人看报告。

⚠️ **已知的能力边界（2026-10-07 漏报归因实测）**：
    本模块只校验「文件存在 + 行号在范围内」，**不校验「该行是否仍有标注所
    描述的问题」**。实测 61 个漏报中有 29 个（48%）属于后者——标注描述的缺陷
    在当前代码里已被修复、或与代码直接矛盾（例如标注称"无异常处理"，
    而源码里就是 try/except）。

    因此：`preflight()` 通过 **不等于** 标注语义仍然有效。
    修正方向是给每个标注记录"锚点代码指纹"（例如该行的规范化文本哈希），
    在代码变化时提示复核，而不是靠人工逐条比对。
    `scripts/fn_attribution.py` 与 `fn_attribution_summary.py` 用于量化这一风险。

设计约束：纯函数 + 只读文件系统访问，不调用任何 LLM，可完全离线运行。
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 允许的枚举值：与标注格式约定一致
ALLOWED_CATEGORIES = frozenset({"BUG", "SECURITY", "PERF", "STYLE"})
ALLOWED_SEVERITIES = frozenset({"High", "Medium", "Low"})

# 标注元组结构：(file, line, category, severity, description_en, description_cn, is_real_bug)
SAMPLE_FIELDS = 7


@dataclass(frozen=True)
class DatasetSpec:
    """一个标注数据集的声明。"""

    dataset_id: str
    module_path: str          # 相对仓库根的模块文件路径
    default_target: str       # 样本里的文件相对哪个根目录；"." = 仓库根
    description: str
    # basename 索引的搜索根（相对 target）。必须与 DATASET_META.target_layout
    # 一致：既漏掉又重复扫描都会让"同名文件"判定失真。
    search_root: str = "src"


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        dataset_id="llm-agent-qa-system-labeled",
        module_path="tests/eval_llm_agent_qa.py",
        # 外部仓库，正常不随本仓库存在；CI 跑本仓自评集，外部集需显式给路径
        default_target="",
        description="124 条：第三方项目 llm-agent-qa-system 完整审查标注",
    ),
    DatasetSpec(
        dataset_id="code-review-agent-self",
        module_path="tests/eval_dataset.py",
        default_target=".",
        description="33 条：本仓库自评标注",
    ),
)


@dataclass
class DatasetIssue:
    """一条预检问题。severity=error 会否决策集可用。"""

    severity: str  # error | warning
    message: str
    sample_id: str = ""


@dataclass
class PreflightResult:
    """预检结果。"""

    dataset_id: str
    version: str
    total: int
    matched: int = 0
    skipped: int = 0
    ambiguous: int = 0
    issues: list[DatasetIssue] = field(default_factory=list)
    sample_ids: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[DatasetIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[DatasetIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def match_ratio(self) -> float:
        return self.matched / self.total if self.total else 0.0

    @property
    def passed(self) -> bool:
        """只要有 error 级问题即不可用；跳过/歧义单独暴露但由调用方决定门槛。"""
        return not self.errors

    def summary_line(self) -> str:
        return (f"{self.dataset_id} v{self.version}: "
                f"匹配 {self.matched}/{self.total}"
                + (f"，跳过 {self.skipped}" if self.skipped else "")
                + (f"，歧义 {self.ambiguous}" if self.ambiguous else "")
                + f"，error {len(self.errors)}，warning {len(self.warnings)}")


def load_dataset_module(spec: DatasetSpec, repo_root: str | Path = "."):
    """按文件路径加载数据集模块（避免依赖包式导入，数据集不是包）。

    用 `spec_from_file_location` 而不是 `import_module`：数据集文件位于
    tests/ 下且没有包结构，直接按路径加载最稳，也不会触发 `__main__` 块。
    """
    path = Path(repo_root) / spec.module_path
    if not path.is_file():
        raise FileNotFoundError(f"数据集模块不存在: {path}")
    module_name = f"eval_dataset_{spec.dataset_id.replace('-', '_')}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"无法加载数据集模块: {path}")
    module = importlib.util.module_from_spec(module_spec)
    # 数据集里 `from __future__` 等相对自身文件解析，不需要放进 sys.modules，
    # 但放进去可让 dataclass/typing 的自省更可靠
    sys.modules[module_name] = module
    try:
        module_spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def load_samples(spec: DatasetSpec, repo_root: str | Path = ".") -> tuple[list[tuple], dict]:
    """加载数据集的样本与元信息，并做结构校验。"""
    module = load_dataset_module(spec, repo_root)
    samples = list(getattr(module, "EVAL_SAMPLES", []))
    meta = dict(getattr(module, "DATASET_META", {}))
    if not samples:
        raise ValueError(f"{spec.module_path} 未定义 EVAL_SAMPLES 或为空")
    for index, sample in enumerate(samples):
        if not isinstance(sample, tuple) or len(sample) != SAMPLE_FIELDS:
            raise ValueError(
                f"{spec.module_path} 第 {index + 1} 条样本结构非法（期望 {SAMPLE_FIELDS} 元组）: "
                f"{sample!r}")
    return samples, meta


def _resolve_target(spec: DatasetSpec, target_root: str | Path | None) -> Path | None:
    """确定标注文件相对哪个根目录解析。"""
    if target_root is not None:
        return Path(target_root)
    if spec.default_target:
        return Path(spec.default_target)
    return None


# 构建文件索引时要排除的目录：同名文件出现在这些位置会造成假歧义
# （例如 .worktrees/ 下的仓库副本、__pycache__ 下的编译产物）。
_INDEX_EXCLUDES = ("__pycache__", ".worktrees", ".git", ".pytest_cache",
                   ".mypy_cache", ".ruff_cache", ".sandbox", "node_modules",
                   ".eval-runs", ".claude", ".agents", "build", "dist")


def _build_file_index(target: Path, search_root: str = "src") -> dict[str, list[Path]]:
    """建立 basename → 候选文件列表的索引。

    只扫描 `target/<search_root>`（与数据集声明的 target_layout 对应）。
    旧实现同时扫 `target/src` 与 `target`，而 `target` 本身就包含 `src`，
    于是每个文件被登记两次 → 所有标注都被判成"同名歧义"。
    """
    root = target / search_root if search_root else target
    index: dict[str, list[Path]] = {}
    if not root.is_dir():
        return index
    for candidate in root.rglob("*.py"):
        parts = set(candidate.parts)
        if any(excluded in parts for excluded in _INDEX_EXCLUDES):
            continue
        index.setdefault(candidate.name, []).append(candidate)
    for candidates in index.values():
        candidates.sort()
    return index


def resolve_sample_file(target: Path, file_name: str,
                        index: dict[str, list[Path]]) -> tuple[Path | None, str]:
    """把标注里的文件名解析成真实文件。

    解析顺序：
        1. `target/src/<file_name>`（数据集声明的 target_layout）
        2. `target/<file_name>`
        3. 按 basename 在全项目索引里查找——**仅当唯一命中时**才接受。

    第 3 步是关键的安全约束：外部项目里 `tools/base.py`、`llm/base.py`、
    `memory/base.py` 三个同名文件，若像旧实现那样"取最后一个匹配"，
    标注点会静默指到错误文件，并让评测结果看起来正常。

    返回 (路径 or None, 说明)。
    """
    for candidate in (target / "src" / file_name, target / file_name):
        if candidate.is_file():
            return candidate, ""

    basename = str(file_name).split("/")[-1]
    candidates = index.get(basename, [])
    if len(candidates) == 1:
        return candidates[0], ""
    if len(candidates) > 1:
        return None, (f"同名文件有 {len(candidates)} 个，无法安全解析："
                      + ", ".join(str(c) for c in candidates[:4]))
    return None, f"未找到任何名为 {basename} 的文件"


def preflight(spec: DatasetSpec, target_root: str | Path | None = None,
              repo_root: str | Path = ".") -> PreflightResult:
    """对标注集做离线预检。

    target_root=None 且数据集声明了 default_target 时用默认根；
    外部数据集（default_target 为空）必须显式提供 target_root，
    否则记为"未提供目标目录"而不是假装通过。
    """
    samples, meta = load_samples(spec, repo_root)
    result = PreflightResult(
        dataset_id=spec.dataset_id,
        version=str(meta.get("version", "unknown")),
        total=len(samples),
    )

    # ── 版本元信息与实际内容一致性 ──
    declared = meta.get("sample_count")
    if declared is not None and int(declared) != len(samples):
        result.issues.append(DatasetIssue(
            "error",
            f"DATASET_META.sample_count={declared} 与实际样本数 {len(samples)} 不一致"
            "（改了标注必须同步升版本并更新计数）"))
    real = sum(1 for s in samples if s[6])
    if meta.get("real_bug_count") is not None and int(meta["real_bug_count"]) != real:
        result.issues.append(DatasetIssue(
            "error",
            f"DATASET_META.real_bug_count={meta['real_bug_count']} 与实际 {real} 不一致"))
    if meta.get("fp_trap_count") is not None and int(meta["fp_trap_count"]) != len(samples) - real:
        result.issues.append(DatasetIssue(
            "error",
            f"DATASET_META.fp_trap_count={meta['fp_trap_count']} 与实际 "
            f"{len(samples) - real} 不一致"))

    target = _resolve_target(spec, target_root)
    if target is None:
        result.issues.append(DatasetIssue(
            "error", "未提供目标项目目录（该数据集的标注指向外部仓库，必须显式传入）"))
        result.skipped = len(samples)
        return result
    if not target.is_dir():
        result.issues.append(DatasetIssue("error", f"目标项目目录不存在: {target}"))
        result.skipped = len(samples)
        return result

    index = _build_file_index(target, spec.search_root)
    if not index:
        result.issues.append(DatasetIssue(
            "error", f"在 {target / spec.search_root} 下未找到任何 .py 文件，"
                     "目标项目目录可能不是预期的仓库布局"))
        result.skipped = len(samples)
        return result

    # ── 逐条校验 ──
    seen_ids: set[str] = set()
    for position, sample in enumerate(samples):
        file_name, line, category, severity, _en, _cn, is_bug = sample
        sample_id = f"{spec.dataset_id}#{position + 1}"
        result.sample_ids.append(sample_id)

        if sample_id in seen_ids:
            result.issues.append(DatasetIssue("error", "样本 ID 重复", sample_id))
        seen_ids.add(sample_id)

        if category not in ALLOWED_CATEGORIES:
            result.issues.append(DatasetIssue(
                "error", f"非法 category={category!r}（允许 {sorted(ALLOWED_CATEGORIES)}）",
                sample_id))
        if severity not in ALLOWED_SEVERITIES:
            result.issues.append(DatasetIssue(
                "error", f"非法 severity={severity!r}（允许 {sorted(ALLOWED_SEVERITIES)}）",
                sample_id))
        if not isinstance(is_bug, bool):
            result.issues.append(DatasetIssue(
                "error", f"is_real_bug 必须是布尔值，实际 {type(is_bug).__name__}", sample_id))

        resolved, reason = resolve_sample_file(target, file_name, index)
        if resolved is None:
            result.skipped += 1
            if "同名文件" in reason:
                result.ambiguous += 1
            result.issues.append(DatasetIssue(
                "error", f"标注文件无法解析: {file_name} —— {reason}", sample_id))
            continue

        # 行号必须在文件范围内（标注失效会让嵌入的代码片段指向无关位置）
        try:
            lines = resolved.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError as exc:
            result.skipped += 1
            result.issues.append(DatasetIssue("error", f"文件不可读: {exc}", sample_id))
            continue
        if not isinstance(line, int):
            result.issues.append(DatasetIssue(
                "error", f"line 必须是整数，实际 {type(line).__name__}", sample_id))
            result.skipped += 1
            continue
        if line < 1 or line > len(lines):
            if is_bug:
                # 真问题的标注点必须真实存在，否则嵌入给判分模型的上下文
                # 指向无关位置，评测结果不可信（这正是 preflight 要拦的事故）。
                result.issues.append(DatasetIssue(
                    "error", f"标注行号 {line} 超出 {file_name} 的范围（共 {len(lines)} 行）",
                    sample_id))
                result.skipped += 1
                continue
            # FP 陷阱允许越界：约定"越界行号"表示该问题在代码中根本不存在，
            # 判分模型应当回答 NO（命中则计 FP）。记为 warning 提醒复核。
            result.issues.append(DatasetIssue(
                "warning",
                f"FP 陷阱使用越界行号 {line}（{file_name} 共 {len(lines)} 行），"
                "按约定表示'问题不存在'，请确认仍是有意的 FP 样本",
                sample_id))
            result.matched += 1
            continue

        result.matched += 1

    result.issues.extend(_duplicate_position_issues(samples))
    return result


def _duplicate_position_issues(samples: list[tuple]) -> list[DatasetIssue]:
    """同一 (file, line) 有多条标注时给出 warning。

    为什么必须可见：几乎所有分析脚本都用 `{(file, line): 值}` 建索引。
    同一个位置有两条样本时，字典会**静默保留最后一条**——另一条消失，
    表现为分母变小或标签取错。这类问题不报错，只让读数偏离。

    重复本身未必是错（同一行可以有两个不同的真实问题），因此记为 warning
    而不是 error；但必须让人知道"该位置的权重是 2"，以及它会影响哪些统计。
    """
    by_key: dict[tuple[str, int], list[int]] = {}
    for position, sample in enumerate(samples, start=1):
        by_key.setdefault((str(sample[0]), int(sample[1])), []).append(position)

    issues: list[DatasetIssue] = []
    for (file_name, line), positions in sorted(by_key.items()):
        if len(positions) < 2:
            continue
        labels = {bool(samples[p - 1][6]) for p in positions}
        conflict = ("，⚠️ 标签互相矛盾（同一行既算正例又算负例）"
                    if len(labels) > 1 else "")
        issues.append(DatasetIssue(
            "warning",
            f"{file_name}:{line} 有 {len(positions)} 条标注（样本 "
            f"{positions}）—— 判分模型在该位置只会被问一次同类问题，"
            f"一次检出会计入 {len(positions)} 次{conflict}",
            f"#{positions[0]}"))
    return issues


def preflight_all(targets: dict[str, str | Path] | None = None,
                  repo_root: str | Path = ".") -> list[PreflightResult]:
    """对所有已登记数据集做预检。

    targets 按 dataset_id 覆盖目标目录；未提供且数据集无默认根时记为 error。
    """
    targets = targets or {}
    results = []
    for spec in DATASETS:
        results.append(preflight(spec, targets.get(spec.dataset_id), repo_root=repo_root))
    return results


def main(argv: list[str] | None = None) -> int:
    """命令行预检。

    python -m tests.eval.datasets                       # 校验可用的数据集
    python -m tests.eval.datasets --target ID=DIR        # 指定目标项目目录
    python -m tests.eval.datasets --require-targets      # 未提供目标目录即失败
    """
    import argparse

    parser = argparse.ArgumentParser(description="标注数据集离线预检")
    parser.add_argument("--target", action="append", default=[], metavar="DATASET_ID=DIR",
                        help="指定某个数据集的目标项目目录（可重复）")
    parser.add_argument("--require-targets", action="store_true",
                        help="未提供目标目录的数据集视为失败（用于拥有全部目标仓库的环境）")
    args = parser.parse_args(argv)

    targets: dict[str, str] = {}
    for item in args.target:
        if "=" not in item:
            parser.error(f"--target 需要 DATASET_ID=DIR 形式: {item}")
        key, value = item.split("=", 1)
        targets[key] = value

    exit_code = 0
    fixtures_only = sum(1 for s in DATASETS if s.default_target)
    for spec in DATASETS:
        result = preflight(spec, targets.get(spec.dataset_id))
        untargeted = any("未提供目标项目目录" in i.message for i in result.errors)
        print(f"[dataset] {result.summary_line()}")
        for issue in result.issues:
            if untargeted and issue.severity == "error":
                # 外部项目不在本机时不算失败，而是显式"本次未覆盖"
                continue
            marker = "ERROR" if issue.severity == "error" else "WARN "
            location = f" ({issue.sample_id})" if issue.sample_id else ""
            print(f"  {marker} {issue.message}{location}")
        if untargeted:
            print("  SKIP  未提供目标项目目录，本次未校验（--require-targets 可强制）")
            if args.require_targets:
                exit_code = 1
            continue
        if not result.passed:
            exit_code = 1
    print(f"[dataset] 已校验 {fixtures_only} 个随仓库的数据集；"
          "外部项目数据集需显式 --target 才会校验")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
