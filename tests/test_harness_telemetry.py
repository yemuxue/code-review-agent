"""Phase 0 遥测补全的端到端回归：工具事件全链路 + 多 Agent 角色归因。

守护三件事：
1. tool_call_start 曾被完全漏记（历史日志 0 条），每次工具调用必须有 start/end 配对；
2. 失败路径（未知工具 / HITL 拦截 / 参数不全 / 工具抛异常）必须落 failure_kind；
3. 多 Agent 运行必须写角色标签事件 + node_stats（此前只有 session_start）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.harness.agent import AgentHarness, ToolDefinition
from src.harness.telemetry import AgentLogger
from src.llm_client import LLMResponse
from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator


def _read_events(logger: AgentLogger) -> list[dict]:
    text = Path(logger.log_path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _events(logger: AgentLogger, name: str) -> list[dict]:
    return [e for e in _read_events(logger) if e["event"] == name]


class ToolCallingMockLLM:
    """首轮返回预设 tool_calls，次轮返回最终答案。"""

    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.call_count = 0

    def chat(self, messages, tools=None, **kwargs):
        self.call_count += 1
        if self.call_count == 1:
            return LLMResponse(content=None, tool_calls=self.tool_calls, usage={})
        return LLMResponse(content="done", usage={})


def _tool_calls(*calls) -> list[dict]:
    return [{"id": f"c{i}", "name": name, "args": args}
            for i, (name, args) in enumerate(calls)]


def _agent(tools, tool_calls, logger) -> AgentHarness:
    return AgentHarness(model=ToolCallingMockLLM(tool_calls), tools=tools,
                        system_prompt="test", max_turns=3, logger=logger)


# ═══ 工具事件全链路 ═══

def test_every_tool_call_has_matched_start_and_end(tmp_path):
    logger = AgentLogger(str(tmp_path))
    tools = [ToolDefinition("read", "Read", {"properties": {}, "required": ["path"]},
                            lambda **kw: f"content:{kw}")]
    _agent(tools, _tool_calls(("read", {"path": "a.py"})), logger).run("go")

    starts, ends = _events(logger, "tool_call_start"), _events(logger, "tool_call_end")
    assert len(starts) == len(ends) == 1
    assert starts[0]["tool"] == "read" and starts[0]["args_ok"] is True
    assert starts[0]["args_fingerprint"]
    assert ends[0]["error"] is False


def test_unknown_tool_is_classified(tmp_path):
    logger = AgentLogger(str(tmp_path))
    _agent([], _tool_calls(("nope", {})), logger).run("go")

    end = _events(logger, "tool_call_end")[0]
    assert end["error"] is True and end["failure_kind"] == "unknown_tool"
    # 未知工具没有 schema 可校验 → args_ok 不臆断
    assert "args_ok" not in _events(logger, "tool_call_start")[0]


def test_tool_exception_is_classified(tmp_path):
    def boom(**kwargs):
        raise ZeroDivisionError("division by zero")

    logger = AgentLogger(str(tmp_path))
    tools = [ToolDefinition("read", "Read", {"properties": {}, "required": ["path"]}, boom)]
    result = _agent(tools, _tool_calls(("read", {"path": "a.py"})), logger).run("go")

    assert "Tool error" in result or result == "done"
    end = _events(logger, "tool_call_end")[0]
    assert end["failure_kind"] == "tool_exception"
    assert _events(logger, "error")[0]["error_type"] == "ZeroDivisionError"


def test_missing_required_arg_is_observed_but_not_blocked(tmp_path):
    logger = AgentLogger(str(tmp_path))
    tools = [ToolDefinition("read", "Read", {"properties": {}, "required": ["path"]},
                            lambda **kw: "content")]
    _agent(tools, _tool_calls(("read", {"wrong": 1})), logger).run("go")

    start = _events(logger, "tool_call_start")[0]
    assert start["args_ok"] is False
    assert "path" in start["args_error"]
    # 纯观测：调用照常发生（行为与改造前一致）
    assert _events(logger, "tool_call_end")[0]["error"] is False


def test_hitl_blocked_call_is_logged(tmp_path):
    class DenyAll:
        def needs_approval(self, name, args):
            return True

        def request_approval(self, name, args):
            return False

    logger = AgentLogger(str(tmp_path))
    tools = [ToolDefinition("run_command", "Run", {"properties": {}}, lambda **kw: "ran")]
    agent = _agent(tools, _tool_calls(("run_command", {"command": "rm -rf /"})), logger)
    agent.hitl = DenyAll()
    result = agent.run("go")

    assert result == "done"
    ends = _events(logger, "tool_call_end")
    assert len(ends) == 1
    assert ends[0]["failure_kind"] == "hitl_blocked"
    assert ends[0]["error"] is True


# ═══ 多 Agent 角色归因 ═══

class RecordingMockLLM:
    """按用户消息路由的离线 mock，跑通 plan → execute → review。"""

    def chat(self, messages, tools=None, **kwargs):
        user_content = str(messages[-1]["content"])
        if user_content.startswith("Task: "):
            content = "FINDING|src/target.py|42|BUG|High|EN: none check|CN: 缺少检查|add check"
        elif "Verify finding #" in user_content:
            content = "VERDICT|1|CONFIRMED|line 42"
        else:
            content = "# Code Analysis Report\n1 finding"
        return LLMResponse(content=content, usage={})


def test_multi_agent_run_writes_role_tagged_events(tmp_path):
    logger = AgentLogger(str(tmp_path))
    orch = LangGraphOrchestrator(RecordingMockLLM(), [], skills_dir=str(tmp_path / "missing"),
                                 logger=logger)
    orch.run(task="review target.py", project_path=str(tmp_path))

    events = _read_events(logger)
    roles = {e.get("role") for e in events if e.get("role")}
    assert {"planner", "executor", "reviewer"} <= roles
    assert all(e.get("role") is None for e in events if e["event"] == "session_start")
    # 角色事件确实产生在视图上，且 end 配对
    assert len(_events(logger, "tool_call_start")) >= len(_events(logger, "tool_call_end"))


def test_node_stats_and_run_summary_are_persisted(tmp_path):
    logger = AgentLogger(str(tmp_path))
    orch = LangGraphOrchestrator(RecordingMockLLM(), [], skills_dir=str(tmp_path / "missing"),
                                 logger=logger)
    result = orch.run(task="review target.py", project_path=str(tmp_path))

    stats_event = _events(logger, "node_stats")
    assert len(stats_event) == 1
    assert stats_event[0]["stats"].keys() == result["node_stats"].keys()
    assert {"plan", "execute_1", "review"} == set(stats_event[0]["stats"])

    end = _events(logger, "session_end")
    assert len(end) == 1 and end[0]["complete"] is True and end[0]["nodes"] == 3


def test_parse_result_events_capture_protocol_markers(tmp_path):
    logger = AgentLogger(str(tmp_path))
    LangGraphOrchestrator(RecordingMockLLM(), [], skills_dir=str(tmp_path / "missing"),
                          logger=logger).run(task="review target.py", project_path=str(tmp_path))

    parsed = {(e["role"], e["kind"]): e for e in _events(logger, "parse_result")}
    assert parsed[("planner", "findings")]["count"] == 1
    assert parsed[("planner", "findings")]["marker"] is True
    assert parsed[("executor", "verdict")]["count"] == 1
    assert parsed[("executor", "verdict")]["ok"] is True


def test_marker_without_parse_is_recorded_as_failure(tmp_path):
    """含协议标记但解析 0 条 = 格式失败（F-01），与"本就没发现问题"必须区分。"""
    class BrokenFormatMockLLM(RecordingMockLLM):
        def chat(self, messages, tools=None, **kwargs):
            user_content = str(messages[-1]["content"])
            if user_content.startswith("Task: "):
                # FINDING| 标记残缺（字段不足）→ 解析不出任何 finding
                return LLMResponse(content="FINDING|src/a.py|42|BUG", usage={})
            return super().chat(messages, tools=tools, **kwargs)

    logger = AgentLogger(str(tmp_path))
    LangGraphOrchestrator(BrokenFormatMockLLM(), [], skills_dir=str(tmp_path / "missing"),
                          logger=logger).run(task="review a.py", project_path=str(tmp_path))

    planner = [e for e in _events(logger, "parse_result") if e["role"] == "planner"][0]
    assert planner["marker"] is True and planner["ok"] is False and planner["count"] == 0


def test_orchestrator_without_logger_writes_nothing(tmp_path):
    """logger=None（默认）→ 不创建日志目录/文件，改造前行为零变化。"""
    before = set(Path(tmp_path).glob("*.jsonl"))
    orch = LangGraphOrchestrator(RecordingMockLLM(), [], skills_dir=str(tmp_path / "missing"))
    orch.run(task="review target.py", project_path=str(tmp_path))
    assert set(Path(tmp_path).glob("*.jsonl")) == before
    assert orch.logger is None
