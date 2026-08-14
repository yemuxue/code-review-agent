"""
Telemetry / Logger — Agent Harness 第六组件：日志与追踪

结构化 JSON Lines 日志，记录：
    - 每轮 LLM 调用 (turn_start / turn_end)
    - 每次工具调用 (tool_call / tool_result)
    - 错误 (error)
    - Token 消耗 (usage)

设计原则：
    - 非侵入式：AgentHarness 通过回调注册，不需要修改核心逻辑
    - JSON Lines：每行一条 JSON，方便 grep/jq/管道分析
    - 分级：INFO（正常流程）/ WARN（重试/降级）/ ERROR（异常）
"""

from __future__ import annotations

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
    """

    def __init__(self, log_dir: str = "logs"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = _make_session_id()
        self._log_path = self._log_dir / f"{self._session_id}.jsonl"
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._event_count = 0

        # 初始化写入
        self._write("session_start", {
            "session_id": self._session_id,
            "timestamp": _iso_now(),
        })

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

    def tool_call_start(self, turn: int, tool_name: str, args: dict):
        self._write("tool_call_start", {
            "turn": turn,
            "tool": tool_name,
            "args": _truncate_dict(args),
        })

    def tool_call_end(self, turn: int, tool_name: str, result: str, duration_ms: float, error: bool = False):
        self._write("tool_call_end", {
            "turn": turn,
            "tool": tool_name,
            "duration_ms": round(duration_ms, 1),
            "result_len": len(result),
            "result_preview": result[:200],
            "error": error,
        })

    def error(self, turn: int, error_type: str, message: str):
        self._write("error", {
            "turn": turn,
            "error_type": error_type,
            "message": message[:500],
        })

    def finish(self, stats: dict):
        elapsed = time.time() - self._start_time
        self._write("session_end", {
            "elapsed_s": round(elapsed, 1),
            "events": self._event_count,
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

    def __enter__(self):
        self._start = time.time()
        self._logger.tool_call_start(self._turn, self._tool_name, self._args)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = (time.time() - self._start) * 1000
        if exc_type:
            self._logger.tool_call_end(self._turn, self._tool_name,
                                        str(exc_val), duration, error=True)
        return False  # 不吞异常

    def execute(self) -> str:
        """执行工具并记录"""
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

def _truncate_dict(d: dict, max_len: int = 200) -> dict:
    """截断 dict 中过长的值"""
    if not isinstance(d, dict):
        return str(d)[:max_len]
    result = {}
    for k, v in d.items():
        s = str(v)
        result[k] = s[:max_len] + ("..." if len(s) > max_len else "")
    return result
