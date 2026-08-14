"""Streaming Parser: state machine for parsing tool call JSON from streaming chunks"""
from __future__ import annotations
import json

class Chunk:
    def __init__(self, type: str, content_block=None, delta=None, index=0, usage=None):
        self.type = type
        self.content_block = content_block or {}
        self.delta = delta or {}
        self.index = index
        self.usage = usage

class StreamingParser:
    def __init__(self):
        self.state = "IDLE"
        self._current_block_type: str | None = None
        self._text_buffer = ""
        self._json_buffer = ""
        self._tool_name: str | None = None
        self._tool_id: str | None = None
        self._tool_calls: list[dict] = []

    def feed(self, chunk) -> list[dict]:
        events = []
        def _get(obj, attr, default=None):
            try: return getattr(obj, attr)
            except AttributeError: return obj.get(attr, default) if isinstance(obj, dict) else default
        ct = _get(chunk, "type", "")
        if ct == "content_block_start":
            events.extend(self._handle_start(chunk, _get))
        elif ct == "content_block_delta":
            events.extend(self._handle_delta(chunk, _get))
        elif ct == "content_block_stop":
            events.extend(self._handle_stop(chunk, _get))
        elif ct == "message_delta":
            pass
        elif ct == "message_stop":
            events.append({"type":"done","text":self._text_buffer,
                           "tool_calls":self._tool_calls,"usage":_get(chunk,"usage",None)})
        return events

    def _handle_start(self, chunk, _get) -> list:
        block = _get(chunk, "content_block", {})
        bt = block.get("type","text") if isinstance(block,dict) else getattr(block,"type","text")
        self._current_block_type = bt
        if bt == "tool_use":
            self.state = "TOOL_OPEN"
            self._tool_name = block.get("name","unknown") if isinstance(block,dict) else getattr(block,"name","unknown")
            self._tool_id = block.get("id","unknown") if isinstance(block,dict) else getattr(block,"id","unknown")
            self._json_buffer = ""
        else:
            self.state = "TEXT_OPEN"
        return []

    def _handle_delta(self, chunk, _get) -> list:
        events = []
        delta = _get(chunk, "delta", {})
        if self._current_block_type == "text":
            text = delta.get("text","") if isinstance(delta,dict) else getattr(delta,"text","")
            self._text_buffer += text
            if text: events.append({"type":"text_chunk","text":text})
        elif self._current_block_type == "tool_use":
            pj = delta.get("partial_json","") if isinstance(delta,dict) else getattr(delta,"partial_json","")
            self._json_buffer += pj
        return events

    def _handle_stop(self, chunk, _get) -> list:
        events = []
        if self._current_block_type == "tool_use":
            try:
                args = json.loads(self._json_buffer) if self._json_buffer else {}
                tc = {"id":self._tool_id,"name":self._tool_name,"args":args}
                self._tool_calls.append(tc)
                events.append({"type":"tool_call_ready","tool_call":tc})
            except json.JSONDecodeError as e:
                events.append({"type":"parse_error","message":str(e)})
        self.state = "IDLE"; self._current_block_type = None
        return events
