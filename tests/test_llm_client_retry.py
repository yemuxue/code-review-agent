"""AnthropicClient.chat 重试语义：瞬时 HTTP 429/5xx/529 重试 2 次，其余错误直接抛。"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm_client import AnthropicClient

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


def test_transient_exhausted_raises_after_three_attempts(monkeypatch):
    client = _client()
    calls = _stub_urlopen(monkeypatch, [_http_error(503)] * 3)

    with pytest.raises(RuntimeError, match="API HTTP 503"):
        client.chat([{"role": "user", "content": "hi"}])
    assert calls["n"] == 3  # 总尝试 3 次（初始 + 2 次重试），不再继续


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
