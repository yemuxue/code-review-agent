"""API 端点级用户隔离测试（整改清单 P1#17 测试矩阵缺口）。

DAO 级隔离已有 tests/test_database.py 与 tests/test_owner_isolation.py 覆盖，
但"端点是否真的把 owner 传下去"此前完全没有测试——这正是 Streamlit/API
绕过隔离那类缺陷能长期存活的原因。这里用 TestClient + 两个真实用户
验证跨用户读写都被拒绝，全程零网络调用（LLM 用桩替换）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture()
def server(monkeypatch, tmp_path):
    """加载一份指向临时 data 目录的 API 服务实例。"""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("CODE_REVIEW_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)

    module = importlib.import_module("src.api.server")
    module.DATA_DIR = tmp_path / "data"
    module.DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        yield module
    finally:
        sys.modules.pop("src.api.server", None)
        sys.modules.pop("src.harness.jwt_auth", None)
        sys.modules.pop("src.config", None)


@pytest.fixture()
def client(server):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    with fastapi_testclient.TestClient(server.app) as test_client:
        yield test_client


def _second_user(server, username="bob", password="bob-password-123"):
    """创建第二个普通用户，用于验证跨用户访问。"""
    store = server.user_store
    if not store.get_user(username):
        store.create_user(username, password, role="user")
    return username, password


def _login(client, username, password):
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _seed_session(server, owner, name="owner-only"):
    """直接经 DAO 造一条属于 owner 的会话与消息（绕开真实 LLM 调用）。"""
    sid = server.get_db().create_session(name=name, mode="single", owner_username=owner)
    server.get_db().add_message(sid, "user", "secret content")
    server.get_db().add_message(sid, "assistant", "secret answer")
    return sid


def test_sessions_list_only_returns_own_sessions(client, server):
    alice, alice_pw = "alice", "alice-password-123"
    server.user_store.create_user(alice, alice_pw, role="user")
    _second_user(server)

    alice_headers = _login(client, alice, alice_pw)
    bob_headers = _login(client, *_second_user(server))

    alice_sid = _seed_session(server, "alice")
    bob_sid = _seed_session(server, "bob")

    alice_ids = {s["id"] for s in client.get("/sessions", headers=alice_headers).json()}
    bob_ids = {s["id"] for s in client.get("/sessions", headers=bob_headers).json()}

    assert alice_ids == {alice_sid}
    assert bob_ids == {bob_sid}


def test_cross_user_session_detail_is_404_not_data(client, server):
    alice, alice_pw = "alice", "alice-password-123"
    server.user_store.create_user(alice, alice_pw, role="user")
    _second_user(server)

    _login(client, alice, alice_pw)
    bob_headers = _login(client, *_second_user(server))
    alice_sid = _seed_session(server, "alice")

    resp = client.get(f"/sessions/{alice_sid}", headers=bob_headers)
    assert resp.status_code == 404
    assert "secret content" not in resp.text


def test_cross_user_findings_and_keyword_search_are_empty(client, server):
    from src.storage.database import Finding

    alice, alice_pw = "alice", "alice-password-123"
    server.user_store.create_user(alice, alice_pw, role="user")
    _second_user(server)

    alice_headers = _login(client, alice, alice_pw)
    bob_headers = _login(client, *_second_user(server))

    sid = _seed_session(server, "alice")
    server.get_db().add_finding(sid, Finding(
        session_id=sid, file_path="a.py", line=1, category="SECURITY", severity="High",
        description_en="alice sql injection", description_cn="注入", owner_username="alice"))

    assert len(client.get("/findings", headers=alice_headers).json()) == 1
    assert client.get("/findings", headers=bob_headers).json() == []
    assert client.get("/findings/keyword?q=injection", headers=bob_headers).json() == []


def test_cross_user_vector_search_returns_nothing(client, server):
    from src.memory.vector_store import FindingDocument

    alice, alice_pw = "alice", "alice-password-123"
    server.user_store.create_user(alice, alice_pw, role="user")
    _second_user(server)

    alice_headers = _login(client, alice, alice_pw)
    bob_headers = _login(client, *_second_user(server))

    sid = _seed_session(server, "alice")
    server.get_vector_store().add_finding(FindingDocument(
        id="doc-alice", file_path="a.py", description_en="alice sql injection",
        description_cn="注入", session_id=sid), owner_username="alice")

    body = {"query": "injection", "n_results": 5}
    alice_hits = client.post("/findings/search", json=body, headers=alice_headers).json()
    bob_hits = client.post("/findings/search", json=body, headers=bob_headers).json()

    assert [r["id"] for r in alice_hits["vector"]] == ["doc-alice"]
    assert bob_hits["vector"] == []


def test_health_is_public_but_does_not_leak_business_stats(client, server):
    sid = _seed_session(server, "someone")

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "healthy"
    assert body["database_ready"] is True
    # 公开端点不得返回会话数/发现数/分布等业务统计
    assert "stats" not in body
    assert "vector_store_count" not in body
    assert sid not in resp.text


def test_protected_endpoints_require_authentication(client, server):
    for method, path in (("get", "/sessions"), ("get", "/findings"), ("get", "/stats"),
                         ("get", "/metrics"), ("get", "/dashboard")):
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{path} should require auth"


def test_stats_requires_admin_role(client, server):
    alice, alice_pw = "alice", "alice-password-123"
    server.user_store.create_user(alice, alice_pw, role="user")
    headers = _login(client, alice, alice_pw)

    assert client.get("/stats", headers=headers).status_code == 403


def test_admin_stats_reachable_for_admin(client, server):
    headers = _login(client, "admin", "test-admin-password")
    resp = client.get("/stats", headers=headers)

    assert resp.status_code == 200
    assert "database" in resp.json()
