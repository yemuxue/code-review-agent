"""API 密钥来源的回归测试。

背景：密钥此前只能明文写在仓库目录内的 .env 里——一旦构建上下文或提交
配置出错就会泄漏。`ANTHROPIC_AUTH_TOKEN_FILE` 允许把密钥放在 .env 之外的
独立文件（可设更严格权限），发布物里不再需要出现明文。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.config as config  # noqa: E402
from src.config import ConfigError, get_api_key  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离环境：这些测试只关心"从哪读密钥"。"""
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                 "ANTHROPIC_AUTH_TOKEN_FILE", "ANTHROPIC_API_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)


def test_direct_env_var_wins(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-direct")
    assert get_api_key() == "sk-direct"


def test_api_key_fallback_name(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-alt")
    assert get_api_key() == "sk-alt"


def test_file_is_used_when_direct_var_absent(monkeypatch, tmp_path):
    secret = tmp_path / "api.key"
    secret.write_text("sk-from-file\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN_FILE", str(secret))

    assert get_api_key() == "sk-from-file"      # 末尾换行必须被去掉


def test_direct_var_takes_precedence_over_file(monkeypatch, tmp_path):
    secret = tmp_path / "api.key"
    secret.write_text("sk-from-file", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN_FILE", str(secret))
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-direct")

    assert get_api_key() == "sk-direct"


def test_unreadable_file_raises_config_error_not_silent(monkeypatch, tmp_path):
    """文件不可读时必须给出配置错误，而不是悄悄当作"没配密钥"。"""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN_FILE", str(tmp_path / "missing.key"))

    with pytest.raises(ConfigError, match="API key is missing"):
        get_api_key()


def test_missing_everything_mentions_the_file_option(monkeypatch):
    """报错信息要告诉运维有"不进 .env"的选项。"""
    with pytest.raises(ConfigError) as exc:
        get_api_key()

    assert "ANTHROPIC_AUTH_TOKEN_FILE" in str(exc.value)


def test_unreadable_file_logs_a_warning(monkeypatch, tmp_path, caplog):
    """不静默：文件读了但失败时要有日志，否则表现为"认证莫名失败"。"""
    import logging

    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN_FILE", str(tmp_path / "nope.key"))
    with caplog.at_level(logging.WARNING, logger="src.config"):
        with pytest.raises(ConfigError):
            get_api_key()

    assert any("unreadable" in rec.message for rec in caplog.records)


def test_secret_file_helper_returns_empty_for_blank(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN_FILE", "   ")
    assert config._read_secret_file("ANTHROPIC_AUTH_TOKEN_FILE") == ""
