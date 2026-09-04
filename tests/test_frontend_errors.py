"""前端错误提示的回归测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app.frontend_errors import format_frontend_error


def test_network_permission_error_has_actionable_chinese_message():
    result = format_frontend_error(
        RuntimeError("API connection error: [WinError 10013] access denied")
    )

    assert "WinError 10013" in result
    assert "网络" in result
    assert "日志" in result
    assert "Traceback" not in result


def test_unknown_error_keeps_type_without_rendering_traceback():
    result = format_frontend_error(ValueError("bad configuration"))

    assert "ValueError" in result
    assert "bad configuration" in result
    assert "Traceback" not in result


def test_connection_error_without_10013_keeps_real_reason():
    """DNS 解析失败等连接故障 ≠ 权限问题：不得误报为 WinError 10013，须直显真实原因。"""
    result = format_frontend_error(
        RuntimeError("API connection error: <urlopen error [Errno 11001] getaddrinfo failed>")
    )

    assert "getaddrinfo failed" in result   # 真实原因直显
    assert "WinError 10013" not in result   # 不被误判为沙箱/权限问题
    assert "Traceback" not in result


def test_timeout_connection_error_not_mapped_to_10013():
    result = format_frontend_error(RuntimeError("API timeout/connection error: timed out"))

    assert "timed out" in result
    assert "WinError 10013" not in result
    assert "Traceback" not in result
