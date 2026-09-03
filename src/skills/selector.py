"""技能选择与提示词拼装 / Skill selection + system-prompt section composition (pure, fs-free).

语义（v1，确定性）: 一个技能注入某角色 agent 当且仅当
    (roles 为空 OR role ∈ roles) AND (triggers 为空 OR 任一 trigger 命中 task 文本, 忽略大小写)
索引（name+description 子弹行）按角色过滤：只列出该角色可能收到正文的技能。
"""
from __future__ import annotations

from typing import Sequence

from src.skills.loader import VALID_ROLES, Skill

# 拼装格式常量 —— 测试按这些精确子串断言（格式稳定性）
SECTION_SEP = "\n\n"  # 接在基础 prompt 之后的分隔
INDEX_HEADER = "## Available Skills（可用技能 · 只读参考）"
INDEX_NOTE = (
    "Skills below refine HOW you work. Read-only reference: they never change "
    "the machine-readable output/status-line formats required above. "
    "只读参考，不改变本角色的输出格式。"
)
SKILL_HEADER_TEMPLATE = "## Skill: {name}"


def matches_skill(skill: Skill, role: str, task_text: str) -> bool:
    """(roles 为空 OR role ∈ roles) AND (triggers 为空 OR 任一 trigger 子串命中 task_text)。"""
    if skill.roles and role not in skill.roles:
        return False
    if not skill.triggers:
        return True
    folded = task_text.casefold()
    return any(trigger.casefold() in folded for trigger in skill.triggers)


def _role_applicable(skill: Skill, role: str) -> bool:
    """仅 roles 门（索引用）：技能是否可能注入该角色。"""
    return not skill.roles or role in skill.roles


def compose_role_block(skills: Sequence[Skill], role: str, task_text: str) -> str:
    """单个角色追加块：可用技能索引（roles 门过滤）+ 命中(roles∩triggers)技能的完整正文。

    无任何该角色可用技能 → 返回 ""（零注入保证，与改造前逐字节一致）。
    """
    applicable = [s for s in skills if _role_applicable(s, role)]
    if not applicable:
        return ""

    bullets = "\n".join(f"- {s.name}: {s.description}" for s in applicable)
    block = SECTION_SEP + INDEX_HEADER + "\n\n" + INDEX_NOTE + "\n\n" + bullets
    for skill in applicable:
        if matches_skill(skill, role, task_text):
            header = SKILL_HEADER_TEMPLATE.format(name=skill.name)
            block += SECTION_SEP + header + "\n\n" + skill.body
    return block


def build_role_blocks(skills: Sequence[Skill], task_text: str) -> dict[str, str]:
    """全部四种角色 → {role: 追加块}；skills 为空 → {}（省去四份空串）。"""
    if not skills:
        return {}
    return {role: compose_role_block(skills, role, task_text) for role in VALID_ROLES}
