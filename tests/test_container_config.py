"""交付面配置守卫的单元测试。

`scripts/check_container_config.py` 断言的是"密钥不进镜像"这类无法靠代码
逻辑保证、只能靠配置保证的性质。这里验证脚本本身真的会拦住违规配置，
否则它会变成一个永远返回 0 的假门禁。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_container_config as checker  # noqa: E402


def test_current_repo_container_config_is_valid():
    assert checker.check_dockerignore() == []
    assert checker.check_compose() == []


def test_dockerignore_missing_returns_problem(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    problems = checker.check_dockerignore()

    assert problems and ".dockerignore" in problems[0]


def test_dockerignore_without_env_exclusion_is_rejected(tmp_path, monkeypatch):
    """只排除 data/ 但漏掉 .env 时必须被拦下（密钥会进镜像）。"""
    (tmp_path / ".dockerignore").write_text("data/\nlogs/\nsessions/\n.git/\n",
                                            encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    problems = checker.check_dockerignore()

    assert any(".env" in problem for problem in problems)


def test_dockerignore_without_secrets_dir_is_rejected(tmp_path, monkeypatch):
    """密钥文件目录也必须排除：.env 现在只引用 .secrets/ 下的明文。

    这条断言把"密钥不进镜像"从一句约定变成机器可验证的边界。
    """
    (tmp_path / ".dockerignore").write_text(
        ".env\ndata/\nlogs/\nsessions/\n.git/\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    problems = checker.check_dockerignore()

    assert any(".secrets" in problem for problem in problems)


def test_current_repo_excludes_secrets_directory():
    """真实仓库必须真的排除 .secrets/（不能只在测试里通过）。"""
    from pathlib import Path as _Path

    entries = {line.strip() for line in
               _Path(".dockerignore").read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.strip().startswith("#")}

    assert ".secrets/" in entries or ".secrets" in entries


def test_compose_without_healthcheck_is_rejected(tmp_path, monkeypatch):
    (tmp_path / ".dockerignore").write_text(
        ".env\ndata/\nlogs/\nsessions/\n.git/\n", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  api:\n"
        "    image: x\n"
        "    restart: unless-stopped\n"
        "  streamlit:\n"
        "    image: x\n"
        "    restart: unless-stopped\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    problems = checker.check_compose()

    assert len(problems) == 2
    assert all("healthcheck" in problem for problem in problems)


def test_compose_missing_service_is_rejected(tmp_path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  api:\n"
        "    image: x\n"
        "    restart: unless-stopped\n"
        "    healthcheck:\n"
        "      test: [\"CMD\", \"true\"]\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    problems = checker.check_compose()

    assert any("streamlit" in problem for problem in problems)


def test_main_returns_nonzero_on_problems(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert checker.main() == 1
