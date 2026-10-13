"""异步分析任务（第 5 项）的离线回归测试。

锁定企业级审计提出的三个缺口：
    - `/analyze` 同步阻塞：请求挂到分析结束，调用方无法查进度/取消/幂等重试；
    - 无并发闸门：几十个并发会一起冲进 asyncio 线程池（等于没有上限）；
    - 无单任务超时：卡住的任务永远不结束，调用方一直等。

任务注册表（`src/api/jobs.py`）用纯 asyncio 实现，因此这些测试**不需要真实
LLM、不需要网络**，可以用"等待事件"的方式精确控制时序。
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.jobs import Job, JobRegistry, JobStatus  # noqa: E402
from src.llm_client import LLMResponse  # noqa: E402


class StubLLM:
    def __init__(self, responses=None, delay: float = 0.0):
        self.responses = list(responses or [LLMResponse(content="ok", usage={})])
        self.delay = delay
        self.calls = 0

    def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.delay:
            import time
            time.sleep(self.delay)
        if not self.responses:
            return LLMResponse(content="ok", usage={})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


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
    server.job_registry.reset()
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
        token = c.post("/auth/login", json={"username": "admin",
                                            "password": "test-admin-password"}).json()
        c.headers.update({"Authorization": f"Bearer {token['access_token']}"})
        yield c
    for mod in ("src.api.server", "src.harness.jwt_auth", "src.config"):
        sys.modules.pop(mod, None)


def _submit(client, **overrides):
    body = {"query": "Find bugs", "mode": "single",
            "project_path": str(client.project), "max_turns": 3}
    body.update(overrides)
    return client.post("/analyze/async", json=body)


def _wait_terminal(client, job_id, timeout=15.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["terminal"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"任务未在 {timeout}s 内到达终态: {body}")


# ═══ 注册表单元行为 ═══

def test_registry_rejects_invalid_concurrency():
    with pytest.raises(ValueError):
        JobRegistry(max_concurrent=0)


def test_registry_enforces_concurrency_gate():
    """并发上限必须真的限制同时在跑的任务数。"""
    async def scenario():
        registry = JobRegistry(max_concurrent=2)
        running = 0
        peak = 0

        async def runner(job):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1

        jobs = [registry.create(actor="a", session_id=f"s{i}", mode="single",
                                project_path="/p") for i in range(6)]
        await asyncio.gather(*(registry.start(job, runner) for job in jobs))
        return peak, registry.counts()

    peak, counts = asyncio.run(scenario())

    assert peak == 2, f"并发闸门失效，峰值并发 {peak}"
    assert counts[JobStatus.SUCCEEDED.value] == 6


def test_registry_marks_timeout_and_does_not_hang():
    async def scenario():
        registry = JobRegistry(max_concurrent=1, default_timeout_s=0.05)

        async def runner(job):
            await asyncio.sleep(5)

        job = registry.create(actor="a", session_id="s", mode="single", project_path="/p")
        await registry.start(job, runner)
        return job

    job = asyncio.run(scenario())

    assert job.status is JobStatus.TIMEOUT
    assert "job_timeout" in job.error_code
    assert job.finished_at is not None


def test_registry_marks_runner_exception_as_failed():
    async def scenario():
        registry = JobRegistry(max_concurrent=1)

        async def runner(job):
            raise RuntimeError("runner exploded")

        job = registry.create(actor="a", session_id="s", mode="single", project_path="/p")
        await registry.start(job, runner)
        return job

    job = asyncio.run(scenario())

    assert job.status is JobStatus.FAILED
    assert job.error_code == "job_failed:RuntimeError"


def test_registry_idempotency_lookup_is_scoped_to_actor():
    registry = JobRegistry()
    job = registry.create(actor="alice", session_id="s", mode="single",
                          project_path="/p", idempotency_key="key-1")

    assert registry.find_idempotent("alice", "key-1") is job
    assert registry.find_idempotent("bob", "key-1") is None, "幂等键不能跨用户命中"
    assert registry.find_idempotent("alice", "") is None


def test_registry_get_owned_hides_other_users_jobs():
    registry = JobRegistry()
    job = registry.create(actor="alice", session_id="s", mode="single", project_path="/p")

    assert registry.get_owned(job.job_id, "alice") is job
    assert registry.get_owned(job.job_id, "bob") is None


def test_registry_evicts_old_terminal_jobs():
    """进程内注册表必须有界：终态任务按完成时间淘汰最旧的。"""
    registry = JobRegistry(max_concurrent=1, retention=5)
    jobs = []
    for i in range(12):
        job = registry.create(actor="a", session_id=f"s{i}", mode="single", project_path="/p")
        job.status = JobStatus.SUCCEEDED
        job.finished_at = float(i)      # 稳定递增，避免依赖创建顺序
        jobs.append(job)

    assert len(registry._jobs) <= 5
    assert registry.get(jobs[0].job_id) is None, "最旧的终态任务应被淘汰"
    assert registry.get(jobs[-1].job_id) is not None, "最新的任务必须保留"


def test_registry_does_not_evict_running_jobs():
    """淘汰只针对终态：不能把还在跑的任务丢掉，否则查不到自己的任务。"""
    registry = JobRegistry(max_concurrent=1, retention=2)
    running = [registry.create(actor="a", session_id=f"r{i}", mode="single",
                               project_path="/p") for i in range(3)]
    for job in running:
        job.status = JobStatus.RUNNING
    for i in range(4):
        done = registry.create(actor="a", session_id=f"d{i}", mode="single",
                               project_path="/p")
        done.status = JobStatus.SUCCEEDED
        done.finished_at = float(i)

    assert all(registry.get(job.job_id) is not None for job in running)


def test_job_to_dict_omits_internal_task_handle():
    job = Job(job_id="j1", actor="a", session_id="s", mode="single", project_path="/p")

    payload = job.to_dict()

    assert "job_id" in payload and payload["job_id"] == "j1"
    assert not any(k.startswith("_") for k in payload)


# ═══ 端点行为 ═══

def test_async_submit_returns_202_with_job_id(client):
    resp = _submit(client)

    assert resp.status_code == 202
    body = resp.json()
    assert len(body["job_id"]) == 16
    assert body["status"] in {"QUEUED", "RUNNING", "SUCCEEDED"}
    assert body["idempotent_replay"] is False


def test_async_job_reaches_success_and_exposes_result(client):
    job_id = _submit(client).json()["job_id"]

    final = _wait_terminal(client, job_id)

    assert final["status"] == "SUCCEEDED"
    assert final["degraded"] is False
    assert final["result"]
    assert final["session_id"]
    assert final["elapsed_ms"] >= 0


def test_async_job_persists_messages_and_indexes_findings(client):
    client.stub.responses = [LLMResponse(
        content="FINDING|a.py|1|BUG|Low|EN:const|CN:常量|none", usage={})]
    job_id = _submit(client).json()["job_id"]
    final = _wait_terminal(client, job_id)

    sid = final["session_id"]
    messages = client.server.get_db().get_messages(sid, owner_username="admin")
    assert [m["role"] for m in messages] == ["user", "assistant"]
    findings = client.server.get_db().get_findings(session_id=sid, owner_username="admin")
    assert findings, "异步路径也必须解析并落库 FINDING"


def test_async_job_reports_degraded_run(client):
    client.stub.responses = [RuntimeError("API HTTP 429: rate limited")]
    job_id = _submit(client).json()["job_id"]

    final = _wait_terminal(client, job_id)

    assert final["status"] == "SUCCEEDED"     # 任务本身跑完了
    assert final["degraded"] is True          # 但结果是降级的
    assert final["error_code"].startswith("model_error:")


def test_async_idempotency_key_returns_same_job(client):
    headers = {"Idempotency-Key": "same-key"}

    first = client.post("/analyze/async", headers=headers, json={
        "query": "x", "mode": "single", "project_path": str(client.project)})
    second = client.post("/analyze/async", headers=headers, json={
        "query": "x", "mode": "single", "project_path": str(client.project)})

    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.json()["idempotent_replay"] is True
    # 只应产生一个会话，未重复消耗 token
    sessions = client.get("/sessions").json()
    assert len(sessions) == 1


def test_different_idempotency_keys_create_distinct_jobs(client):
    a = client.post("/analyze/async", headers={"Idempotency-Key": "k1"}, json={
        "query": "x", "mode": "single", "project_path": str(client.project)})
    b = client.post("/analyze/async", headers={"Idempotency-Key": "k2"}, json={
        "query": "x", "mode": "single", "project_path": str(client.project)})

    assert a.json()["job_id"] != b.json()["job_id"]


def test_job_status_is_404_for_other_user(client):
    job_id = _submit(client).json()["job_id"]
    client.server.user_store.create_user("bob", "bob-password-123", role="user")
    bob_token = client.post("/auth/login", json={"username": "bob",
                                                 "password": "bob-password-123"}).json()
    bob_headers = {"Authorization": f"Bearer {bob_token['access_token']}"}

    resp = client.get(f"/jobs/{job_id}", headers=bob_headers)

    assert resp.status_code == 404, "他人任务应视为不存在，不泄露其存在性"


def test_unknown_job_is_404(client):
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_cancel_running_job_marks_cancelled(client, monkeypatch):
    """取消必须落到终态，否则调用方会一直轮询一个已放弃的任务。"""
    import time

    client.stub.delay = 1.0          # 让任务在跑
    job_id = _submit(client).json()["job_id"]
    # 等任务真正开始
    for _ in range(100):
        if client.get(f"/jobs/{job_id}").json()["status"] == "RUNNING":
            break
        time.sleep(0.02)

    resp = client.delete(f"/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json()["status"] in {"CANCELLED", "RUNNING"}
    # 无论取消是否及时生效，最终都必须到达某个终态
    final = _wait_terminal(client, job_id)
    assert final["terminal"] is True


def test_cancel_finished_job_returns_409(client):
    job_id = _submit(client).json()["job_id"]
    _wait_terminal(client, job_id)

    resp = client.delete(f"/jobs/{job_id}")

    assert resp.status_code == 409
    assert "SUCCEEDED" in resp.json()["detail"]


def test_async_requires_authentication(client):
    client.headers.pop("Authorization", None)

    resp = client.post("/analyze/async", json={"query": "x", "mode": "single",
                                               "project_path": str(client.project)})

    assert resp.status_code == 401


def test_async_rejects_invalid_mode_and_out_of_scope_path(client, tmp_path):
    assert _submit(client, mode="bogus").status_code == 422
    outside = tmp_path.parent / "outside-async"
    outside.mkdir(exist_ok=True)
    assert _submit(client, project_path=str(outside)).status_code == 422


def test_async_paths_are_audited(client):
    job_id = _submit(client).json()["job_id"]
    _wait_terminal(client, job_id)

    trail = client.server.get_db().get_audit_trail(actor="admin", limit=50)
    events = {row["event"] for row in trail}

    assert "analyze_submitted" in events
    assert "analyze_complete" in events
    # 提交与完成必须共享同一会话号，才能串成一次操作
    submitted = next(r for r in trail if r["event"] == "analyze_submitted")
    completed = next(r for r in trail if r["event"] == "analyze_complete")
    assert submitted["session_id"] == completed["session_id"]


def test_sync_endpoint_still_works(client):
    """同步端点保留兼容：现有调用方不受异步化影响。"""
    resp = client.post("/analyze", json={"query": "x", "mode": "single",
                                         "project_path": str(client.project)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["result"]
    assert body["job_id"] == ""      # 同步路径不产生任务号
