"""Agent Harness core: Execution Loop + Tool Calling + Context Management"""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class ToolCall:
    id: str
    name: str
    args: dict

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict
    fn: Callable

class AgentHarness:
    def __init__(self, model, tools: list[ToolDefinition], system_prompt: str,
                 max_turns: int = 50, logger=None):
        self.model = model
        self.tools = {t.name: t for t in tools}
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.messages: list[dict] = []
        self.turns_taken = 0
        self.tools_called = 0
        self.total_tokens_used = 0
        self.logger = logger
        self.sandbox: Any = None
        self.hitl: Any = None
        self.memory: Any = None

    def run(self, user_query: str) -> str:
        self._reset(user_query)
        for turn in range(self.max_turns):
            self._pre_turn(turn)
            response = self.model.chat(messages=self.messages, tools=self._get_tool_schemas())
            self._accumulate_usage(response)  # ← 累计 token
            result = self._process_turn_response(response, turn)
            if result is not None:
                return result
        return self._force_finish()

    async def run_streaming(self, user_query: str):
        from .streaming import StreamingParser
        self._reset(user_query)
        for turn in range(self.max_turns):
            parser = StreamingParser()
            self._pre_turn(turn)
            turn_tool_calls = []
            async for chunk in self.model.stream(messages=self.messages, tools=self._get_tool_schemas()):
                for event in parser.feed(chunk):
                    if event["type"] == "text_chunk":
                        yield event  # ← 真正逐字流式：每个 chunk 立即 yield
                    elif event["type"] == "tool_call_ready":
                        tc = ToolCall(**event["tool_call"])
                        turn_tool_calls.append(tc)
                        yield {"type":"tool_call_detected","name":tc.name}
                    elif event["type"] == "done":
                        if turn_tool_calls:
                            self._execute_tool_calls(turn_tool_calls)
                            yield {"type":"tools_executed","count":len(turn_tool_calls)}
                            break
                        else:
                            # 流式结束：累计 usage（message_stop 事件携带）
                            usage = getattr(chunk, 'usage', None)
                            if usage:
                                self._accumulate_usage(type('R', (), {'usage': usage})())
                            yield {"type":"finished","text":event["text"]}
                            return
        yield {"type":"finished","text":self._force_finish()}

    def _pre_turn(self, turn: int):
        """每轮开始前：记忆压缩 + 日志（run/run_streaming 共享）"""
        self.turns_taken = turn + 1
        if hasattr(self, 'memory') and self.memory and self.memory.should_compact(self.messages):
            self.messages = self.memory.compact(self.messages)
        if self.logger:
            self.logger.turn_start(turn + 1, len(self.messages))

    def _accumulate_usage(self, response) -> None:
        """从 LLM response 的 usage 字段累计 token 消耗"""
        usage = getattr(response, 'usage', None)
        if not usage:
            return
        input_tokens = usage.get('input_tokens', 0) if isinstance(usage, dict) else 0
        output_tokens = usage.get('output_tokens', 0) if isinstance(usage, dict) else 0
        self.total_tokens_used += int(input_tokens) + int(output_tokens)

    def _process_turn_response(self, response, turn: int) -> str | None:
        """处理 LLM 响应：工具调用 → 执行；文本 → 返回"""
        has_tools = bool(response.tool_calls)
        if has_tools:
            self._execute_tool_calls(response.tool_calls)
            if self.logger:
                self.logger.turn_end(turn + 1, has_tools)
            return None  # 继续循环
        else:
            self.messages.append({"role":"assistant","content":response.content})
            if self.logger:
                self.logger.turn_end(turn + 1, has_tools, getattr(response, 'usage', None))
                self.logger.finish(self.get_stats())
            return response.content  # 最终答案

    def _reset(self, user_query: str):
        self.messages = [{"role":"system","content":self.system_prompt},
                         {"role":"user","content":user_query}]
        self.turns_taken = 0; self.tools_called = 0

    def _execute_tool_calls(self, tool_calls: list):
        normalized = [ToolCall(**tc) if isinstance(tc, dict) else tc for tc in tool_calls]
        self.messages.append({
            "role":"assistant","content":None,
            "tool_calls":[{"id":tc.id,"type":"function",
                          "function":{"name":tc.name,"arguments":json.dumps(tc.args,ensure_ascii=False)}}
                         for tc in normalized],
        })
        for tc in normalized:
            result = self._execute_single_tool(tc)
            self.messages.append({"role":"tool","tool_call_id":tc.id,"content":str(result)})
            self.tools_called += 1

    def _execute_single_tool(self, tc: ToolCall) -> str:
        import time
        start = time.time()
        try:
            tool = self.tools.get(tc.name)
            if tool is None:
                result = f"Tool '{tc.name}' not found."
                if self.logger:
                    self.logger.tool_call_end(self.turns_taken, tc.name, result, (time.time()-start)*1000, error=True)
                return result
            # HITL check
            if hasattr(self, 'hitl') and self.hitl and self.hitl.needs_approval(tc.name, tc.args):
                if not self.hitl.request_approval(tc.name, tc.args):
                    return f"Tool '{tc.name}' blocked by Human-in-the-Loop guard."
            # Sandbox: run_command 类工具在沙箱中执行
            if hasattr(self, 'sandbox') and self.sandbox and tc.name == 'run_command':
                cmd = tc.args.get('command', '')
                if isinstance(cmd, str):
                    import shlex
                    cmd = shlex.split(cmd)
                sb_result = self.sandbox.run(cmd)
                result = sb_result.summary()
            else:
                result = str(tool.fn(**tc.args))
            if self.logger:
                self.logger.tool_call_end(self.turns_taken, tc.name, result, (time.time()-start)*1000)
            return result
        except Exception as e:
            result = f"Tool error: {type(e).__name__}: {e}"
            if self.logger:
                self.logger.error(self.turns_taken, type(e).__name__, str(e))
                self.logger.tool_call_end(self.turns_taken, tc.name, result, (time.time()-start)*1000, error=True)
            return result

    def _get_tool_schemas(self) -> list[dict]:
        return [{"type":"function","function":{"name":t.name,"description":t.description,"parameters":t.parameters}}
                for t in self.tools.values()]

    def _force_finish(self) -> str:
        last = self.messages[-1] if self.messages else None
        if last and last.get("role") == "assistant" and last.get("tool_calls"):
            for tc in last["tool_calls"]:
                self.messages.append({"role":"tool","tool_call_id":tc.get("id","unknown"),
                                       "content":"Not executed (max turns reached)."})
        self.messages.append({"role":"user","content":"Max turns reached. Give your best answer now. No more tools."})
        resp = self.model.chat(messages=self.messages, tools=[])
        return resp.content or "Agent stopped."

    def get_stats(self) -> dict:
        return {"turns_taken":self.turns_taken,"tools_called":self.tools_called,
                "total_tokens_used":self.total_tokens_used,"messages_count":len(self.messages)}
