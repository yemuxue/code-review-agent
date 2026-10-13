"""请求级上下文 / Request-scoped context.

解决的问题（企业级审计发现）
    系统回答不了"**谁**在**什么时候**对**哪个文件**做了什么"：
    - 遥测 JSONL 记录里没有 actor 字段（`telemetry.py` 只写 ts/event/...）；
    - `AgentLogger("./logs")` 由 API 构造时不传用户，日志无主体；
    - 没有任何请求 ID 贯穿日志、遥测与错误响应；
    - 登录成功/失败、写操作审批都零审计。

做法：把"当前请求是谁、针对哪个项目、请求号是多少"放进 contextvar，
由入口（FastAPI 中间件 / CLI）设置一次，其余各层按需读取而无需层层传参。
contextvar 是**异步安全**的：并发请求各自持有自己的上下文副本，
不会像模块级全局变量那样在协程切换时互相污染。

用法：
    with request_context(request_id="abc", actor="alice", project_path="/repo"):
        ...        # 期间创建的任何 AgentLogger 都会自动带上这些字段

CLI 等非 HTTP 入口可以直接调用 `set_context(...)`，未设置时字段为空，
不会影响既有日志格式（空值不写入）。
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    """一次请求/一次运行的归属信息。"""

    request_id: str = ""
    actor: str = ""          # 用户名；匿名/CLI 时为空
    project_path: str = ""   # 本次分析的目标项目根

    def as_fields(self) -> dict:
        """转成遥测字段（空值不写，保持旧日志格式兼容）。"""
        fields = {}
        if self.request_id:
            fields["request_id"] = self.request_id
        if self.actor:
            fields["actor"] = self.actor
        if self.project_path:
            fields["project_path"] = self.project_path
        return fields


_EMPTY = RequestContext()
_current: ContextVar[RequestContext] = ContextVar("dsh_request_context", default=_EMPTY)


def new_request_id() -> str:
    """生成请求 ID（短、可读、无连字符，便于在日志里 grep）。"""
    return uuid.uuid4().hex[:12]


def set_context(*, request_id: str = "", actor: str = "",
                project_path: str = ""):
    """设置当前上下文并返回可用于 reset 的 token。

    ⚠️ 必须用返回的 token 调 `reset_context(token)` 恢复，
    否则上下文会泄漏到同一线程/事件循环的后续请求。
    """
    context = RequestContext(request_id=request_id, actor=actor, project_path=project_path)
    return _current.set(context)


def get_context() -> RequestContext:
    """读取当前上下文（未设置时返回全空对象，绝不返回 None）。"""
    return _current.get()


def update_context(**kwargs) -> RequestContext:
    """在当前上下文基础上覆盖部分字段（例如拿到 project_path 后再补）。"""
    current = _current.get()
    merged = RequestContext(
        request_id=kwargs.get("request_id", current.request_id),
        actor=kwargs.get("actor", current.actor),
        project_path=kwargs.get("project_path", current.project_path),
    )
    _current.set(merged)
    return merged


def reset_context(token) -> None:
    """恢复到 set_context 之前的状态（配合 token 使用）。"""
    _current.reset(token)


@contextmanager
def request_context(*, request_id: str = "", actor: str = "", project_path: str = ""):
    """上下文管理器形式：退出时自动恢复，适合测试与 CLI 使用。"""
    if not request_id:
        request_id = new_request_id()
    token = _current.set(RequestContext(request_id=request_id, actor=actor,
                                        project_path=project_path))
    try:
        yield _current.get()
    finally:
        _current.reset(token)
