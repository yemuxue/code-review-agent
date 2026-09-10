"""评测指标库测试（Phase 1）：旧日志降级 / 指标真值 / 失败分类 / 离线端到端。

关键立场：数据源缺失的指标必须是 n/a（None）而不是 0——测不到 ≠ 零故障。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eval.log_parser import invalid_json_ratio, load_logs, parse_log_file
from src.eval.metrics import (
    classify_failures,
    compute_all,
    e2e_success_rate,
    flaky_rate,
    idle_turn_rate,
    incomplete_runs,
    loop_rate,
    node_efficiency,
    pass_at_k,
    plan_parse_rate,
    tool_success_rate,
    args_valid_rate,
)


def _write_log(tmp_path: Path, name: str, events: list[dict]) -> Path:
    path = tmp_path / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                    encoding="utf-8")
    return path


def _tool_end(turn: int, tool: str = "read_file", error: bool = False, **extra) -> dict:
    return {"event": "tool_call_end", "turn": turn, "tool": tool,
            "duration_ms": 1.0, "result_len": 3, "result_preview": "ok",
            "error": error, **extra}


# ═══ 解析层：旧日志降级 ═══

def test_legacy_log_without_p0_fields_parses_and_degrades(tmp_path):
    path = _write_log(tmp_path, "legacy", [
        {"event": "session_start", "session_id": "legacy"},
        {"event": "turn_start", "turn": 1, "messages_count": 2},
        _tool_end(1),
        {"event": "turn_end", "turn": 1, "has_tool_calls": True, "usage": None},
        {"event": "turn_start", "turn": 2, "messages_count": 5},
    ])
    run = parse_log_file(path)
    assert run.has_roles is False and run.has_tool_start is False
    assert run.has_node_stats is False and run.is_multi_agent is False
    # 无 start 的老日志：配对为 (None, end)，不丢事件
    assert [e["tool"] for _, e in run.tool_events()] == ["read_file"]
    assert run.tool_events()[0][0] is None


def test_dirty_lines_are_counted_not_fatal(tmp_path):
    path = tmp_path / "dirty.jsonl"
    path.write_text('{"event":"session_start"}\nnot json\n{"no_event":1}\n',
                    encoding="utf-8")
    run = parse_log_file(path)
    assert len(run.events) == 1 and run.bad_lines == 2
    assert invalid_json_ratio([run]) == 2 / 3


def test_missing_dir_returns_empty():
    assert load_logs("definitely/not/here") == []


# ═══ 轨迹指标真值 ═══

def test_tool_success_rate_counts_error_flag(tmp_path):
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        _tool_end(1), _tool_end(2), _tool_end(3, error=True, failure_kind="tool_exception"),
    ])
    metric = tool_success_rate([parse_log_file(path)])
    assert metric.value == 2 / 3
    assert "tool_exception×1" in metric.note


def test_args_valid_rate_na_without_p0_data(tmp_path):
    path = _write_log(tmp_path, "r1", [{"event": "session_start"}])
    metric = args_valid_rate([parse_log_file(path)])
    assert metric.value is None and metric.denominator == 0  # 不用 0 冒充


def test_parse_rates_are_role_scoped(tmp_path):
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        {"event": "parse_result", "role": "planner", "kind": "findings", "ok": True, "count": 2},
        {"event": "parse_result", "role": "planner", "kind": "findings", "ok": False,
         "count": 0, "marker": True},
        {"event": "parse_result", "role": "executor", "kind": "verdict", "ok": True, "count": 1},
    ])
    runs = [parse_log_file(path)]
    assert plan_parse_rate(runs).value == 0.5
    assert classify_failures(runs)["F-01"] != []  # marker=True + ok=False → 真·格式失败


def test_idle_turn_rate_ignores_terminal_turn(tmp_path):
    """空闲判定：无工具产出 + 后面还有轮次 = 空转；终结轮不算。"""
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        {"event": "turn_start", "turn": 1}, _tool_end(1),
        {"event": "turn_start", "turn": 2},                       # 空转（无工具，后面还有轮）
        {"event": "turn_start", "turn": 3}, _tool_end(3),
        {"event": "turn_start", "turn": 4},                       # 终结轮（无工具但无后续）
    ])
    metric = idle_turn_rate([parse_log_file(path)])
    assert metric.numerator == 1 and metric.denominator == 4


def test_idle_turn_rate_handles_restarted_turn_numbers(tmp_path):
    """同一文件多次运行（turn 号从 1 重来）时，上一段的末轮不得被判为空转。"""
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        {"event": "turn_start", "turn": 1}, _tool_end(1),
        {"event": "turn_start", "turn": 2},                       # 第一段终结轮
        {"event": "turn_start", "turn": 1}, _tool_end(1),         # 第二次运行
        {"event": "turn_start", "turn": 2},                       # 第二次运行的终结轮
    ])
    assert idle_turn_rate([parse_log_file(path)]).numerator == 0


def test_idle_turn_rate_uses_turn_end_flag_without_tool_events(tmp_path):
    """P0 前的老日志没有 tool_call_start；turn_end.has_tool_calls 也算工具产出证据。"""
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        {"event": "turn_start", "turn": 1},
        {"event": "turn_end", "turn": 1, "has_tool_calls": True},
        {"event": "turn_start", "turn": 2},
        {"event": "turn_end", "turn": 2, "has_tool_calls": False},  # 空转
        {"event": "turn_start", "turn": 3},
    ])
    metric = idle_turn_rate([parse_log_file(path)])
    assert metric.numerator == 1 and metric.denominator == 3


def test_loop_rate_needs_fingerprints(tmp_path):
    looped = _write_log(tmp_path, "looped", [
        {"event": "session_start"},
        *[{"event": "tool_call_start", "turn": i, "tool": "grep_pattern",
           "args": {}, "args_fingerprint": "abc"} for i in range(3)],
    ])
    varied = _write_log(tmp_path, "varied", [
        {"event": "session_start"},
        *[{"event": "tool_call_start", "turn": i, "tool": "grep_pattern",
           "args": {}, "args_fingerprint": f"fp{i}"} for i in range(3)],
    ])
    assert loop_rate([parse_log_file(looped)]).value == 1.0
    assert loop_rate([parse_log_file(varied)]).value == 0.0
    assert loop_rate([parse_log_file(varied)]).denominator == 1


def test_e2e_rate_treats_missing_complete_as_implicit_success(tmp_path):
    """单 Agent 路径只写 session_end（无 complete 字段）→ 视为正常收尾，不算失败。"""
    ok = _write_log(tmp_path, "single_agent", [
        {"event": "session_start"},
        {"event": "session_end", "elapsed_s": 1.0, "events": 1, "turns_taken": 2},
    ])
    failed = _write_log(tmp_path, "bad", [
        {"event": "session_start"},
        {"event": "session_end", "complete": False},
    ])
    hole = _write_log(tmp_path, "interrupted", [{"event": "session_start"}])
    metric = e2e_success_rate([parse_log_file(p) for p in (ok, failed, hole)])
    assert metric.value == 1 / 3
    # 中断的运行属采集缺口，不是失败分类
    assert incomplete_runs([parse_log_file(hole)])[0]["run"] == "interrupted"


# ═══ 失败分类 ═══

def test_failure_taxonomy_mapping(tmp_path):
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        _tool_end(1, "ghost", error=True, failure_kind="unknown_tool"),
        _tool_end(2, "read_file", error=True, failure_kind="args_invalid"),
        _tool_end(3, "write_file", error=True, failure_kind="tool_exception"),
        _tool_end(4, "run_command", error=True, failure_kind="hitl_blocked"),
        {"event": "error", "turn": 5, "error_type": "RuntimeError",
         "message": "API HTTP 503: overloaded"},
    ])
    buckets = classify_failures([parse_log_file(path)])
    assert [len(buckets[c]) for c in ("F-02", "F-03", "F-04", "F-05")] == [1, 1, 1, 1]


def test_tool_error_and_error_event_not_double_counted(tmp_path):
    """同一 (run, turn) 的 tool_call_end(error) 与 error 事件只计一次。"""
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        _tool_end(2, "read_file", error=True, failure_kind="tool_exception"),
        {"event": "error", "turn": 2, "error_type": "TypeError",
         "message": "read_file() got an unexpected keyword argument 'offset'"},
    ])
    buckets = classify_failures([parse_log_file(path)])
    assert len(buckets["F-04"]) == 1


def test_legacy_api_error_classified_by_stack_frame(tmp_path):
    """老日志 500 字截断，栈尾根因不可见 → 按栈帧（HTTP 层）归 F-05 并标注截断。"""
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        {"event": "error", "turn": 3, "error_type": "RuntimeError",
         "message": 'Traceback (most recent call last):\n  File "C:\\...\\urllib\\request.py", '
                    'line 1321, in do_open\n    h.request(...)' + "x" * 600},
    ])
    entry = classify_failures([parse_log_file(path)])["F-05"][0]
    assert entry["run"] == "r1" and "截断" in entry["detail"]


def test_incomplete_runs_are_not_failure_taxonomy(tmp_path):
    path = _write_log(tmp_path, "r1", [{"event": "session_start"}])
    buckets = classify_failures([parse_log_file(path)])
    assert "F-08" not in buckets  # 采集缺口不得伪装成"状态污染"


# ═══ 效率 / 稳定性 ═══

def test_node_efficiency_aggregates_and_percentiles(tmp_path):
    """runs 计的是"多少个运行包含该节点"，不是 node_stats 事件数。"""
    runs = []
    for idx, elapsed in enumerate((100, 200, 300, 400)):
        runs.append(parse_log_file(_write_log(tmp_path, f"r{idx}", [
            {"event": "session_start"},
            {"event": "node_stats", "stats": {"plan": {
                "turns": 2, "tools": 1, "messages": 3, "tokens": 10, "elapsed_ms": elapsed}}},
        ])))
    stats = node_efficiency(runs)["plan"]
    assert stats["runs"] == 4 and stats["tokens"] == 40 and stats["tokens_per_run"] == 10.0
    assert stats["elapsed_p50_ms"] == 250  # 偶数样本取中间两值均值
    assert stats["elapsed_p95_ms"] == 400


def test_stability_metrics_need_repeats():
    assert pass_at_k([]).value is None and flaky_rate([]).value is None
    assert pass_at_k([[True, True], [True, False]]).value == 0.5
    assert flaky_rate([[True, True], [True, False], [False, False]]).value == 1 / 3


def test_fix_metrics_from_session_end_status(tmp_path):
    path = _write_log(tmp_path, "r1", [
        {"event": "session_start"},
        {"event": "session_end", "complete": True,
         "fix_status": {"FIXED": 3, "FAILED": 1, "ROLLED_BACK": 1}},
    ])
    data = compute_all([parse_log_file(path)])
    by_name = {m.name: m for m in data.trajectory}
    assert by_name["fix 成功率"].value == 0.75
    assert by_name["回归引入率"].value == 0.2  # 1 回滚 / 5 条修复记录


# ═══ 端到端：离线编排器产出真实格式日志 → 全部 P0 指标可算 ═══

def test_offline_orchestrator_log_runs_full_metric_pipeline(tmp_path):
    """离线跑一次多 Agent 全链路 → 全部 P0 指标都能从真实格式日志算出来。"""
    from src.harness.agent import ToolDefinition
    from src.harness.telemetry import AgentLogger
    from src.llm_client import LLMResponse
    from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator

    class MockLLM:
        """planner 先调一次工具再给 FINDING；executor/reviewer 直接给文本。"""

        def chat(self, messages, tools=None, **kwargs):
            last = messages[-1]
            if last.get("role") == "tool":
                content = "FINDING|src/a.py|7|BUG|High|EN: bad|CN: 坏|fix it"
            elif str(last["content"]).startswith("Task: "):
                return LLMResponse(content=None, usage={}, tool_calls=[
                    {"id": "c1", "name": "grep_pattern", "args": {"pattern": "x", "path": "."}}])
            elif "Verify finding #" in str(last["content"]):
                content = "VERDICT|1|CONFIRMED|line 7"
            else:
                content = "# Code Analysis Report\n1 finding"
            return LLMResponse(content=content, usage={})

    tools = [ToolDefinition("grep_pattern", "search",
                            {"properties": {"pattern": {"type": "string"}},
                             "required": ["pattern", "path"]},
                            lambda **kw: "src/a.py:7: match")]
    logs_dir = tmp_path / "logs"
    LangGraphOrchestrator(MockLLM(), tools, skills_dir=str(tmp_path / "none"),
                          logger=AgentLogger(str(logs_dir))).run(
        task="review a.py", project_path=str(tmp_path))

    data = compute_all(load_logs(logs_dir))
    by_name = {m.name: m for m in data.trajectory}
    assert by_name["端到端成功率"].value == 1.0
    assert by_name["工具调用成功率"].value == 1.0
    assert by_name["参数合法率"].value == 1.0        # P0 后新日志才有分母
    assert by_name["Plan 可执行率"].value == 1.0
    assert by_name["VERDICT 解析率"].value == 1.0
    assert by_name["循环率"].value == 0.0
    assert by_name["空转率"].value == 0.0
    assert data.failures == {}
    assert data.coverage["with_roles"] == 1 and data.coverage["multi_agent"] == 1
    assert data.coverage["incomplete"] == 0
    assert data.tokens["total_tokens"] == 0  # mock 不带 usage，不虚构成本
    # auto_fix 未开启 → 修复/验证节点不参与，节点统计如实只含跑过的节点
    assert {"plan", "execute_1", "review"} == set(data.node_efficiency)
