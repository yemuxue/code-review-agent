import importlib
import sys
from pathlib import Path


def _load_server(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("CODE_REVIEW_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("src.api.server", None)
    return importlib.import_module("src.api.server")


def test_api_tools_exclude_host_command_execution(monkeypatch, tmp_path):
    server = _load_server(monkeypatch, tmp_path)

    assert "run_command" not in {tool.name for tool in server.TOOLS}
