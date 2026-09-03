"""src/skills selector 测试：纯匹配语义与 prompt 段拼装（无文件系统）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.skills.loader import VALID_ROLES, Skill
from src.skills.selector import (
    INDEX_HEADER,
    SECTION_SEP,
    SKILL_HEADER_TEMPLATE,
    build_role_blocks,
    compose_role_block,
    matches_skill,
)


def _skill(**overrides) -> Skill:
    fields = dict(
        name="skill-x",
        description="desc line one",
        roles=(),
        triggers=(),
        path=Path("/skills/skill-x"),
        body="body line 1\nbody line 2",
    )
    fields.update(overrides)
    return Skill(**fields)


def test_valid_roles_synced_with_agent_definitions():
    """VALID_ROLES 必须与 AGENT_DEFINITIONS 的键保持同步。"""
    from src.multi_agent.agents import AGENT_DEFINITIONS

    assert set(VALID_ROLES) == set(AGENT_DEFINITIONS)


def test_empty_roles_matches_any_role():
    skill = _skill(roles=())
    for role in VALID_ROLES:
        assert matches_skill(skill, role, "anything")


def test_role_gate_restricts():
    skill = _skill(roles=("executor",))
    assert matches_skill(skill, "executor", "task")
    assert not matches_skill(skill, "reviewer", "task")
    assert not matches_skill(skill, "planner", "task")


def test_empty_triggers_matches_any_text():
    skill = _skill(roles=("reviewer",), triggers=())
    assert matches_skill(skill, "reviewer", "any text at all")


def test_trigger_match_is_case_insensitive_and_cjk_safe():
    skill = _skill(triggers=("sql injection",))
    assert matches_skill(skill, "planner", "Check Sql Injection in login")
    cn = _skill(triggers=("编码",))
    assert matches_skill(cn, "planner", "修复 编码 问题")
    assert not matches_skill(cn, "planner", "修复 解码 问题")


def test_multi_trigger_any_semantics():
    skill = _skill(triggers=("perf", "performance", "性能"))
    assert matches_skill(skill, "planner", "audit performance")
    assert matches_skill(skill, "planner", "性能 优化")
    assert not matches_skill(skill, "planner", "readability cleanup")


def test_and_semantics_between_gates():
    skill = _skill(roles=("executor",), triggers=("secret",))
    assert matches_skill(skill, "executor", "find hardcoded secrets")
    assert not matches_skill(skill, "executor", "refactor naming")  # trigger 缺
    assert not matches_skill(skill, "reviewer", "hardcoded secret")  # 角色不符


def test_substring_semantics():
    skill = _skill(triggers=("secret",))
    assert matches_skill(skill, "planner", "read hardcoded SECRETS.txt")


def test_compose_empty_when_no_applicable_skill():
    skill = _skill(roles=("fixer",))
    assert compose_role_block([skill], "planner", "secret leak") == ""


def test_index_is_role_filtered():
    fixer_only = _skill(name="fix-enc", roles=("fixer",), description="fix encoding")
    planner_block = compose_role_block([fixer_only], "planner", "encoding issue")
    fixer_block = compose_role_block([fixer_only], "fixer", "encoding issue")
    assert planner_block == ""
    assert INDEX_HEADER in fixer_block
    assert "- fix-enc: fix encoding" in fixer_block
    assert compose_role_block([fixer_only], "executor", "encoding issue") == ""


def test_full_block_format_stability():
    s1 = _skill(name="sec-review", description="security rules",
                roles=("executor", "reviewer"), triggers=("injection",),
                body="No raw SQL\n用参数化查询")
    s2 = _skill(name="naming", description="rename vars only",
                roles=("executor",), triggers=("rename",),
                body="Use snake_case")

    block = compose_role_block([s1, s2], "executor", "check sql injection")
    assert block.startswith(SECTION_SEP + INDEX_HEADER)
    # 索引列全部角色适用技能（含未命中的）
    assert "- sec-review: security rules" in block
    assert "- naming: rename vars only" in block
    # 命中技能：正文整体出现
    assert SKILL_HEADER_TEMPLATE.format(name="sec-review") in block
    assert "No raw SQL\n用参数化查询" in block
    # 未命中技能：索引在场但正文缺席
    assert SKILL_HEADER_TEMPLATE.format(name="naming") not in block
    assert "snake_case" not in block
    # 角色门：fixer 视角两者都不可用 → 空
    assert compose_role_block([s1, s2], "fixer", "check sql injection") == ""


def test_build_role_blocks_keys_and_empty():
    assert build_role_blocks([], "anything") == {}
    blocks = build_role_blocks([_skill(roles=("fixer",))], "anything")
    assert set(blocks) == set(VALID_ROLES)
    assert blocks["planner"] == ""  # 无可用技能的角色是空串而非缺 key
    assert blocks["fixer"] != ""


def test_description_normalized_but_body_multiline_intact():
    # description 换行会由 loader 归约，但这里直接构造时也按首行渲染
    skill = _skill(description="multi\nline", body="深\n度\n正文")
    bullets = compose_role_block([skill], "planner", "x").split("\n")
    assert any(b == "- skill-x: multi" for b in bullets)
    assert "深\n度\n正文" in compose_role_block([skill], "planner", "x")


def test_unknown_role_gets_empty_block():
    skill = _skill(roles=("fixer",))
    assert compose_role_block([skill], "verifier", "fix encoding") == ""
