"""审计闭环回归测试（第 4 项）。

企业级审计提出的问题是：**能不能回答"谁在什么时候对哪个文件做了什么"**。
改造前答不出来，因为：
    - 遥测 JSONL 里没有 actor 字段；
    - `AgentLogger("./logs")` 由 API 构造时不传用户；
    - 没有任何请求 ID 把日志/遥测/数据库审计行串起来；
    - 登录成功/失败、越权访问全部零审计记录。

这些测试锁定四件事：请求 ID 贯穿、actor 归属、文件级取证、错误码稳定。
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.harness.request_context import (  # noqa: E402
    get_context,
    new_request_id,
    request_context,
)
from src.llm_client import LLMResponse  # noqa: E402


class StubLLM:
    def __init__(self, responses=None):
        self.responses = list(responses or [LLMResponse(content="ok", usage={})])

    def chat(self, messages, tools=None, **kwargs):
        if not self.responses:
            return LLMResponse(content="ok", usage={})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _load_server(monkeypatch, tmp_path, stub=None):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("CODE_REVIEW_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)
    server = importlib.import_module("src.api.server")
    server.DATA_DIR = tmp_path / "data"
    server.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if stub is not None:
        server._build_llm_client = lambda: stub
    return server


@pytest.fixture()
def client(monkeypatch, tmp_path):
    stub = StubLLM()
    server = _load_server(monkeypatch, tmp_path, stub)
    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")
    testclient = pytest.importorskip("fastapi.testclient")
    with testclient.TestClient(server.app) as c:
        c.server = server  # type: ignore[attr-defined]
        c.project = project  # type: ignore[attr-defined]
        c.stub = stub  # type: ignore[attr-defined]
        yield c
    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)


def _login(client, username="admin", password="test-admin-password"):
    resp = client.post("/auth/login", json={"username": username, "password": password})
    if resp.status_code == 200:
        client.headers.update({"Authorization": f"Bearer {resp.json()['access_token']}"})
    return resp


# ═══ 请求上下文 ═══

def test_request_context_is_scoped_and_restored():
    assert get_context().request_id == ""

    with request_context(request_id="abc123", actor="alice", project_path="/repo"):
        assert get_context().request_id == "abc123"
        assert get_context().actor == "alice"
        assert get_context().as_fields()["project_path"] == "/repo"

    assert get_context().request_id == "", "上下文必须在退出后恢复，否则会串到下一个请求"


def test_context_fields_omit_empty_values():
    """空值不写入：保证旧日志格式与既有逐字段断言不受影响。"""
    from src.harness.request_context import RequestContext

    assert RequestContext().as_fields() == {}
    assert RequestContext(actor="alice").as_fields() == {"actor": "alice"}


def test_new_request_id_is_short_and_unique():
    ids = {new_request_id() for _ in range(50)}

    assert len(ids) == 50
    assert all(len(i) == 12 for i in ids)


# ═══ 请求 ID 贯穿 ═══

def test_response_carries_request_id_header(client):
    resp = client.get("/health")

    assert resp.status_code == 200
    assert len(resp.headers.get("X-Request-ID", "")) == 12


def test_incoming_request_id_is_honoured(client):
    """上游反代/网关传入的请求号必须沿用，否则链路追踪会断。"""
    resp = client.get("/health", headers={"X-Request-ID": "trace-from-gateway"})

    assert resp.headers["X-Request-ID"] == "trace-from-gateway"


def test_request_id_appears_in_error_response(client):
    resp = client.get("/sessions/does-not-exist")

    assert resp.status_code == 401      # 未登录
    body = resp.json()
    assert body["error_code"] == "UNAUTHORIZED"
    assert len(body["request_id"]) == 12


# ═══ 统一错误码 ═══

def test_error_codes_are_stable_and_categorised(client):
    _login(client)

    not_found = client.get("/sessions/nope")
    invalid = client.post("/analyze", json={"query": "x", "mode": "bogus",
                                            "project_path": str(client.project)})

    assert not_found.status_code == 404
    assert not_found.json()["error_code"] == "NOT_FOUND"
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "INVALID_REQUEST"
    # detail 仍然保留可读信息，只是额外提供了稳定分类
    assert "single" in invalid.json()["detail"]


def test_unhandled_exception_returns_structured_500(client, monkeypatch):
    """未预期异常也必须带 request_id 与 error_code，便于运维定位。

    用一个会抛异常的额外端点验证全局兜底处理器：`/analyze` 有自己的
    try/except（它必须留下可解释终态），所以异常到不了全局处理器。
    """
    _login(client)

    @client.server.app.get("/boom-audit-test")
    async def _boom_endpoint():
        raise RuntimeError("kaboom")

    resp = client.get("/boom-audit-test")

    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert len(body["request_id"]) == 12
    assert "kaboom" not in resp.text       # 内部细节不外泄


def test_analyze_internal_failure_stays_degraded_with_error_code(client, monkeypatch):
    """`/analyze` 内部异常返回 200 + degraded（会话已建），但必须带错误码。"""
    _login(client)

    def _boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(client.server, "build_api_tools", _boom)

    resp = client.post("/analyze", json={"query": "x", "mode": "single",
                                         "project_path": str(client.project)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["error_code"] == "analyze_failed:RuntimeError"
    assert "kaboom" not in body["result"], "内部异常文本不应回显给调用方"


# ═══ 登录审计 ═══

def test_login_success_and_failure_are_audited(client):
    client.post("/auth/login", json={"username": "admin", "password": "wrong"})
    _login(client)

    trail = client.server.get_db().get_audit_trail(limit=50)
    events = {(row["event"], row["outcome"]) for row in trail}

    assert ("auth_login", "FAILED") in events
    assert ("auth_login", "SUCCESS") in events
    assert all(row["request_id"] for row in trail), "审计行必须带请求号"


def test_login_logout_is_audited(client):
    _login(client)

    client.post("/auth/logout")

    trail = client.server.get_db().get_audit_trail(limit=50)
    assert any(row["event"] == "auth_logout" for row in trail)


# ═══ 文件级取证（核心问题：谁对哪个文件做了什么）═══

def test_analyze_audit_answers_who_did_what_to_which_project(client):
    _login(client)

    resp = client.post("/analyze", json={
        "query": "Find bugs in a.py", "mode": "single",
        "project_path": str(client.project)})
    assert resp.status_code == 200

    trail = client.server.get_db().get_audit_trail(actor="admin", limit=50)
    starts = [r for r in trail if r["event"] == "analyze_start"]
    completes = [r for r in trail if r["event"] == "analyze_complete"]

    assert starts and completes
    assert starts[0]["actor"] == "admin"
    assert starts[0]["file_path"] == str(client.project)
    assert starts[0]["request_id"]
    assert completes[0]["outcome"] == "SUCCESS"
    # 同一次操作的两条记录必须共享请求号，否则无法串成一次操作
    assert starts[0]["request_id"] == completes[0]["request_id"]


def test_out_of_scope_access_is_audited_as_forbidden(client, tmp_path):
    _login(client)
    outside = tmp_path.parent / "outside-audit"
    outside.mkdir(exist_ok=True)

    resp = client.post("/analyze", json={"query": "x", "mode": "single",
                                         "project_path": str(outside)})

    assert resp.status_code == 422
    trail = client.server.get_db().get_audit_trail(actor="admin", limit=50)
    rejected = [r for r in trail if r["event"] == "analyze_rejected"]
    assert rejected, "越界访问必须留审计记录"
    assert rejected[0]["outcome"] == "FORBIDDEN"
    assert rejected[0]["error"] == 1


def test_degraded_run_is_audited_as_degraded(client, monkeypatch):
    """降级运行在审计里必须与成功区分，否则审计会掩盖质量问题。"""
    client.stub.responses = [RuntimeError("API HTTP 429: rate limited")]
    _login(client)

    client.post("/analyze", json={"query": "x", "mode": "single",
                                  "project_path": str(client.project)})

    trail = client.server.get_db().get_audit_trail(actor="admin", limit=50)
    completes = [r for r in trail if r["event"] == "analyze_complete"]
    assert completes and completes[0]["outcome"] == "DEGRADED"


def test_audit_trail_filters_by_file_path(client):
    _login(client)
    client.post("/analyze", json={"query": "x", "mode": "single",
                                  "project_path": str(client.project)})

    matching = client.server.get_db().get_audit_trail(file_path="project", limit=50)
    non_matching = client.server.get_db().get_audit_trail(file_path="nonexistent-path",
                                                          limit=50)

    assert matching
    assert non_matching == []


def test_audit_trail_excludes_non_audit_logs(client):
    """普通执行日志不应混进审计查询（否则取证被噪音淹没）。"""
    db = client.server.get_db()
    db.add_log("s1", "turn_start", 1, "read_file")          # 无 actor / request_id
    db.add_log("s1", "analyze_start", actor="alice", request_id="r1")

    trail = db.get_audit_trail(limit=50)

    assert [r["event"] for r in trail] == ["analyze_start"]


# ═══ 遥测 JSONL 带审计字段 ═══

def test_telemetry_records_actor_and_request_id(client, tmp_path):
    _login(client)
    client.post("/analyze", json={"query": "x", "mode": "single",
                                  "project_path": str(client.project)})

    log_files = sorted(Path("logs").glob("*.jsonl"))
    assert log_files, "分析未产生遥测日志"
    records = [json.loads(line) for line in
               log_files[-1].read_text(encoding="utf-8").splitlines() if line.strip()]

    with_actor = [r for r in records if r.get("actor")]
    assert with_actor, "遥测记录缺少 actor —— 审计无法归因到人"
    assert all(r["actor"] == "admin" for r in with_actor)
    assert all(r.get("request_id") for r in with_actor)


def test_telemetry_without_context_has_no_audit_fields(tmp_path):
    """CLI/测试等无请求上下文的场景不写审计字段，保持旧格式。"""
    from src.harness.telemetry import AgentLogger

    logger = AgentLogger(str(tmp_path / "logs"))
    logger.turn_start(1, 1)

    records = [json.loads(line) for line in
               Path(logger.log_path).read_text(encoding="utf-8").splitlines()]
    assert all("actor" not in r for r in records)
    assert all("request_id" not in r for r in records)


def test_role_views_inherit_actor(tmp_path):
    """多 Agent 的角色视图必须继承审计主体，否则只有主 logger 的事件可归因。"""
    from src.harness.telemetry import AgentLogger

    with request_context(request_id="rid-1", actor="alice"):
        main = AgentLogger(str(tmp_path / "logs"))
        planner = main.for_role("planner")
        planner.turn_start(1, 1)

    records = [json.loads(line) for line in
               Path(main.log_path).read_text(encoding="utf-8").splitlines()]
    assert all(r.get("actor") == "alice" for r in records)
    assert all(r.get("request_id") == "rid-1" for r in records)


# ═══ 结构化日志 ═══

def test_structured_log_lines_are_json(tmp_path, caplog):
    """日志输出必须是可被采集系统解析的单行 JSON。"""
    from src.api.server import _JsonLogFormatter
    import logging

    formatter = _JsonLogFormatter()
    record = logging.LogRecord("src.test", logging.INFO, __file__, 1,
                               "hello %s", ("world",), None)
    line = formatter.format(record)
    payload = json.loads(line)

    assert payload["level"] == "INFO"
    assert payload["message"] == "hello world"
    assert "ts" in payload


def test_structured_log_includes_request_context():
    from src.api.server import _JsonLogFormatter
    import logging

    formatter = _JsonLogFormatter()
    record = logging.LogRecord("src.test", logging.WARNING, __file__, 1,
                               "careful", None, None)
    with request_context(request_id="rid-9", actor="bob"):
        payload = json.loads(formatter.format(record))

    assert payload["request_id"] == "rid-9"
    assert payload["actor"] == "bob"
