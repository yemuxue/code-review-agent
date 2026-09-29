"""
Telemetry / Logger — Agent Harness 第六组件：日志与追踪

结构化 JSON Lines 日志，记录：
    - 每轮 LLM 调用 (turn_start / turn_end)
    - 每次工具调用 (tool_call_start / tool_call_end)
    - 多 Agent 角色归于 (role 字段 / for_role 视图)
    - 节点级统计 (node_stats) 与结构化输出解析结果 (parse_result)
    - 错误 (error)
    - Token 消耗 (usage)

设计原则：
    - 非侵入式：AgentHarness 通过回调注册，不需要修改核心逻辑
    - JSON Lines：每行一条 JSON，方便 grep/jq/管道分析
    - 分级：INFO（正常流程）/ WARN（重试/降级）/ ERROR（异常）
    - 一次运行一个文件：多 Agent 各角色通过 for_role() 共享同一 session 文件，
      每条事件带 role 字段（单 Agent 路径不传 role → 事件形态与改造前逐字节一致）
"""

from __future__ import annotations

import hashlib
import json
import time
import threading
from pathlib import Path
from typing import Callable


class AgentLogger:
    """
    结构化日志器。

    用法：
        logger = AgentLogger("logs/session_001.jsonl")
        agent = AgentHarness(model, tools, system_prompt, logger=logger)
        agent.run("...")
        # 所有日志自动写入

    多 Agent 用法（同一 session 文件，事件带 role 标签）：
        logger = AgentLogger("logs")
        orch = LangGraphOrchestrator(..., logger=logger)
        # 内部：logger.for_role("planner") → 事件带 "role": "planner"
    """

    def __init__(self, log_dir: str = "logs", role: str | None = None):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = _make_session_id()
        self._log_path = self._log_dir / f"{self._session_id}.jsonl"
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._event_count = 0
        self._role = role
        self._parent: AgentLogger | None = None

        # 初始化写入
        self._write("session_start", {
            "session_id": self._session_id,
            "timestamp": _iso_now(),
        })

    def for_role(self, role: str) -> "AgentLogger":
        """返回共享同一 JSONL 文件 / 锁 / session 的带角色视图。

        视图不新建 session、不写 session_start；其事件统一带 role 字段，
        计数器与 elapsed 归属主 logger。role 为空时返回自身（单 Agent 路径零开销）。
        """
        if not role:
            return self
        view = object.__new__(AgentLogger)
        view._parent = self._parent or self
        view._role = role
        view._log_dir = self._log_dir
        view._session_id = self._session_id
        view._log_path = self._log_path
        view._lock = self._lock
        view._start_time = self._start_time
        view._event_count = 0  # 由主 logger 统一计数
        return view

    # ─── 公开 API ────────────────────────────────────

    def turn_start(self, turn: int, messages_count: int):
        self._write("turn_start", {
            "turn": turn,
            "messages_count": messages_count,
        })

    def turn_end(self, turn: int, has_tool_calls: bool, token_usage: dict | None = None):
        self._write("turn_end", {
            "turn": turn,
            "has_tool_calls": has_tool_calls,
            "usage": token_usage,
        })

    def tool_call_start(self, turn: int, tool_name: str, args: dict,
                        args_ok: bool | None = None, args_error: str | None = None):
        data = {
            "turn": turn,
            "tool": tool_name,
            "args": _truncate_dict(args),
            # 规范 JSON 的 sha1：即使 args 预览被截断，仍可判"同工具同参数重复调用"
            "args_fingerprint": _args_fingerprint(args),
        }
        if args_ok is not None:
            data["args_ok"] = args_ok
        if args_error:
            data["args_error"] = args_error
        self._write("tool_call_start", data)

    def tool_call_end(self, turn: int, tool_name: str, result: str, duration_ms: float,
                      error: bool = False, failure_kind: str | None = None):
        data = {
            "turn": turn,
            "tool": tool_name,
            "duration_ms": round(duration_ms, 1),
            "result_len": len(result),
            "result_preview": result[:200],
            "error": error,
        }
        if failure_kind:
            # unknown_tool / tool_exception / args_invalid / hitl_blocked
            data["failure_kind"] = failure_kind
        self._write("tool_call_end", data)

    def parse_result(self, kind: str, ok: bool, count: int = 0, marker: bool | None = None):
        """结构化输出解析结果（Plan 可执行率 / VERDICT 解析率 / fix 解析率数据源）。

        kind: findings / verdict / fix；只记录成败与条目数，不落 LLM 全文。
        marker: 原始输出是否含协议标记（含标记但解析 0 条 = 格式失败 F-01）。
        """
        data = {"kind": kind, "ok": bool(ok), "count": int(count)}
        if marker is not None:
            data["marker"] = bool(marker)
        self._write("parse_result", data)

    def node_stats(self, stats: dict):
        """节点级统计快照：run 结束时一次性落盘，供轨迹指标消费。"""
        self._write("node_stats", {"stats": stats})

    def forced_finish(self, turn: int, max_turns: int):
        """turn 预算耗尽的兜底收尾（agent 已被强制要求"不再调工具，直接作答"）。

        显式留痕而非靠 node_end 缺失反推：兜底产出质量下降，是需要被计数的路径。
        """
        self._write("forced_finish", {"turn": turn, "max_turns": max_turns})

    def error(self, turn: int, error_type: str, message: str):
        # 错误事件罕见且常带 traceback，根因在栈尾 —— 不截断，保持与
        # "原始堆栈已写入本次运行日志" 的 UI 承诺一致（截断曾吞掉真正的异常消息）。
        self._write("error", {
            "turn": turn,
            "error_type": error_type,
            "message": message,
        })

    def finish(self, stats: dict):
        """会话结束统计。主 logger → session_end；角色视图 → node_end（节点级）。

        多 Agent 链路中每个节点 agent 结束都会调 finish，用 node_end 区分，
        避免一个文件里出现 N 条 session_end 干扰"端到端是否跑完"的判定。
        """
        elapsed = time.time() - self._start_time
        if self._parent is None:
            self._write("session_end", {
                "elapsed_s": round(elapsed, 1),
                "events": self._event_count,
                **stats,
            })
        else:
            self._write("node_end", {
                "elapsed_s": round(elapsed, 1),
                **stats,
            })

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def log_path(self) -> str:
        return str(self._log_path)

    # ─── 内部 ────────────────────────────────────────

    def _write(self, event: str, data: dict):
        parent = self._parent
        if parent is not None:
            # 角色视图：合并 role 后交给主 logger 落盘（共享同一文件与锁）
            parent._write(event, {**data, "role": self._role})
            return
        if self._role and "role" not in data:
            data = {**data, "role": self._role}
        record = {
            "ts": _iso_now(),
            "event": event,
            **data,
        }
        with self._lock:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._event_count += 1


class TimedToolCall:
    """上下文管理器：自动记录工具调用耗时并写入日志"""

    def __init__(self, logger: AgentLogger, turn: int, tool_name: str, args: dict, fn: Callable):
        self._logger = logger
        self._turn = turn
        self._tool_name = tool_name
        self._args = args
        self._fn = fn
        self._started = False

    def __enter__(self):
        self._start = time.time()
        self._started = True
        self._logger.tool_call_start(self._turn, self._tool_name, self._args)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (time.time() - self._start) * 1000
        if exc_type:
            self._logger.tool_call_end(self._turn, self._tool_name,
                                        str(exc_val), duration, error=True)
        return False  # 不吞异常

    def execute(self) -> str:
        """执行工具并记录（若已在 __enter__ 记录过 start，不重复记录）"""
        if not self._started:
            self._logger.tool_call_start(self._turn, self._tool_name, self._args)
        start = time.time()
        error = False
        try:
            result = self._fn(**self._args)
            return str(result)
        except Exception as e:
            error = True
            result = f"Tool error: {type(e).__name__}: {e}"
            return result
        finally:
            duration = (time.time() - start) * 1000
            self._logger.tool_call_end(self._turn, self._tool_name,
                                        result, duration, error=error)


# ─── 辅助函数 ───────────────────────────────────────

def _make_session_id() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{_random_suffix(4)}"

def _random_suffix(n: int) -> str:
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

def _args_fingerprint(args: dict) -> str:
    """工具参数指纹：规范 JSON（键排序）的 sha1 前 16 位，跨进程稳定。

    用于"同工具同参数重复调用（空转）"检测 —— args 预览被截断也不影响判定。
    """
    try:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        canonical = str(args)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]

def _truncate_dict(d: dict, max_len: int = 200) -> dict:
    """截断 dict 中过长的值"""
    if not isinstance(d, dict):
        return str(d)[:max_len]
    result = {}
    for k, v in d.items():
        s = str(v)
        result[k] = s[:max_len] + ("..." if len(s) > max_len else "")
    return result
