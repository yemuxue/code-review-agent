"""LLM Client adapter: Anthropic API"""
from __future__ import annotations
import json
import asyncio
from dataclasses import dataclass, field
try:
    from src.harness.streaming import Chunk
except ImportError:
    from harness.streaming import Chunk

@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list = field(default_factory=list)
    usage: dict | None = None
    @classmethod
    def from_anthropic(cls, raw: dict) -> "LLMResponse":
        import re as _re
        tcs, text = [], ""
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

# Module-level cache shared across all clients
try:
    from src.harness.llm_cache import LLMCache
    _llm_cache = LLMCache(max_size=200, ttl_seconds=300)
except ImportError:
    from harness.llm_cache import LLMCache
    _llm_cache = LLMCache(max_size=200, ttl_seconds=300)


class AnthropicClient:
    # Model-specific max output token limits (fallback for known models)
    _MODEL_DEFAULT_MAX_TOKENS = {
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
        except ImportError:
            from config import get_api_key, get_base_url, get_model
        self.api_key = api_key or get_api_key()
        self.base_url = (base_url or get_base_url()).rstrip("/")
        self.model = model or get_model()
        self.temperature = temperature
        self.use_cache = use_cache
        # Configurable per client/model; falls back to model-specific default
        self.max_tokens = max_tokens or self._MODEL_DEFAULT_MAX_TOKENS.get(self.model, 16384)

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

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        import urllib.request
        import urllib.error
        # Check cache
        if self.use_cache and not tools:
            cached = _llm_cache.get(messages, self.model)
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
                     "anthropic-version":"2023-06-01","anthropic-beta":"prompt-caching-2024-07-31"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                result = LLMResponse.from_anthropic(raw)
                # Save to cache (text-only and tool-call-only responses)
                if self.use_cache and not tools and (result.content or result.tool_calls):
                    _llm_cache.set(messages, json.dumps(
                        {"content": result.content, "tool_calls": result.tool_calls,
                         "usage": result.usage}, ensure_ascii=False), self.model)
                return result
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"API HTTP {e.code}: {e.read().decode('utf-8',errors='replace')}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"API connection error: {e.reason}")
        except (TimeoutError, ConnectionError, OSError) as e:
            raise RuntimeError(f"API timeout/connection error: {e}")

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
                         "anthropic-version":"2023-06-01","anthropic-beta":"prompt-caching-2024-07-31"},
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
