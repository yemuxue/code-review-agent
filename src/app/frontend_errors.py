"""Streamlit 前端的可操作错误提示。"""
from __future__ import annotations


def format_frontend_error(error: BaseException) -> str:
    """把底层异常转换为不暴露堆栈、但能指导用户处理的中文消息。"""
    detail = str(error)
    if "WinError 10013" in detail or "API connection error" in detail:
        # 网络权限错误最常见于受限启动上下文，提示用户修复运行环境而非重试模型。
        return (
            "❌ 模型 API 网络连接被操作系统拒绝（WinError 10013）。\n\n"
            "请在允许访问模型 API 的终端中启动 Streamlit，并检查防火墙、代理和网络权限。"
            "原始堆栈已写入本次运行日志。"
        )
    return f"❌ 多代理流程失败：{type(error).__name__}: {detail}"
