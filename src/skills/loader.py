"""Agent Skills 加载器 — 读取 <skills_dir>/<name>/SKILL.md 并解析 frontmatter
Loader for SKILL.md skill packages.

每个技能 = 一个目录下的 SKILL.md，frontmatter 支持:
    name:        必填，技能名（索引与正文小标题）
    description: 必填，一句话说明（索引中一行一条）
    roles:       可选，适用角色列表；空 = 所有角色可用
    triggers:    可选，触发关键词列表；空 = 不受任务关键词门控
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# 合法角色 = src/multi_agent/agents.py 中 AGENT_DEFINITIONS 的键。
# 刻意不 import multi_agent.agents，使本包保持零项目依赖；
# 由 tests/test_skills_selector.py 静态断言与 AGENT_DEFINITIONS 保持同步。
VALID_ROLES: tuple[str, ...] = ("planner", "executor", "reviewer", "fixer")

_FRONTMATTER_MARKER = "---"


@dataclass(frozen=True)
class Skill:
    """A single loaded skill: frontmatter metadata + body (SKILL.md minus frontmatter)."""

    name: str                      # 必填；用于索引与正文小标题
    description: str               # 必填；解析时归约为第一行（索引一行一条）
    roles: tuple[str, ...]         # 空 = 所有角色可用
    triggers: tuple[str, ...]      # 空 = 不受任务关键词门控
    path: Path                     # 技能目录（v1 只读，不读 sidecar 文件）
    body: str                      # frontmatter 之后的正文（首尾 strip）


def default_skills_dir() -> Path:
    """默认技能目录 = 本仓库根目录 /skills（loader.py → parents[2] = repo root）。"""
    return Path(__file__).resolve().parents[2] / "skills"


def _strip_quotes(value: str) -> str:
    """Remove a single pair of surrounding quotes and unescape them."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        quote = value[0]
        value = value[1:-1].replace("\\" + quote, quote)
    return value.strip()


def _parse_list_value(value: str) -> tuple[str, ...]:
    """解析单行列表：`[a, b]` / `a, b` / `["a"]` 均支持，逐元素去引号去空。

    引号内含逗号不受支持（与解析器"宽容但简单"的定位一致）。
    """
    value = value.strip()
    if value.startswith("["):
        value = value[1:]
    if value.endswith("]"):
        value = value[:-1]
    tokens = []
    for token in value.split(","):
        token = _strip_quotes(token)
        if token:
            tokens.append(token)
    return tuple(tokens)


def parse_skill_file(skill_md: Path) -> Optional[Skill]:
    """解析单个 SKILL.md；损坏/缺 frontmatter/缺 name 或 description/非法角色 → None（跳过）。"""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _LOGGER.warning("Skipping skill file %s: cannot read (%s)", skill_md, exc)
        return None

    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_MARKER:
        return None

    fields: dict[str, str] = {}
    close_index: int = -1
    for i, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == _FRONTMATTER_MARKER:
            close_index = i
            break
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()  # 重复 key：后值覆盖

    if close_index == -1:
        return None

    name = _strip_quotes(fields.get("name", ""))
    if not name:
        return None
    description = _strip_quotes(fields.get("description", ""))
    if not description:
        return None
    description = next((ln for ln in description.splitlines() if ln.strip()), description)

    roles = _parse_list_value(fields.get("roles", ""))
    invalid = [r for r in roles if r not in VALID_ROLES]
    if invalid:
        _LOGGER.warning(
            "Skipping skill %r in %s: unknown roles %s (valid: %s)",
            name, skill_md, invalid, ", ".join(VALID_ROLES),
        )
        return None
    triggers = _parse_list_value(fields.get("triggers", ""))

    body = "\n".join(lines[close_index + 1:]).strip()
    return Skill(
        name=name,
        description=description,
        roles=roles,
        triggers=triggers,
        path=skill_md.parent.resolve(strict=False),
        body=body,
    )


def load_skills(skills_dir: str | Path | None = None) -> list[Skill]:
    """扫描 <skills_dir>/*/SKILL.md 返回按目录名排序的技能。

    - skills_dir=None → 仓库根默认 skills/ 目录
    - 目录缺失/指向文件/读取失败 → []（warn，不抛）
    - 损坏条目与重名技能（按排序目录 first-wins）→ 跳过并 warn
    - 每个技能目录做 resolve+relative_to 包含性检查，防 symlink 逃逸
    """
    root = Path(skills_dir).resolve(strict=False) if skills_dir is not None else default_skills_dir()
    if not root.is_dir():
        return []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        _LOGGER.warning("Cannot scan skills dir %s: %s", root, exc)
        return []

    skills: list[Skill] = []
    seen: set[str] = set()
    for entry in entries:
        if not entry.is_dir():
            continue
        candidate = entry.resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            _LOGGER.warning("Skipping skill dir escaping root: %s", entry)
            continue
        skill_md = candidate / "SKILL.md"
        if not skill_md.is_file():
            continue
        skill = parse_skill_file(skill_md)
        if skill is None:
            continue
        if skill.name in seen:
            _LOGGER.warning("Skipping duplicate skill %r in %s (first wins)", skill.name, candidate)
            continue
        seen.add(skill.name)
        skills.append(skill)
    return skills
