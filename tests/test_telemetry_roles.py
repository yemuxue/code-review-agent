"""AgentLogger 多角色遥测（Phase 0）回归测试。

覆盖：for_role 视图共享文件 / role 标签 / node_end 与 session_end 区分 /
node_stats 与 parse_result 事件 / 参数指纹稳定性 / 旧调用形态逐字节不变。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.harness.telemetry import AgentLogger


def _read_events(logger: AgentLogger) -> list[dict]:
    text = Path(logger.log_path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ═══ for_role 视图 ═══

def test_for_role_tags_events_and_shares_single_file(tmp_path):
    logger = AgentLogger(str(tmp_path))
    planner = logger.for_role("planner")
    executor = logger.for_role("executor")

    planner.turn_start(1, 2)
    planner.tool_call_start(1, "read_file", {"file_path": "a.py"})
    executor.turn_start(1, 2)
    planner.error(1, "ValueError", "boom")
    logger.turn_start(1, 1)  # 主 logger（无角色）

    events = _read_events(logger)
    # 一个 session 一个文件：视图不新建 session
    assert sum(1 for e in events if e["event"] == "session_start") == 1
    assert len(list(Path(tmp_path).glob("*.jsonl"))) == 1
    assert planner.log_path == logger.log_path

    by_role = {e["event"]: e.get("role") for e in events if e["event"] != "session_start"}
    assert by_role["turn_start"] is None  # 主 logger 事件不带 role
    assert [e.get("role") for e in events if e["event"] == "turn_start"] == ["planner", "executor", None]
    assert all(e.get("role") == "planner"
               for e in events if e["event"] in ("tool_call_start", "error"))


def test_for_role_empty_role_returns_self(tmp_path):
    logger = AgentLogger(str(tmp_path))
    assert logger.for_role("") is logger


def test_view_finish_emits_node_end_main_finish_emits_session_end(tmp_path):
    logger = AgentLogger(str(tmp_path))
    logger.finish({"turns_taken": 1})
    logger.for_role("reviewer").finish({"turns_taken": 2})

    events = _read_events(logger)
    assert [e["event"] for e in events] == ["session_start", "session_end", "node_end"]
    assert events[1]["events"] == 1          # session_end 保留事件计数（旧语义：不含本条）
    assert "events" not in events[2]
    assert events[2]["role"] == "reviewer"


def test_node_stats_and_parse_result_events(tmp_path):
    logger = AgentLogger(str(tmp_path))
    logger.node_stats({"plan": {"turns": 3, "tokens": 1200}})
    logger.for_role("planner").parse_result("findings", ok=False, count=0, marker=True)

    events = _read_events(logger)
    assert events[1]["event"] == "node_stats"
    assert events[1]["stats"]["plan"]["turns"] == 3
    assert events[2] == {**events[2], "event": "parse_result", "role": "planner",
                         "kind": "findings", "ok": False, "count": 0, "marker": True}


# ═══ 单 Agent 路径零回归 ═══

def test_tool_call_start_payload_unchanged_without_optional_args(tmp_path):
    logger = AgentLogger(str(tmp_path))
    logger.tool_call_start(1, "read", {"path": "x" * 300})
    logger.tool_call_end(1, "read", "ok", 12.34)

    start, end = _read_events(logger)[1:]
    # 旧字段齐全且语义不变（args 仍截断到 200 + 省略号）
    assert start["turn"] == 1 and start["tool"] == "read"
    assert start["args"]["path"] == "x" * 200 + "..."
    assert "args_ok" not in start and "args_error" not in start
    assert "role" not in start
    assert end["duration_ms"] == 12.3 and end["result_len"] == 2
    assert end["error"] is False and "failure_kind" not in end


def test_args_fingerprint_stable_and_discriminating(tmp_path):
    logger = AgentLogger(str(tmp_path))
    # 键序不同 → 同一指纹；参数不同 → 不同指纹
    logger.tool_call_start(1, "read", {"path": "a.py", "start_line": 1})
    logger.tool_call_start(1, "read", {"start_line": 1, "path": "a.py"})
    logger.tool_call_start(1, "read", {"path": "b.py", "start_line": 1})
    logger.tool_call_start(2, "read", {"path": object()})  # 不可序列化值不炸

    events = _read_events(logger)
    prints = [e["args_fingerprint"] for e in events if e["event"] == "tool_call_start"]
    assert prints[0] == prints[1] != prints[2]
    assert all(len(p) == 16 for p in prints)


def test_args_ok_and_failure_kind_are_optional(tmp_path):
    logger = AgentLogger(str(tmp_path))
    logger.tool_call_start(1, "write_file", {"content": "x"}, args_ok=False,
                           args_error="缺少必填参数: file_path")
    logger.tool_call_end(1, "write_file", "Tool error", 1.0, error=True,
                         failure_kind="args_invalid")

    start, end = _read_events(logger)[1:]
    assert start["args_ok"] is False
    assert start["args_error"] == "缺少必填参数: file_path"
    assert end["failure_kind"] == "args_invalid"


# ═══ TimedToolCall 双记 start（数据集已知缺陷） ═══

def test_timed_tool_call_context_plus_execute_logs_start_once(tmp_path):
    from src.harness.telemetry import TimedToolCall

    logger = AgentLogger(str(tmp_path))
    with TimedToolCall(logger, 1, "read", {"path": "a"}, lambda **kw: "result") as timed:
        assert timed.execute() == "result"

    starts = [e for e in _read_events(logger) if e["event"] == "tool_call_start"]
    assert len(starts) == 1
