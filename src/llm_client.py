"""LLM Client adapter: Anthropic API"""
from __future__ import annotations
import json, os, asyncio
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
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, temperature: float = 0.1, use_cache: bool = True):
        try:
            from src.config import get_api_key, get_base_url, get_model
        except ImportError:
            from config import get_api_key, get_base_url, get_model
        self.api_key = api_key or get_api_key()
        self.base_url = (base_url or get_base_url()).rstrip("/")
        self.model = model or get_model()
        self.temperature = temperature
        self.use_cache = use_cache

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        import urllib.request, urllib.error
        # Check cache
        if self.use_cache and not tools:
            cached = _llm_cache.get(messages, self.model)
            if cached:
                return LLMResponse(content=cached)
        sp, apimsg = self._convert(messages)
        ant = None
        if tools:
            ant = [{"name":t.get("function",t)["name"],"description":t.get("function",t).get("description",""),
                    "input_schema":t.get("function",t).get("parameters",{"type":"object","properties":{}})} for t in tools]
        payload = {"model":self.model,"max_tokens":16384,"messages":apimsg}
        if sp: payload["system"]=sp
        if ant: payload["tools"]=ant
        req = urllib.request.Request(
            url=f"{self.base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type":"application/json","x-api-key":self.api_key,"anthropic-version":"2023-06-01"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                result = LLMResponse.from_anthropic(raw)
                # Save to cache
                if self.use_cache and not tools and result.content:
                    _llm_cache.set(messages, result.content, self.model)
                return result
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"API HTTP {e.code}: {e.read().decode('utf-8',errors='replace')}")

    async def stream(self, messages: list[dict], tools: list[dict] | None = None):
        import urllib.request, urllib.error
        sp, apimsg = self._convert(messages)
        ant = None
        if tools:
            ant = [{"name":t.get("function",t)["name"],"description":t.get("function",t).get("description",""),
                    "input_schema":t.get("function",t).get("parameters",{"type":"object","properties":{}})} for t in tools]
        payload = {"model":self.model,"max_tokens":16384,"messages":apimsg,"stream":True}
        if sp: payload["system"]=sp
        if ant: payload["tools"]=ant
        def _sync():
            req = urllib.request.Request(
                url=f"{self.base_url}/v1/messages",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type":"application/json","x-api-key":self.api_key,"anthropic-version":"2023-06-01"},
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
        gen=_sync()
        loop=asyncio.get_event_loop()
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
        elif t=="message_stop": yield Chunk(type="message_stop",usage={})

    def _convert(self, messages: list[dict]) -> tuple:
        sp, api = "", []
        i=0
        while i<len(messages):
            m=messages[i]
            if m["role"]=="system": sp=m["content"]; i+=1
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
        return sp, api

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
