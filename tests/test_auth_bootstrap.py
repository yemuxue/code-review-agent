import pytest

from src.harness.jwt_auth import UserStore


def test_first_start_requires_explicit_admin_password(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        UserStore(str(tmp_path))


def test_first_start_creates_admin_from_configured_password(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_PASSWORD", "configured-password")

    store = UserStore(str(tmp_path))

    assert store.verify_password("admin", "configured-password") is not None
