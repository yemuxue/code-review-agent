"""AnthropicClient.chat 重试语义。

瞬时 HTTP 429/5xx/529 会重试（默认 4 次尝试，含首次），其余错误直接抛；
重试次数可通过 LLM_MAX_ATTEMPTS 配置——网关抖动实测会出现"成片 502"，
固定 3 次不足以吸收，因此默认值提高到 4 且可调。
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm_client import AnthropicClient, _retry_attempts

_OK_BODY = json.dumps(
    {"content": [{"type": "text", "text": "OK"}], "usage": {}}).encode("utf-8")


def _client() -> AnthropicClient:
    return AnthropicClient(api_key="test-key", base_url="http://api.test",
                           model="test-model", use_cache=False)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://api.test/v1/messages", code, "err",
                                  hdrs=None, fp=io.BytesIO(b'{"error":"boom"}'))


class _FakeResp:
    """urlopen 成功路径返回的响应：支持 with 语法与 .read()。"""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _stub_urlopen(monkeypatch, responses: list):
    """urlopen 按序返回 responses（异常或 _FakeResp）；退避 sleep 置为 no-op。"""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=120):
        calls["n"] += 1
        resp = responses[min(calls["n"] - 1, len(responses) - 1)]
        if isinstance(resp, BaseException):
            raise resp
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return calls


def test_transient_503_retried_then_succeeds(monkeypatch):
    client = _client()
    calls = _stub_urlopen(monkeypatch, [_http_error(503), _FakeResp(_OK_BODY)])

    result = client.chat([{"role": "user", "content": "hi"}])

    assert result.content == "OK"
    assert calls["n"] == 2  # 1 次失败 + 1 次成功


def test_transient_exhausted_raises_after_configured_attempts(monkeypatch):
    """用满配置的尝试次数后必须抛出，且不再继续调用。"""
    monkeypatch.delenv("LLM_MAX_ATTEMPTS", raising=False)
    expected = _retry_attempts()
    client = _client()
    calls = _stub_urlopen(monkeypatch, [_http_error(503)] * expected)

    with pytest.raises(RuntimeError, match="API HTTP 503"):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == expected


def test_retry_attempts_is_configurable(monkeypatch):
    """可通过环境变量增强容忍度（网关抖动时需要更多尝试）。"""
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "6")
    assert _retry_attempts() == 6

    client = _client()
    calls = _stub_urlopen(monkeypatch, [_http_error(502)] * 6)

    with pytest.raises(RuntimeError, match="API HTTP 502"):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 6


def test_retry_attempts_clamps_invalid_and_extreme_values(monkeypatch):
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "not-a-number")
    assert _retry_attempts() >= 1

    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "999")
    assert _retry_attempts() <= 8      # 防止把一次调用变成无限等待


def test_retry_after_header_is_respected(monkeypatch):
    """上游给了 Retry-After 就按它等待，避免在网关过载时继续加压。"""
    from src.llm_client import _retry_delay

    headers = {"Retry-After": "7"}
    error = urllib.error.HTTPError("http://api.test/v1/messages", 503, "err",
                                   hdrs=headers, fp=io.BytesIO(b"{}"))
    assert _retry_delay(0, error) == 7.0

    # 非法值回退到指数退避
    bad = urllib.error.HTTPError("http://api.test/v1/messages", 503, "err",
                                 hdrs={"Retry-After": "soon"}, fp=io.BytesIO(b"{}"))
    assert _retry_delay(0, bad) == 1.0


def test_request_sets_non_default_user_agent(monkeypatch):
    """网关边缘过滤会拒绝 urllib 默认 UA（403 / error 1010）。

    responses 路径早已设置该头，anthropic 路径此前遗漏，
    导致同一网关下"一条协议能用、另一条 403"。
    """
    captured = {}

    def fake_urlopen(req, timeout=120):
        captured["ua"] = req.get_header("User-agent")
        return _FakeResp(_OK_BODY)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: None)

    _client().chat([{"role": "user", "content": "hi"}])

    assert captured["ua"] and captured["ua"].lower() != "python-urllib"


def test_client_error_4xx_not_retried(monkeypatch):
    client = _client()
    calls = _stub_urlopen(monkeypatch, [_http_error(400), _FakeResp(_OK_BODY)])

    with pytest.raises(RuntimeError, match="API HTTP 400"):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # 4xx 属客户端错误，不重试


def test_urlerror_not_retried(monkeypatch):
    client = _client()
    calls = _stub_urlopen(
        monkeypatch, [urllib.error.URLError(OSError("connection refused"))])

    with pytest.raises(RuntimeError, match="connection refused"):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # 连接类错误（如 10013）交给上层精确展示，不盲目重试
