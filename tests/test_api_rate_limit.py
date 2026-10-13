"""API 限流与配置校验的回归测试。

背景（企业级审计发现）：`Limiter(default_limits=...)` 只挂在 `app.state.limiter`、
**没有挂 SlowAPIMiddleware**，而 slowapi 的 `default_limits` 只在中间件路径上生效。
结果是只有被 `@limiter.limit` 装饰的 `/auth/login`、`/auth/refresh` 受限，
`/analyze`（分钟级、消耗真实 token）、`/findings/search`、`/dashboard`
完全不受限流保护——这类"配置了但没生效"的缺陷没有测试就无法发现。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _load_server(monkeypatch, tmp_path, *, monkey_setup=None):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("CODE_REVIEW_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)
    server = importlib.import_module("src.api.server")
    server.DATA_DIR = tmp_path / "data"
    server.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if monkey_setup:
        monkey_setup(server)
    return server


@pytest.fixture()
def server(monkeypatch, tmp_path):
    module = _load_server(monkeypatch, tmp_path)
    try:
        yield module
    finally:
        for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
            sys.modules.pop(mod, None)


@pytest.fixture()
def client(server):
    testclient = pytest.importorskip("fastapi.testclient")
    with testclient.TestClient(server.app) as c:
        yield c


def test_slowapi_middleware_is_registered(server):
    """中间件必须真的挂上：否则 default_limits 形同虚设。"""
    from slowapi.middleware import SlowAPIMiddleware

    registered = [getattr(m, "cls", None) for m in server.app.user_middleware]
    assert SlowAPIMiddleware in registered, (
        "SlowAPIMiddleware 未注册 → 未被 @limiter.limit 装饰的路由不受 default_limits 约束")


def test_undecorated_route_actually_returns_429(monkeypatch, tmp_path):
    """把默认限流收紧到 2/minute，未装饰的 /health 第 3 次必须 429。

    这是对"限流是否生效"的行为级断言——只检查配置存在是不够的。
    必须同时清空路由级限制：slowapi 在装饰时就把 limit 绑定到路由上，
    只改 default_limits 不会影响已注册的 /auth/login 等端点。
    """
    def tighten(server):
        from slowapi import Limiter as _Limiter
        server.app.state.limiter = _Limiter(key_func=server._rate_limit_key,
                                            default_limits=["2/minute"])
        server.app.state.limiter._route_limits = {}
        server.limiter = server.app.state.limiter

    server = _load_server(monkeypatch, tmp_path, monkey_setup=tighten)
    testclient = pytest.importorskip("fastapi.testclient")
    with testclient.TestClient(server.app) as c:
        codes = [c.get("/health").status_code for _ in range(4)]

    assert 429 in codes, f"限流未生效，全部状态码: {codes}"
    assert codes[0] == 200


def test_rate_limit_key_prefers_authenticated_username(server):
    """限流键优先用户名：反代后所有用户共用一个 IP 桶会导致互相限流。"""
    from fastapi import Request

    token = server.jwt_auth.create_tokens(
        server.User(username="alice", role="user"))["access_token"]

    scope = {
        "type": "http", "method": "GET", "path": "/health", "headers": [
            (b"authorization", f"Bearer {token}".encode()),
        ],
    }
    request = Request(scope)
    assert server._rate_limit_key(request) == "user:alice"


def test_rate_limit_key_falls_back_to_ip_without_valid_token(server):
    from fastapi import Request

    scope = {"type": "http", "method": "GET", "path": "/health",
             "headers": [(b"authorization", b"Bearer not-a-real-token")],
             "client": ("10.0.0.9", 1234)}
    request = Request(scope)

    key = server._rate_limit_key(request)
    assert key.startswith("ip:")


def test_login_is_limited_by_default_configuration(client):
    """默认配置下登录连续失败必须出现 429（10/minute）。

    登录端点是唯一被显式装饰限流的地方，也是"限流是否真的生效"的最小验证点。
    """
    codes = [client.post("/auth/login",
                         json={"username": "admin", "password": "wrong"}).status_code
             for _ in range(12)]

    assert 401 in codes
    assert 429 in codes, f"默认限流未生效，状态码: {codes}"


def test_config_typo_is_reported_instead_of_silently_ignored(monkeypatch, tmp_path):
    """错拼的环境变量必须产生告警，而不是静默回退默认值。"""
    monkeypatch.setenv("CODE_REVIEW_ALLOWED_ROOTS", str(tmp_path))  # 故意拼错（复数）

    server = _load_server(monkeypatch, tmp_path)

    assert server._config_warnings, "错拼的配置项没有产生任何告警"
    assert any("CODE_REVIEW_ALLOWED_ROOTS" in w for w in server._config_warnings)
    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)
