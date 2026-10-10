"""Phase 2：通过真实 Harness/编排器运行十类离线故障注入。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.eval.log_parser import load_logs
from src.eval.metrics import classify_failures
from src.harness.agent import AgentHarness, ToolDefinition
from src.harness.telemetry import AgentLogger
from src.llm_client import LLMResponse
from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator
from tests.eval.cases import EvalCase, OFFLINE_CASES


@dataclass(frozen=True)
class FaultResult:
    case_id: str
    passed: bool
    detail: str


class ScriptedLLM:
    """每次 chat 消费一个预设响应；用于精确复现故障位置。"""

    def __init__(self, responses: list[LLMResponse | Exception]):
        self.responses = list(responses)

    def chat(self, messages, tools=None, **kwargs):
        if not self.responses:
            return LLMResponse(content="done", usage={})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _tool_calls(name: str, args: dict) -> list[dict]:
    return [{"id": "call-1", "name": name, "args": args}]


def _tools(*, timeout: bool = False) -> list[ToolDefinition]:
    def read(path: str) -> str:
        if timeout:
            raise TimeoutError("tool deadline exceeded")
        return f"read:{path}"

    return [ToolDefinition("read", "read", {"required": ["path"]}, read),
            ToolDefinition("boom", "boom", {"required": []},
                           lambda: (_ for _ in ()).throw(RuntimeError("tool failed")))]


def _run_agent(case: EvalCase, logs_dir: Path) -> tuple[str, list]:
    kind = case.inject["kind"] if case.inject else ""
    if kind == "malformed_tool_call":
        llm = ScriptedLLM([LLMResponse(content=None, tool_calls=[{"id": "bad", "args": {}}], usage={}),
                           LLMResponse(content="done", usage={})])
        agent = AgentHarness(llm, _tools(), "test", max_turns=3, logger=AgentLogger(str(logs_dir)))
    elif kind == "unknown_tool":
        llm = ScriptedLLM([LLMResponse(content=None, tool_calls=_tool_calls("ghost", {}), usage={}),
                           LLMResponse(content="done", usage={})])
        agent = AgentHarness(llm, _tools(), "test", max_turns=3, logger=AgentLogger(str(logs_dir)))
    elif kind == "invalid_args":
        llm = ScriptedLLM([LLMResponse(content=None, tool_calls=_tool_calls("read", {}), usage={}),
                           LLMResponse(content="done", usage={})])
        agent = AgentHarness(llm, _tools(), "test", max_turns=3, logger=AgentLogger(str(logs_dir)))
    elif kind == "tool_exception":
        llm = ScriptedLLM([LLMResponse(content=None, tool_calls=_tool_calls("boom", {}), usage={}),
                           LLMResponse(content="done", usage={})])
        agent = AgentHarness(llm, _tools(), "test", max_turns=3, logger=AgentLogger(str(logs_dir)))
    elif kind == "tool_timeout":
        llm = ScriptedLLM([LLMResponse(content=None, tool_calls=_tool_calls("read", {"path": "a.py"}), usage={}),
                           LLMResponse(content="done", usage={})])
        agent = AgentHarness(llm, _tools(timeout=True), "test", max_turns=3,
                             logger=AgentLogger(str(logs_dir)))
    elif kind == "api_429":
        agent = AgentHarness(ScriptedLLM([RuntimeError("API HTTP 429: rate limited")]), _tools(), "test",
                             max_turns=3, logger=AgentLogger(str(logs_dir)))
    elif kind == "context_limit":
        agent = AgentHarness(ScriptedLLM([RuntimeError("context length exceeded")]), _tools(), "test",
                             max_turns=3, logger=AgentLogger(str(logs_dir)))
    elif kind == "loop":
        repeated = LLMResponse(content=None, tool_calls=_tool_calls("read", {"path": "a.py"}), usage={})
        agent = AgentHarness(ScriptedLLM([repeated, repeated, repeated, LLMResponse(content="done", usage={})]),
                             _tools(), "test", max_turns=3, logger=AgentLogger(str(logs_dir)))
    elif kind == "empty_answer":
        agent = AgentHarness(ScriptedLLM([LLMResponse(content="", usage={})]), _tools(), "test",
                             max_turns=3, logger=AgentLogger(str(logs_dir)))
    else:
        raise ValueError(kind)
    return agent.run("evaluate"), load_logs(logs_dir)


def _run_malformed_finding(case: EvalCase, logs_dir: Path) -> list:
    class BrokenPlanner:
        def chat(self, messages, tools=None, **kwargs):
            if str(messages[-1]["content"]).startswith("Task: "):
                return LLMResponse(content="FINDING|src/a.py|7|BUG", usage={})
            return LLMResponse(content="done", usage={})

    project = logs_dir / "project"
    project.mkdir(exist_ok=True)
    LangGraphOrchestrator(BrokenPlanner(), [], skills_dir=str(project / "missing"),
                          logger=AgentLogger(str(logs_dir))).run("review a.py", str(project))
    return load_logs(logs_dir)


def run_fault_case(case: EvalCase, logs_dir: str | Path) -> FaultResult:
    """运行一条故障用例，并从真实日志核验其有界行为与归因。"""
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)
    kind = case.inject["kind"] if case.inject else ""
    if kind == "malformed_finding":
        runs = _run_malformed_finding(case, logs_path)
        output = ""
    else:
        output, runs = _run_agent(case, logs_path)
    failures = classify_failures(runs)
    expected = case.expect.get("failure")
    if expected:
        passed = bool(failures.get(expected))
        detail = f"{expected}={'命中' if passed else '未命中'}"
    elif kind == "invalid_args":
        starts = [event for run in runs for event in run.of("tool_call_start")]
        passed = any(event.get("args_ok") is False for event in starts)
        detail = "args_ok=False" if passed else "缺少参数合法率埋点"
    elif kind == "loop":
        passed = any(run.of("forced_finish") for run in runs)
        detail = "触发 forced_finish" if passed else "未触发 turn 上限"
    else:
        passed = bool(output.strip()) and "empty final answer" in output
        detail = "空回答已降级" if passed else "空回答未给出可读结果"
    return FaultResult(case.id, passed, detail)


def run_all_faults(logs_dir: str | Path) -> list[FaultResult]:
    """按稳定顺序运行十个场景，方便报告比较与 CI 定位。"""
    return [run_fault_case(case, Path(logs_dir) / case.id) for case in OFFLINE_CASES]


def run_healthy_case(logs_dir: str | Path) -> None:
    """门禁基准：只运行无故障链路，避免故障注入把成功率阈值拉低。"""
    llm = ScriptedLLM([
        LLMResponse(content=None, tool_calls=_tool_calls("read", {"path": "a.py"}), usage={}),
        LLMResponse(content="done", usage={}),
    ])
    AgentHarness(llm, _tools(), "test", max_turns=3, logger=AgentLogger(str(logs_dir))).run("health")
