"""src/skills loader 测试：目录扫描 / frontmatter 解析 / 容错。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.skills.loader import Skill, default_skills_dir, load_skills, parse_skill_file


def _write_skill(tmp_path: Path, dir_name: str, content: str) -> Path:
    """在 tmp_path/<dir_name>/SKILL.md 写入技能文件，返回 SKILL.md 路径。"""
    skill_dir = tmp_path / dir_name
    skill_dir.mkdir(parents=True)
    md = skill_dir / "SKILL.md"
    md.write_text(content, encoding="utf-8")
    return md


SAMPLE_SKILL = """---
name: demo-skill
description: A demo skill for tests
roles: [executor, reviewer]
triggers: [sql, injection, 注入]
---

rule one
rule two
"""


def test_missing_dir_returns_empty(tmp_path):
    assert load_skills(tmp_path / "nope") == []


def test_skills_dir_pointing_at_file_returns_empty(tmp_path):
    regular_file = tmp_path / "not_a_dir"
    regular_file.write_text("x", encoding="utf-8")
    assert load_skills(regular_file) == []


def test_empty_dir_or_stray_files_returns_empty(tmp_path):
    assert load_skills(tmp_path) == []
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    assert load_skills(tmp_path) == []


def test_load_basic_skill_fields(tmp_path):
    _write_skill(tmp_path, "demo", SAMPLE_SKILL)
    skills = load_skills(tmp_path)
    assert len(skills) == 1
    skill = skills[0]
    assert isinstance(skill, Skill)
    assert skill.name == "demo-skill"
    assert skill.description == "A demo skill for tests"
    assert skill.roles == ("executor", "reviewer")
    assert skill.triggers == ("sql", "injection", "注入")
    assert skill.path == (tmp_path / "demo").resolve()
    assert skill.body == "rule one\nrule two"
    assert "---" not in skill.body


def test_dirs_sorted_deterministic_order(tmp_path):
    _write_skill(tmp_path, "bbb", SAMPLE_SKILL.replace("demo-skill", "skill-b"))
    _write_skill(tmp_path, "aaa", SAMPLE_SKILL.replace("demo-skill", "skill-a"))
    skills = load_skills(tmp_path)
    assert [s.name for s in skills] == ["skill-a", "skill-b"]


def test_missing_frontmatter_markers_skipped(tmp_path):
    md = _write_skill(tmp_path, "no-open", "name: x\ndescription: y\n---\nbody")
    assert parse_skill_file(md) is None
    md = _write_skill(tmp_path, "no-close", "---\nname: x\ndescription: y\nbody")
    assert parse_skill_file(md) is None
    assert load_skills(tmp_path) == []


def test_one_bad_skill_does_not_break_others(tmp_path):
    _write_skill(tmp_path, "good", SAMPLE_SKILL)
    _write_skill(tmp_path, "bad", "no frontmatter at all\n")
    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "demo-skill"


def test_missing_name_or_description_skipped(tmp_path):
    md = _write_skill(tmp_path, "no-name", "---\ndescription: x\n---\nbody")
    assert parse_skill_file(md) is None
    md = _write_skill(tmp_path, "no-desc", "---\nname: x\n---\nbody")
    assert parse_skill_file(md) is None


def test_invalid_role_skips_whole_entry_unknown_key_ignored(tmp_path):
    md = _write_skill(
        tmp_path, "bad-role",
        "---\nname: x\ndescription: y\nroles: [executor, reviewerx]\nseverity: high\n---\nbody",
    )
    assert parse_skill_file(md) is None
    md = _write_skill(
        tmp_path, "unknown-key",
        "---\nname: x\ndescription: y\nseverity: high\n---\nbody",
    )
    skill = parse_skill_file(md)
    assert skill is not None and skill.name == "x"


def test_list_syntax_variants_equivalent(tmp_path):
    variants = [
        "roles: [executor, reviewer]",
        "roles: executor, reviewer",
        'roles: ["executor", "reviewer"]',
    ]
    parsed = []
    for i, line in enumerate(variants):
        md = _write_skill(tmp_path, f"v{i}", f"---\nname: v{i}\ndescription: d\n{line}\n---\nb")
        parsed.append(parse_skill_file(md))
    assert [s.roles for s in parsed] == [("executor", "reviewer")] * 3


def test_quoted_scalar_with_escaped_quote(tmp_path):
    md = _write_skill(
        tmp_path, "q",
        '---\nname: q\nroles: [executor]\ndescription: "say \\"hi\\" first"\n---\nb',
    )
    skill = parse_skill_file(md)
    assert skill is not None
    assert skill.description == 'say "hi" first'


def test_empty_triggers_field(tmp_path):
    md = _write_skill(tmp_path, "t", "---\nname: t\ndescription: d\ntriggers:\n---\nb")
    skill = parse_skill_file(md)
    assert skill is not None and skill.triggers == ()


def test_cjk_roundtrip_and_invalid_utf8_bytes(tmp_path):
    md = _write_skill(
        tmp_path, "cjk",
        "---\nname: 中文技能\ndescription: 中文说明：含冒号内容\nroles: [fixer]\n"
        "triggers: [乱码, 编码]\n---\n修复时必须保持 UTF-8\n第二行",
    )
    skill = parse_skill_file(md)
    assert skill is not None
    assert skill.description == "中文说明：含冒号内容"  # partition 冒号不截断值
    assert skill.body == "修复时必须保持 UTF-8\n第二行"

    bad = tmp_path / "bad" / "SKILL.md"
    bad.parent.mkdir()
    bad.write_bytes(b"---\nname: odd\ndescription: d\n---\nbody \xff\xfe ok")
    skill = parse_skill_file(bad)  # errors="replace"，不抛异常
    assert skill is not None
    assert "body" in skill.body


def test_duplicate_name_first_wins_in_sorted_dir_order(tmp_path):
    _write_skill(tmp_path, "aaa", SAMPLE_SKILL)  # demo-skill in aaa
    _write_skill(tmp_path, "zzz", SAMPLE_SKILL)  # demo-skill in zzz
    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].path == (tmp_path / "aaa").resolve()


def test_duplicate_frontmatter_key_last_wins(tmp_path):
    md = _write_skill(
        tmp_path, "dup",
        "---\nname: first\nroles: [executor]\ndescription: d\nname: second\n---\nb",
    )
    skill = parse_skill_file(md)
    assert skill is not None and skill.name == "second"


def test_symlinked_dir_escaping_root_skipped(tmp_path):
    if sys.platform == "win32":
        pytest.skip("Windows 默认无 symlink 特权")
    # 真实技能目录位于扫描根之外
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")
    # 扫描根内放置指向根外目录的 symlink
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "evil").symlink_to(outside, target_is_directory=True)

    # 条目解析后离开扫描根 → 整条跳过，不越权读取
    skills = load_skills(skills_dir)
    assert skills == []
    # 真实目录作为根的直接子目录（非 symlink）仍可正常加载
    assert [s.name for s in load_skills(tmp_path)] == ["demo-skill"]


def test_default_skills_dir_is_repo_root_skills():
    expected = Path(__file__).resolve().parents[1] / "skills"
    assert default_skills_dir() == expected
