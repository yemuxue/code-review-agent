"""/analyze 端点的离线回归测试。

背景：这条路径此前**没有任何测试**，因为它直接在函数体里构造 `AnthropicClient()`
并发起真实网络请求。本轮加了 `_build_llm_client()` 依赖注入缝之后，
整条链路（鉴权 → 建会话 → Harness/编排 → 落库 → 建索引 → 响应）
都可以用桩模型离线跑，从而拿到回归保护。

同时锁定"降级语义"：模型故障不再是"HTTP 200 + 一段错误文本"，
而是带 `degraded=True` + `error_code` 的可程序化判断结果。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm_client import LLMResponse  # noqa: E402


class StubLLM:
    """按预设脚本返回响应；兼容 AgentHarness 需要的 chat 契约。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.responses:
            return LLMResponse(content="done", usage={})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def stream(self, messages, tools=None):
        for chunk in ():
            yield chunk


def _load_server(monkeypatch, tmp_path, stub):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-that-is-long-enough")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("CODE_REVIEW_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)
    server = importlib.import_module("src.api.server")
    server.DATA_DIR = tmp_path / "data"
    server.DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 依赖注入：把真实 LLM 客户端换成桩
    server._build_llm_client = lambda: stub
    return server


@pytest.fixture()
def make_client(monkeypatch, tmp_path):
    testclient = pytest.importorskip("fastapi.testclient")
    created = []

    def _factory(stub, project_dir=None):
        server = _load_server(monkeypatch, tmp_path, stub)
        project = project_dir or (tmp_path / "project")
        project.mkdir(parents=True, exist_ok=True)
        (project / "sample.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        client = testclient.TestClient(server.app)
        client.__enter__()
        created.append((client, server))
        token = client.post("/auth/login", json={
            "username": "admin", "password": "test-admin-password"}).json()["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        client.project = project  # type: ignore[attr-defined]
        return client, server

    yield _factory

    for client, _ in created:
        client.__exit__(None, None, None)
    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)


def _run_analysis(client, query="Find bugs", mode="single", project=None):
    return client.post("/analyze", json={
        "query": query, "mode": mode,
        "project_path": str(project or client.project),
        "max_turns": 3,
    })


# ═══ 正常路径 ═══

def test_single_agent_analyze_completes_offline(make_client):
    stub = StubLLM([
        LLMResponse(content=None, tool_calls=[{"id": "c1", "name": "list_files",
                                               "args": {"path": "."}}], usage={}),
        LLMResponse(content="FINDING|sample.py|2|BUG|Low|EN:returns int|CN:返回整数|none",
                    usage={"input_tokens": 10, "output_tokens": 5}),
    ])
    client, server = make_client(stub)

    resp = _run_analysis(client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is False
    assert body["error_code"] == ""
    assert "FINDING|sample.py" in body["result"]
    assert body["stats"]["tools_called"] == 1
    assert body["session_id"]


def test_single_agent_analyze_persists_session_owned_by_caller(make_client):
    stub = StubLLM([LLMResponse(content="all good", usage={})])
    client, server = make_client(stub)

    body = _run_analysis(client).json()

    session = server.get_db().get_session(body["session_id"], owner_username="admin")
    assert session is not None, "会话未落库或未归属调用者"
    messages = server.get_db().get_messages(body["session_id"], owner_username="admin")
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_analyze_indexes_findings_for_the_caller_only(make_client):
    stub = StubLLM([
        LLMResponse(content="FINDING|sample.py|2|BUG|Low|EN:returns int|CN:返回整数|none",
                    usage={}),
    ])
    client, server = make_client(stub)

    body = _run_analysis(client).json()

    findings = server.get_db().get_findings(session_id=body["session_id"],
                                            owner_username="admin")
    assert findings, "FINDING 行未被解析落库"
    assert findings[0]["owner_username"] == "admin"
    # 向量索引同样按所有者可检索
    hits = server.get_vector_store().search("int", owner_username="admin")
    assert hits, "发现未进入全文索引"


def test_multi_agent_analyze_completes_offline(make_client):
    stub = StubLLM([
        LLMResponse(content="FINDING|sample.py|2|BUG|Low|EN:returns int|CN:返回整数|none", usage={}),
        LLMResponse(content="VERDICT|1|CONFIRMED|line 2 returns int", usage={}),
        LLMResponse(content="# Code Analysis Report\n\n## Summary\nok", usage={}),
    ])
    client, _ = make_client(stub)

    resp = _run_analysis(client, mode="multi")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["degraded"] is False
    assert "Code Analysis Report" in body["result"]
    assert "node_stats" in body["stats"]


# ═══ 降级路径：必须可被调用方识别，而不是静默 200 ═══

def test_model_failure_is_reported_as_degraded_not_silent_success(make_client):
    stub = StubLLM([RuntimeError("API HTTP 429: rate limited")])
    client, _ = make_client(stub)

    resp = _run_analysis(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["error_code"].startswith("model_error:")
    assert "429" in body["result"]


def test_turn_budget_exhaustion_is_reported_as_degraded(make_client):
    """模型一直调工具不放答案 → 预算耗尽兜底，必须标记降级。"""
    repeated = LLMResponse(content=None, tool_calls=[{"id": "c1", "name": "list_files",
                                                      "args": {"path": "."}}], usage={})
    stub = StubLLM([repeated, repeated, repeated, LLMResponse(content="best effort", usage={})])
    client, _ = make_client(stub)

    body = _run_analysis(client, project=None).json()

    assert body["degraded"] is True
    assert body["error_code"] == "turn_budget_exhausted"


def test_empty_final_answer_is_reported_as_degraded(make_client):
    stub = StubLLM([LLMResponse(content="", usage={})])
    client, _ = make_client(stub)

    body = _run_analysis(client).json()

    assert body["degraded"] is True
    assert body["error_code"] == "empty_final_answer"


def test_unexpected_orchestrator_exception_does_not_return_bare_500(make_client, monkeypatch):
    """编排层抛出未预期异常时必须留下可解释终态与已落库的消息。"""
    stub = StubLLM([])
    client, server = make_client(stub)

    def _boom(*args, **kwargs):
        raise RuntimeError("orchestrator exploded")

    # 只在 multi 分支触发：替换工厂返回的编排器 run
    class BoomOrch:
        def run(self, *a, **k):
            raise RuntimeError("orchestrator exploded")

    monkeypatch.setattr(server, "create_langgraph_orchestrator",
                        lambda *a, **k: BoomOrch())

    resp = _run_analysis(client, mode="multi")

    assert resp.status_code == 200
    body = resp.json()
    assert body["degraded"] is True
    assert body["error_code"] == "analyze_failed:RuntimeError"
    # 会话必须留下消息，避免"有 session 无内容"的孤儿记录
    messages = server.get_db().get_messages(body["session_id"], owner_username="admin")
    assert len(messages) == 2


# ═══ 输入校验 ═══

def test_unknown_mode_is_rejected_instead_of_silently_treated_as_single(make_client):
    stub = StubLLM([LLMResponse(content="x", usage={})])
    client, _ = make_client(stub)

    resp = _run_analysis(client, mode="multii")  # 拼错

    assert resp.status_code == 422
    assert "single" in resp.json()["detail"]


def test_project_path_outside_allowed_root_is_rejected(make_client, tmp_path):
    stub = StubLLM([LLMResponse(content="x", usage={})])
    client, _ = make_client(stub)
    outside = tmp_path.parent / "outside-project"
    outside.mkdir(exist_ok=True)

    resp = _run_analysis(client, project=outside)

    assert resp.status_code == 422


def test_analyze_requires_authentication(make_client):
    stub = StubLLM([LLMResponse(content="x", usage={})])
    client, _ = make_client(stub)
    client.headers.pop("Authorization", None)

    assert client.post("/analyze", json={"query": "x", "project_path": ".",
                                         "mode": "single"}).status_code == 401


def test_harness_never_calls_real_client_when_stub_injected(make_client):
    """守卫：注入缝必须真的被使用，否则测试会静默打出真实 API 请求。"""
    stub = StubLLM([LLMResponse(content="ok", usage={})])
    client, _ = make_client(stub)

    _run_analysis(client)

    assert stub.calls, "桩模型未被调用 —— _build_llm_client 注入缝可能失效"
    assert stub.calls[0]["tools"], "工具 schema 未传给模型"
