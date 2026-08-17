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
