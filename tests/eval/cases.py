"""评测用例资产：ID 只增不改，故障来源与断言同处维护。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCase:
    id: str
    name: str
    layer: str
    tags: tuple[str, ...]
    setup: str
    inject: dict | None
    expect: dict
    source: str
    severity: str
    added: str


OFFLINE_CASES: tuple[EvalCase, ...] = (
    EvalCase("EV-001", "畸形工具调用不应中断任务", "trajectory", ("fault",),
             "FakeLLM 返回缺失 name 的 tool_call", {"kind": "malformed_tool_call"},
             {"failure": "F-01", "bounded": True}, "injected", "major", "2026-09-11"),
    EvalCase("EV-002", "未知工具可读降级", "trajectory", ("fault",),
             "FakeLLM 调用未注册工具", {"kind": "unknown_tool"},
             {"failure": "F-02", "bounded": True}, "injected", "major", "2026-09-11"),
    EvalCase("EV-003", "缺失必填参数可观测", "trajectory", ("fault",),
             "FakeLLM 遗漏工具必填参数", {"kind": "invalid_args"},
             {"args_ok": False}, "injected", "minor", "2026-09-11"),
    EvalCase("EV-004", "工具异常可归因", "trajectory", ("fault",),
             "工具函数抛出 RuntimeError", {"kind": "tool_exception"},
             {"failure": "F-04", "bounded": True}, "injected", "major", "2026-09-11"),
    EvalCase("EV-005", "工具超时可归因", "trajectory", ("fault",),
             "工具函数抛出 TimeoutError", {"kind": "tool_timeout"},
             {"failure": "F-04", "bounded": True}, "injected", "major", "2026-09-11"),
    EvalCase("EV-006", "模型限流可读降级", "trajectory", ("fault",),
             "模型调用抛出 HTTP 429", {"kind": "api_429"},
             {"failure": "F-05", "bounded": True}, "injected", "blocker", "2026-09-11"),
    EvalCase("EV-007", "上下文超限可归因", "trajectory", ("fault",),
             "模型调用抛出 context length 错误", {"kind": "context_limit"},
             {"failure": "F-07", "bounded": True}, "injected", "major", "2026-09-11"),
    EvalCase("EV-008", "重复调用受 turn 预算限制", "trajectory", ("fault",),
             "FakeLLM 连续返回同一工具调用", {"kind": "loop"},
             {"forced_finish": True, "max_turns": 3}, "injected", "blocker", "2026-09-11"),
    EvalCase("EV-009", "空最终回答有明确降级文案", "trajectory", ("fault",),
             "FakeLLM 返回空 content", {"kind": "empty_answer"},
             {"readable": True}, "injected", "minor", "2026-09-11"),
    EvalCase("EV-010", "畸形 FINDING 触发解析失败", "trajectory", ("fault",),
             "planner 返回字段不足的 FINDING", {"kind": "malformed_finding"},
             {"failure": "F-01"}, "injected", "major", "2026-09-11"),
)


def get_case(case_id: str) -> EvalCase:
    """按全局 ID 查找，未知 ID 立即失败以阻止悄悄跳过用例。"""
    for case in OFFLINE_CASES:
        if case.id == case_id:
            return case
    raise KeyError(f"未知评测用例: {case_id}")
