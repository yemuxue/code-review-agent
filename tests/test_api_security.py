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


def test_server_loads_dotenv_before_auth(monkeypatch, tmp_path):
    import dotenv

    monkeypatch.setattr(dotenv, "dotenv_values", lambda _: {
        "JWT_SECRET_KEY": "dotenv-only-secret-that-is-long-enough",
        "ADMIN_PASSWORD": "dotenv-only-admin-password",
    })
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("src.config", None)
    sys.modules.pop("src.harness.jwt_auth", None)
    sys.modules.pop("src.api.server", None)

    try:
        server = importlib.import_module("src.api.server")
        assert server.jwt_auth.secret_key == "dotenv-only-secret-that-is-long-enough"
    finally:
        sys.modules.pop("src.api.server", None)
        sys.modules.pop("src.harness.jwt_auth", None)
        sys.modules.pop("src.config", None)
