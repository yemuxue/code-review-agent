"""Streamlit 前端的可操作错误提示。"""
from __future__ import annotations

_API_CONNECTION_PREFIXES = (
    "API connection error",          # llm_client URLError（DNS/拒连/权限）
    "API timeout/connection error",  # llm_client Timeout/Connection/OSError
)


def format_frontend_error(error: BaseException) -> str:
    """把底层异常转换为不暴露堆栈、但能指导用户处理的中文消息。"""
    detail = str(error)
    if "WinError 10013" in detail:
        # 操作系统层拒绝出站连接：最常见于受限启动上下文（如沙箱/服务内启动），
        # 提示用户修复运行环境而非重试模型。
        return (
            "❌ 模型 API 网络连接被操作系统拒绝（WinError 10013）。\n\n"
            "请在允许访问模型 API 的终端中启动 Streamlit，并检查防火墙、代理和网络权限。"
            "原始堆栈已写入本次运行日志。"
        )
    if detail.startswith(_API_CONNECTION_PREFIXES):
        # 其余连接类故障（DNS 解析失败、超时、连接被拒等）并非权限问题：
        # 直显真实原因，避免被误报为沙箱/权限导致用户修错方向。
        return (
            "❌ 模型 API 网络请求失败，无法连接模型服务。\n\n"
            "模型服务可能暂时不可用，或当前网络/代理不通。请稍后重试；持续失败时检查："
            "1) API 服务状态与余额；2) 代理与防火墙；3) 若从受限终端启动，改用可联网终端。\n\n"
            f"详情：{detail}\n\n原始堆栈已写入本次运行日志。"
        )
    return f"❌ 多代理流程失败：{type(error).__name__}: {detail}"
