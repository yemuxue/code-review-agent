"""思考深度（reasoning effort）与缓存键正确性的回归测试。

两个真实缺陷：
    1. **缓存键不含请求参数**：把 reasoning_effort 从 low 改成 high 时，
       第二次调用 0.0s 返回了与 low 完全相同的内容——命中旧深度的缓存，
       回答与参数不一致却没有任何提示。
    2. **两条协议的原生写法不同**：responses 用 `reasoning.effort`
       （实测会回报 reasoning_tokens，可验证），anthropic 用
       `thinking.budget_tokens`（网关接受但不回报用量，无法验证）。
       硬套一个参数名会让其中一条协议静默失效。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.harness.llm_cache import LLMCache  # noqa: E402
from src.llm_client import (  # noqa: E402
    AnthropicClient,
    _reasoning_effort,
    _REASONING_BUDGET_TOKENS,
)


def _client(monkeypatch, protocol: str = "anthropic",
            effort: str = "", model: str = "test-model") -> AnthropicClient:
    """构造测试客户端。

    ⚠️ 必须让 base_url 等于"配置的端点"，否则客户端会走
    "自定义端点一律用 anthropic 协议"的兼容分支，测试就在测别的东西。
    """
    monkeypatch.setenv("LLM_API_PROTOCOL", protocol)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://api.test")
    if effort:
        monkeypatch.setenv("LLM_REASONING_EFFORT", effort)
    else:
        monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    client = AnthropicClient(api_key="test-key", model=model, use_cache=False)
    assert client.api_protocol == protocol, (
        f"协议未按预期解析：期望 {protocol}，实际 {client.api_protocol}")
    return client


# ═══ 配置读取 ═══

def test_reasoning_effort_reads_valid_values(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "HIGH")
    assert _reasoning_effort() == "high"


def test_reasoning_effort_ignores_invalid_and_empty(monkeypatch):
    """非法值不能悄悄生效：应视为"不指定"，而不是塞给下游一个未知值。"""
    for bad in ("", "   ", "very-high", "ULTRA"):
        monkeypatch.setenv("LLM_REASONING_EFFORT", bad)
        assert _reasoning_effort() == ""


def test_reasoning_effort_defaults_to_off(monkeypatch):
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    assert _reasoning_effort() == ""


# ═══ 缓存键必须包含请求参数 ═══

def test_cache_key_differs_when_reasoning_effort_differs():
    cache = LLMCache()
    messages = [{"role": "user", "content": "same prompt"}]

    key_low = cache._make_key(messages, "m", "responses|effort=low")
    key_high = cache._make_key(messages, "m", "responses|effort=high")

    assert key_low != key_high, "思考深度不同的请求不能共用缓存条目"


def test_cache_does_not_serve_low_effort_result_to_high_effort_request():
    """核心回归：改 effort 后必须 miss，而不是拿到旧深度的答案。"""
    cache = LLMCache()
    messages = [{"role": "user", "content": "p"}]
    cache.set(messages, "low-effort-answer", "m", "responses|effort=low")

    assert cache.get(messages, "m", "responses|effort=high") is None
    assert cache.get(messages, "m", "responses|effort=low") == "low-effort-answer"


def test_cache_key_differs_across_protocols():
    """同一模型走不同协议时响应结构不同，不能互相污染。"""
    cache = LLMCache()
    messages = [{"role": "user", "content": "p"}]

    assert (cache._make_key(messages, "m", "anthropic|effort=-")
            != cache._make_key(messages, "m", "responses|effort=-"))


def test_client_cache_variant_reflects_protocol_and_effort(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    client = _client(monkeypatch, protocol="responses", effort="high")

    variant = client._cache_variant()

    assert "responses" in variant and "high" in variant


def test_client_cache_variant_is_stable_for_same_settings():
    cache = LLMCache()
    messages = [{"role": "user", "content": "p"}]
    variant = "responses|effort=high"

    assert (cache._make_key(messages, "m", variant)
            == cache._make_key(messages, "m", variant))


# ═══ 两条协议的原生写法 ═══

def test_responses_payload_carries_reasoning_effort(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        raise RuntimeError("stop here")      # 只关心请求体

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client(monkeypatch, protocol="responses", effort="high")

    with pytest.raises(RuntimeError):
        client.chat([{"role": "user", "content": "hi"}])

    assert captured["payload"]["reasoning"] == {"effort": "high"}


def test_anthropic_payload_uses_thinking_budget(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        raise RuntimeError("stop here")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client(monkeypatch, protocol="anthropic", effort="high")

    with pytest.raises(RuntimeError):
        client.chat([{"role": "user", "content": "hi"}])

    thinking = captured["payload"]["thinking"]
    assert thinking["type"] == "enabled"
    assert thinking["budget_tokens"] == _REASONING_BUDGET_TOKENS["high"]
    # thinking 占用输出预算，max_tokens 必须留出余量，否则正文会被挤空
    assert captured["payload"]["max_tokens"] > thinking["budget_tokens"]


def test_no_reasoning_field_when_effort_unset(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        raise RuntimeError("stop here")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = _client(monkeypatch, protocol="responses", effort="")

    with pytest.raises(RuntimeError):
        client.chat([{"role": "user", "content": "hi"}])

    assert "reasoning" not in captured["payload"]


def test_gpt55_keeps_its_historical_low_effort_default(monkeypatch):
    """未显式配置时保留 gpt-5.5 的历史默认（评测里只做短判定）。"""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        raise RuntimeError("stop here")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("LLM_API_PROTOCOL", "responses")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://api.test")
    monkeypatch.delenv("LLM_REASONING_EFFORT", raising=False)
    client = AnthropicClient(api_key="k", model="gpt-5.5", use_cache=False)
    assert client.api_protocol == "responses"

    with pytest.raises(RuntimeError):
        client.chat([{"role": "user", "content": "hi"}])

    assert captured["payload"]["reasoning"] == {"effort": "low"}
