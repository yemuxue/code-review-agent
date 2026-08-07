"""修复管线安全加固单元测试

覆盖三条防线（对应一次真实事故：fix 截断 jwt_auth.py 到 38% 未被发现）：
  1. write_file 截断守卫 — 整文件覆盖时新内容 < 原文件一半 → REFUSED，零副作用
  2. restore_from_backup — 损坏文件从 .bak 自动恢复
  3. verify_file_integrity — 语法 + 尺寸比例 + 关键符号三重检测

零 API 调用，全部使用 tmp_path 临时文件。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.tools.git_tools import write_file, restore_from_backup, verify_file_integrity

ORIGINAL = """def foo():
    return 1


def bar():
    return 2


def baz():
    return 3
"""


# ═══════ 1. write_file 截断守卫 ═══════

def test_write_file_full_rewrite_ok(tmp_path):
    p = tmp_path / "a.py"
    p.write_text(ORIGINAL, encoding="utf-8")
    new = "# comment\n" + ORIGINAL
    r = write_file(str(p), new, start_line=1)
    assert r.startswith("OK:"), r
    assert p.read_text(encoding="utf-8") == new
    assert p.with_suffix(".py.bak").exists()  # 写前备份已生成


def test_write_file_refuses_truncated_full_write(tmp_path):
    """< 50% 的整文件覆盖 → REFUSED 且零副作用（原文件、无备份、无 tmp）"""
    p = tmp_path / "a.py"
    p.write_text(ORIGINAL, encoding="utf-8")
    truncated = "def foo():\n    return 1\n"  # 约原文件 1/4
    r = write_file(str(p), truncated, start_line=1)
    assert "REFUSED" in r, r
    assert p.read_text(encoding="utf-8") == ORIGINAL        # 原文件未被修改
    assert not p.with_suffix(".py.bak").exists()            # 未生成备份
    assert not p.with_suffix(".py.tmp").exists()            # 未写临时文件


def test_write_file_partial_edit_allowed(tmp_path):
    """start_line > 1 的部分编辑：内容足够长（合并后 >= 原文件一半）→ 允许"""
    p = tmp_path / "a.py"
    p.write_text(ORIGINAL, encoding="utf-8")
    # 替换第 2 行起的全部内容，但保留足够长度
    body = "    return 100\n\n\n" + "def bar():\n    return 2\n\n\n" + "def baz():\n    return 3\n"
    r = write_file(str(p), body, start_line=2)
    assert r.startswith("OK:"), r
    assert "return 100" in p.read_text(encoding="utf-8")
    assert "def bar" in p.read_text(encoding="utf-8")


def test_write_file_refuses_truncated_partial_edit(tmp_path):
    """部分编辑合并后仍不足原文件一半 → 同样拒绝（截断路径之二）"""
    p = tmp_path / "a.py"
    p.write_text(ORIGINAL, encoding="utf-8")
    r = write_file(str(p), "    return 100\n", start_line=2)  # 只保留第 1 行 + 1 行新内容
    assert "REFUSED" in r, r
    assert p.read_text(encoding="utf-8") == ORIGINAL  # 原文件未被修改


def test_write_file_new_file_no_guard(tmp_path):
    """新建文件没有原文件可对比，不触发守卫"""
    p = tmp_path / "new.py"
    r = write_file(str(p), "x = 1\n", start_line=1)
    assert r.startswith("OK:"), r
    assert not p.with_suffix(".py.bak").exists()  # 新文件无备份


# ═══════ 2. restore_from_backup ═══════

def test_restore_from_backup(tmp_path):
    p = tmp_path / "a.py"
    p.write_text(ORIGINAL, encoding="utf-8")
    write_file(str(p), "# modified\n" + ORIGINAL)  # 触发首次备份
    p.write_text("broken partial content", encoding="utf-8")  # 模拟修复损坏
    r = restore_from_backup(str(p))
    assert r.startswith("OK:"), r
    assert p.read_text(encoding="utf-8") == ORIGINAL  # 恢复为写前版本


def test_restore_without_backup(tmp_path):
    p = tmp_path / "a.py"
    p.write_text("x = 1", encoding="utf-8")
    r = restore_from_backup(str(p))
    assert r.startswith("ERROR: no backup"), r


# ═══════ 3. verify_file_integrity ═══════

def _make_with_backup(tmp_path, content: str) -> Path:
    """写入文件 + 通过 write_file 生成 .bak 备份"""
    p = tmp_path / "a.py"
    p.write_text(ORIGINAL, encoding="utf-8")
    write_file(str(p), "# hdr\n" + ORIGINAL)  # 生成备份（内容完整）
    p.write_text(content, encoding="utf-8")   # 模拟修复后的状态
    return p


def test_verify_integrity_clean(tmp_path):
    p = _make_with_backup(tmp_path, "# hdr\n" + ORIGINAL)
    assert verify_file_integrity(str(p)) == []


def test_verify_integrity_detects_truncation(tmp_path):
    """尺寸骤减（截断后语法仍合法）→ 必须被检测到"""
    p = _make_with_backup(tmp_path, "def foo():\n    return 1\n")  # 语法合法但太小
    issues = verify_file_integrity(str(p))
    assert any("truncated" in i or "missing" in i for i in issues), issues


def test_verify_integrity_detects_syntax_error(tmp_path):
    p = _make_with_backup(tmp_path, "def foo(:\n")  # 语法错误
    issues = verify_file_integrity(str(p))
    assert any("syntax" in i for i in issues), issues


def test_verify_integrity_detects_missing_symbols(tmp_path):
    """尺寸没骤减但关键符号被删 → 语义截断检测"""
    content = "# hdr\n" + ORIGINAL + "\n# extra padding to keep size reasonable\n" * 10
    p = _make_with_backup(tmp_path, content.replace("def bar", "def deleted_bar", 1))
    issues = verify_file_integrity(str(p))
    assert any("symbols" in i for i in issues), issues


def test_verify_integrity_missing_file(tmp_path):
    p = tmp_path / "gone.py"
    assert verify_file_integrity(str(p)) != []
