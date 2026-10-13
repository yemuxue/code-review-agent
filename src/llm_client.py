"""LLM Client adapter: Anthropic API"""
from __future__ import annotations
import json
import asyncio
import time
import os
from dataclasses import dataclass, field
try:
    from src.harness.streaming import Chunk
except ImportError:  # 兼容以 `src` 为工作目录直接运行脚本的场景
    # 回退导入与上面的绝对导入同名，类型检查会判为重复定义；这是
    # 运行期兼容分支而非真正的重定义，故显式忽略。
    from harness.streaming import Chunk  # type: ignore[no-redef]

# HTTP User-Agent：部分网关（含 Cloudflare 前置）会拒绝 urllib 默认 UA
# （表现为 HTTP 403 / error code 1010，看起来像鉴权失败）。两条协议路径
# 必须使用同一个常量，避免再次出现"一条协议能用、另一条 403"的不一致。
_HTTP_USER_AGENT = "curl/8.0"

# 瞬时故障的重试次数。网关抖动的实测表现是"部分请求耗时 15s 后 502"，
# 固定 3 次（1s/2s 退避）不足以吸收这种成片失败，因此改为可配置。
_DEFAULT_RETRY_ATTEMPTS = 4

# 单次调用的墙钟预算（秒）。**必须有**：重试次数 × 响应超时的乘积很容易
# 变成小时级（4 次 × 120s + 退避 = 8 分钟/样本），批处理会跑到无法接受。
# 预算耗尽即停止重试并把最后一次错误抛给上层，让调用方尽快看到失败。
_DEFAULT_CALL_BUDGET_S = 90.0


def _retry_attempts() -> int:
    """从环境变量读取重试次数（总尝试次数，含首次）。"""
    try:
        return max(1, min(int(os.environ.get("LLM_MAX_ATTEMPTS", "4")), 8))
    except ValueError:
        return _DEFAULT_RETRY_ATTEMPTS


def _call_budget_s() -> float:
    """单次调用的总墙钟预算（秒）。"""
    try:
        return max(5.0, min(float(os.environ.get("LLM_CALL_BUDGET_SECONDS", "90")), 600.0))
    except ValueError:
        return _DEFAULT_CALL_BUDGET_S


def _http_timeout_s() -> float:
    """单次 HTTP 请求超时（秒）。

    必须与"重试次数 × 预算"一起看：三者相乘才是最坏耗时。
    原来的 120s 配合 3 次重试意味着单个样本最坏 6 分钟以上，
    批处理会慢到不可用。
    """
    try:
        return max(5.0, min(float(os.environ.get("LLM_HTTP_TIMEOUT_SECONDS", "60")), 300.0))
    except ValueError:
        return 60.0


# 思考深度可选项。Responses 协议用 reasoning.effort；Anthropic 协议用
# thinking.budget_tokens，两者语义不同，故分开映射。
_REASONING_EFFORTS = ("minimal", "low", "medium", "high")

# effort → Anthropic thinking 预算（token）。仅供 anthropic 协议使用。
_REASONING_BUDGET_TOKENS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
}


def _reasoning_effort() -> str:
    """从 LLM_REASONING_EFFORT 读取思考深度；空值表示不显式指定。"""
    raw = os.environ.get("LLM_REASONING_EFFORT", "").strip().lower()
    return raw if raw in _REASONING_EFFORTS else ""

@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list = field(default_factory=list)
    usage: dict | None = None
    @classmethod
    def from_anthropic(cls, raw: dict) -> "LLMResponse":
        import re as _re
        tcs: list[dict] = []
        text = ""
        for b in raw.get("content",[]):
            if b["type"]=="text":
                block_text = b.get("text","")
                # DeepSeek v4 sometimes uses XML format for tool calls in text content
                # Parse <invoke name="X"><parameter name="Y">V</parameter></invoke>
                xml_pattern = _re.compile(
                    r'<invoke\s+name="(\w+)">(.*?)</invoke>', _re.DOTALL)
                param_pattern = _re.compile(
                    r'<parameter\s+name="(\w+)">(.*?)</parameter>', _re.DOTALL)
                last_end = 0
                clean_text = ""
                for m in xml_pattern.finditer(block_text):
                    clean_text += block_text[last_end:m.start()]
                    tool_name = m.group(1)
                    params_str = m.group(2)
                    args = {}
                    for pm in param_pattern.finditer(params_str):
                        args[pm.group(1)] = pm.group(2).strip()
                    if args:
                        tcs.append({"id": f"xml_{len(tcs)}", "name": tool_name, "args": args})
                    last_end = m.end()
                clean_text += block_text[last_end:]
                if clean_text.strip():
                    text += clean_text
            elif b["type"]=="tool_use":
                tcs.append({"id":b.get("id",""),"name":b.get("name",""),"args":b.get("input",{})})
        return cls(content=text or None, tool_calls=tcs, usage=raw.get("usage"))

    @classmethod
    def from_responses(cls, raw: dict) -> "LLMResponse":
        """Normalize an OpenAI Responses-compatible payload to the harness contract."""
        text_parts = []
        tool_calls = []
        for item in raw.get("output", []) or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                for content in item.get("content", []) or []:
                    if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                        if content.get("text"):
                            text_parts.append(content["text"])
            elif item_type == "function_call":
                raw_args = item.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        raw_args = {}
                tool_calls.append({
                    "id": item.get("call_id") or item.get("id", ""),
                    "name": item.get("name", ""),
                    "args": raw_args if isinstance(raw_args, dict) else {},
                })
        # Some compatible gateways expose the text shortcut in addition to output[].
        shortcut = raw.get("output_text")
        if not shortcut and isinstance(raw.get("text"), dict):
            shortcut = raw["text"].get("value")
        if shortcut and not text_parts:
            text_parts.append(shortcut)
        return cls(content="".join(text_parts) or None,
                   tool_calls=tool_calls,
                   usage=raw.get("usage"))

# Module-level cache shared across all clients
try:
    from src.harness.llm_cache import LLMCache
    _llm_cache = LLMCache(max_size=200, ttl_seconds=300)
except ImportError:
    # 同上的运行期兼容分支：与绝对导入同名会被判为重复定义。
    from harness.llm_cache import LLMCache  # type: ignore[no-redef]
    _llm_cache = LLMCache(max_size=200, ttl_seconds=300)


def _retry_delay(attempt: int, error=None) -> float:
    """瞬时故障的重试等待时间。

    优先使用上游给的 `Retry-After`：不遵守它会在网关已经过载时继续加压
    （实测表现为"第一条成功、后续连续 502"）。没有该头时用指数退避。
    """
    if error is not None:
        header = ""
        try:
            header = (error.headers.get("Retry-After") or "").strip()
        except AttributeError:
            header = ""
        if header:
            try:
                return max(0.0, min(float(header), 60.0))
            except ValueError:
                pass
    return 1.0 * (2 ** attempt)          # 1s, 2s（attempt 从 0 起）


class AnthropicClient:
    # Model-specific max output token limits (fallback for known models)
    _MODEL_DEFAULT_MAX_TOKENS = {
        # Responses gateway models used for evaluation only need a short
        # judgment/tool turn; keep latency and cost bounded by default.
        "gpt-5.5": 1024,
        "gpt-5.6Luna": 1024,
        "gpt-5.6-luna": 1024,
        "claude-3-5-sonnet-20241022": 8192,
        "claude-3-5-haiku-20241022": 4096,
        "claude-3-7-sonnet-20250219": 8192,
        "claude-3-haiku-20240307": 4096,
    }

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, temperature: float = 0.1, use_cache: bool = True,
                 max_tokens: int | None = None):
        try:
            from src.config import get_api_key, get_base_url, get_model
        except ImportError:  # 同上：兼容以 src 为工作目录的运行方式
            from config import (  # type: ignore[no-redef]
                get_api_key, get_base_url, get_model)
        configured_base_url = get_base_url().rstrip("/")
        self.api_key = api_key or get_api_key()
        self.base_url = (base_url or configured_base_url).rstrip("/")
        self.model = model or get_model()
        self.temperature = temperature
        self.use_cache = use_cache
        configured_protocol = os.environ.get("LLM_API_PROTOCOL", "anthropic").strip().lower()
        # Explicit test/custom endpoints retain the legacy Anthropic protocol;
        # the configured production endpoint opts into its declared protocol.
        self.api_protocol = (configured_protocol
                             if self.base_url == configured_base_url
                             else "anthropic")
        # Configurable per client/model; falls back to model-specific default
        self.max_tokens = max_tokens or self._MODEL_DEFAULT_MAX_TOKENS.get(self.model, 16384)
        # 思考深度（reasoning effort）。实测只有 Responses 协议会回报
        # reasoning_tokens，因此它也是唯一能证明"高思考度真的生效"的路径：
        #   effort low → reasoning_tokens 59；effort high → 109。
        # Anthropic 协议的 thinking 参数被网关接受但不回报用量，无法验证，
        # 所以两条协议用各自的原生写法，而不是硬套一个参数名。
        self.reasoning_effort = _reasoning_effort()

    @staticmethod
    def _tool_def(t: dict) -> dict:
        """Safely normalize a tool definition; tolerates malformed dicts / function=None."""
        fn = t.get("function") if isinstance(t, dict) else None
        if not isinstance(fn, dict):
            fn = t if isinstance(t, dict) else {}
        return {
            "name": str(fn.get("name") or t.get("name") or "unknown_tool"),
            "description": str(fn.get("description") or ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        }

    def _cache_variant(self) -> str:
        """参与缓存键的请求参数指纹。

        只包含会改变结果的参数：协议与思考深度。model 已单独作为键的一部分。
        少了它就会出现"改了 reasoning_effort 却拿到旧深度答案"的静默错配。
        """
        return f"{self.api_protocol}|effort={self.reasoning_effort or '-'}"

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        if self.api_protocol == "responses":
            return self._chat_responses(messages, tools)
        import urllib.request
        import urllib.error
        # Check cache
        if self.use_cache and not tools:
            cached = _llm_cache.get(messages, self.model, self._cache_variant())
            if cached:
                try:
                    obj = json.loads(cached)
                    if isinstance(obj, dict) and ("tool_calls" in obj or "content" in obj):
                        return LLMResponse(content=obj.get("content"),
                                           tool_calls=obj.get("tool_calls") or [],
                                           usage=obj.get("usage"))
                except (json.JSONDecodeError, TypeError):
                    pass
                return LLMResponse(content=cached)
        sp, apimsg = self._convert(messages)
        ant = None
        if tools:
            ant = [self._tool_def(t) for t in tools]
        payload = {"model":self.model,"max_tokens":self.max_tokens,"messages":apimsg}
        if self.reasoning_effort:
            # Anthropic 协议的原生写法是 thinking.budget_tokens。
            # ⚠️ 实测该网关接受此参数但不回报 reasoning 用量，因此无法从
            # 响应验证是否真的生效——要可验证的高思考度请用 responses 协议。
            thinking_budget = _REASONING_BUDGET_TOKENS[self.reasoning_effort]
            payload["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            # thinking 会占用输出预算，max_tokens 必须留出余量，否则正文被挤空
            payload["max_tokens"] = max(self.max_tokens, thinking_budget + 1024)
        if sp:
            # Prompt caching: system prompt rarely changes, mark as cacheable
            payload["system"] = [{"type":"text","text":sp,"cache_control":{"type":"ephemeral"}}]
        if ant:
            # Mark last tool definition as cache breakpoint
            for i, t in enumerate(ant):
                if i == len(ant) - 1:
                    t["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = ant
        req = urllib.request.Request(
            url=f"{self.base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type":"application/json","x-api-key":self.api_key,
                     "anthropic-version":"2023-06-01",
                     "anthropic-beta":"prompt-caching-2024-07-31",
                     # 部分网关（含 Cloudflare 前置）按 User-Agent 过滤，
                     # urllib 默认 UA 会被直接拒绝（error code 1010 / HTTP 403）。
                     # responses 路径早已设置该头，anthropic 路径此前遗漏，
                     # 导致同一网关下"responses 能用、anthropic 403"的怪现象。
                     "User-Agent": _HTTP_USER_AGENT},
            method="POST")
        # 瞬时故障（限流 429 / 服务端 5xx / 过载 529）重试，退避 1s、2s、4s…；
        # 其余 HTTP 错误（4xx 等）与连接类错误不重试，直接抛给上层展示。
        transient_codes = (429, 500, 502, 503, 504, 529)
        attempts = _retry_attempts()
        budget = _call_budget_s()
        deadline = time.monotonic() + budget
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=_http_timeout_s()) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                    result = LLMResponse.from_anthropic(raw)
                    # Save to cache (text-only and tool-call-only responses)
                    if self.use_cache and not tools and (result.content or result.tool_calls):
                        _llm_cache.set(messages, json.dumps(
                            {"content": result.content, "tool_calls": result.tool_calls,
                             "usage": result.usage}, ensure_ascii=False), self.model, self._cache_variant())
                    return result
            except urllib.error.HTTPError as e:
                delay = _retry_delay(attempt, e)
                if (e.code in transient_codes and attempt < attempts - 1
                        and time.monotonic() + delay < deadline):
                    time.sleep(delay)
                    continue
                suffix = ("" if e.code not in transient_codes
                          else f" (已尝试 {attempt + 1} 次/预算 {budget:.0f}s)")
                raise RuntimeError(
                    f"API HTTP {e.code}: {e.read().decode('utf-8',errors='replace')}{suffix}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"API connection error: {e.reason}")
            except (TimeoutError, ConnectionError, OSError) as e:
                raise RuntimeError(f"API timeout/connection error: {e}")
        # 重试循环理论上必然在 return/raise 处退出；这里显式兜底，
        # 避免出现"静默返回 None"的调用方崩溃（也让类型检查确认返回类型）。
        raise RuntimeError(f"API request failed after {attempts} attempts")

    def _chat_responses(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """Call a Responses-compatible gateway while preserving the existing client API."""
        import urllib.request
        import urllib.error

        if self.use_cache and not tools:
            cached = _llm_cache.get(messages, self.model, self._cache_variant())
            if cached:
                try:
                    obj = json.loads(cached)
                    if isinstance(obj, dict) and ("tool_calls" in obj or "content" in obj):
                        return LLMResponse(content=obj.get("content"),
                                           tool_calls=obj.get("tool_calls") or [],
                                           usage=obj.get("usage"))
                except (json.JSONDecodeError, TypeError):
                    pass
                return LLMResponse(content=cached)

        instructions, input_items = self._convert_responses(messages)
        payload = {
            "model": self.model,
            "input": input_items,
            "max_output_tokens": self.max_tokens,
        }
        # 思考深度：显式配置优先；未配置时保留 gpt-5.5 的历史默认（low，
        # 因为它在评测里只做短判定）。显式配置会覆盖该默认。
        if self.reasoning_effort:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        elif self.model == "gpt-5.5":
            payload["reasoning"] = {"effort": "low"}
        if instructions:
            payload["instructions"] = instructions
        if tools:
            payload["tools"] = [self._responses_tool_def(t) for t in tools]

        req = urllib.request.Request(
            url=f"{self.base_url}/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}",
                     # 网关边缘过滤会拒绝 urllib 默认 UA
                     "User-Agent": _HTTP_USER_AGENT},
            method="POST")
        transient_codes = (429, 500, 502, 503, 504, 529)
        attempts = _retry_attempts()
        budget = _call_budget_s()
        deadline = time.monotonic() + budget
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=_http_timeout_s()) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                    result = LLMResponse.from_responses(raw)
                    if self.use_cache and not tools and (result.content or result.tool_calls):
                        _llm_cache.set(messages, json.dumps(
                            {"content": result.content, "tool_calls": result.tool_calls,
                             "usage": result.usage}, ensure_ascii=False), self.model, self._cache_variant())
                    return result
            except urllib.error.HTTPError as e:
                delay = _retry_delay(attempt, e)
                if (e.code in transient_codes and attempt < attempts - 1
                        and time.monotonic() + delay < deadline):
                    time.sleep(delay)
                    continue
                suffix = ("" if e.code not in transient_codes
                          else f" (已尝试 {attempt + 1} 次/预算 {budget:.0f}s)")
                raise RuntimeError(
                    f"API HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}{suffix}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"API connection error: {e.reason}")
            except (TimeoutError, ConnectionError, OSError) as e:
                raise RuntimeError(f"API timeout/connection error: {e}")
        raise RuntimeError(f"Responses API request failed after {attempts} attempts")

    @staticmethod
    def _responses_tool_def(tool: dict) -> dict:
        normalized = AnthropicClient._tool_def(tool)
        return {
            "type": "function",
            "name": normalized["name"],
            "description": normalized["description"],
            "parameters": normalized["input_schema"],
        }

    def _convert_responses(self, messages: list[dict]) -> tuple[str, list[dict]]:
        instructions = []
        items = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                content = message.get("content", "")
                if isinstance(content, str) and content.strip():
                    instructions.append(content)
            elif role in {"user", "assistant"}:
                content = message.get("content", "")
                if isinstance(content, list):
                    # The Responses API accepts typed content blocks; retain text blocks
                    # and stringify other legacy blocks rather than dropping context.
                    converted = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") in {"text", "input_text", "output_text"}:
                            converted.append({"type": "input_text" if role == "user" else "output_text",
                                              "text": block.get("text", "")})
                    content = converted or ""
                items.append({"role": role, "content": content})
                for call in message.get("tool_calls") or []:
                    fn = call.get("function", call)
                    args = fn.get("arguments", fn.get("args", {}))
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    items.append({"type": "function_call",
                                  "call_id": call.get("id", ""),
                                  "name": fn.get("name", ""),
                                  "arguments": args})
            elif role == "tool":
                items.append({"type": "function_call_output",
                              "call_id": message.get("tool_call_id", ""),
                              "output": str(message.get("content", ""))})
        return "\n\n".join(instructions), items

    async def stream(self, messages: list[dict], tools: list[dict] | None = None):
        import urllib.request
        import urllib.error
        sp, apimsg = self._convert(messages)
        ant = None
        if tools:
            ant = [self._tool_def(t) for t in tools]
        payload = {"model":self.model,"max_tokens":self.max_tokens,"messages":apimsg,"stream":True}
        if sp:
            payload["system"] = [{"type":"text","text":sp,"cache_control":{"type":"ephemeral"}}]
        if ant:
            for i, t in enumerate(ant):
                if i == len(ant) - 1:
                    t["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = ant
        def _sync():
            req = urllib.request.Request(
                url=f"{self.base_url}/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type":"application/json","x-api-key":self.api_key,
                         "anthropic-version":"2023-06-01",
                         "anthropic-beta":"prompt-caching-2024-07-31",
                         # 同 chat()：网关按 UA 过滤，必须用非 urllib 默认 UA
                         "User-Agent": _HTTP_USER_AGENT},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    buf=b""
                    while True:
                        data=resp.read(4096)
                        if not data: break
                        buf+=data
                        while b"\n" in buf:
                            line,buf=buf.split(b"\n",1)
                            line=line.strip()
                            if line.startswith(b"data: "):
                                s=line[6:].decode("utf-8")
                                try:
                                    for c in self._parse_stream(json.loads(s)): yield c
                                except json.JSONDecodeError: pass
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"API HTTP {e.code}: {e.read().decode('utf-8',errors='replace')}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"API connection error: {e.reason}")
            except (TimeoutError, ConnectionError, OSError) as e:
                raise RuntimeError(f"API timeout/connection error: {e}")
        gen=_sync()
        loop=asyncio.get_running_loop()
        while True:
            chunk=await loop.run_in_executor(None,lambda: next(gen,None))
            if chunk is None: break
            yield chunk

    def _parse_stream(self, data: dict):
        t=data.get("type","")
        if t=="content_block_start":
            b=data.get("content_block",{})
            yield Chunk(type="content_block_start",content_block={"type":b.get("type","text"),"id":b.get("id",""),"name":b.get("name","")},index=data.get("index",0))
        elif t=="content_block_delta":
            d=data.get("delta",{})
            dt=d.get("type","")
            if dt=="text_delta": yield Chunk(type="content_block_delta",delta={"type":"text_delta","text":d.get("text","")},index=data.get("index",0))
            elif dt=="input_json_delta": yield Chunk(type="content_block_delta",delta={"type":"input_json_delta","partial_json":d.get("partial_json","")},index=data.get("index",0))
        elif t=="content_block_stop": yield Chunk(type="content_block_stop",index=data.get("index",0))
        elif t=="message_start":
            msg=data.get("message",{})
            yield Chunk(type="message_start",index=0,usage=msg.get("usage") or {})
        elif t=="message_delta":
            d=data.get("delta",{})
            yield Chunk(type="message_delta",delta={"type":"message_delta","stop_reason":d.get("stop_reason","")},index=0,usage=data.get("usage") or {})
        elif t=="message_stop": yield Chunk(type="message_stop",usage=data.get("usage") or {})
        elif t=="ping":
            yield Chunk(type="ping",index=0)
        elif t=="error":
            err=data.get("error",{})
            raise RuntimeError(f"Streaming API error: {err.get('type','')}: {err.get('message','')}")

    def _convert(self, messages: list[dict]) -> tuple:
        sp_parts, api = [], []
        i=0
        while i<len(messages):
            m=messages[i]
            if m["role"]=="system":
                content=m.get("content","")
                if isinstance(content,str) and content.strip():
                    sp_parts.append(content)
                i+=1
            elif m["role"]=="assistant": api.append(self._conv_asst(m)); i+=1
            elif m["role"]=="user": api.append({"role":"user","content":m["content"]}); i+=1
            elif m["role"]=="tool":
                results=[]
                while i<len(messages) and messages[i]["role"]=="tool":
                    t=messages[i]
                    results.append({"type":"tool_result","tool_use_id":t.get("tool_call_id",""),"content":t.get("content","")})
                    i+=1
                api.append({"role":"user","content":results})
            else: i+=1
        return "\n\n".join(sp_parts), api

    def _conv_asst(self, m: dict) -> dict:
        tcs=m.get("tool_calls") or []
        text=m.get("content")
        if not tcs: return {"role":"assistant","content":text or ""}
        blocks=[]
        if text: blocks.append({"type":"text","text":text})
        for tc in tcs:
            fn=tc.get("function",tc)
            raw_args = fn.get("arguments", fn.get("args", {}))
            # arguments 可能是 JSON 字符串（OpenAI 格式），需要解析成 dict
            if isinstance(raw_args, str):
                try: raw_args = json.loads(raw_args)
                except json.JSONDecodeError: raw_args = {}
            blocks.append({"type":"tool_use","id":tc.get("id",""),"name":fn.get("name",""),"input":raw_args})
        return {"role":"assistant","content":blocks}
