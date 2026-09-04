"""AgentLogger error 事件保留完整堆栈（回归：曾被 message[:500] 截断吞掉根因）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.harness.telemetry import AgentLogger


def test_error_event_keeps_long_message_untruncated(tmp_path):
    logger = AgentLogger(str(tmp_path))
    long_message = ("Traceback (most recent call last):\n"
                    + ("x" * 1500)
                    + "\nRuntimeError: the real root cause lives at the tail")
    logger.error(0, "RuntimeError", long_message)

    lines = Path(logger.log_path).read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[-1])
    assert event["event"] == "error"
    # 若恢复 500 字符截断，此处必不等 → 直接锁死完整保存
    assert event["message"] == long_message


def test_error_event_keeps_traceback_tail(tmp_path):
    """UI 承诺"原始堆栈已写入本次运行日志"，栈尾（真正异常）必须逐字在档。"""
    logger = AgentLogger(str(tmp_path))
    tail = ("urllib.error.URLError: <urlopen error [WinError 10013] "
            "An attempt was made to access a socket in a way forbidden by its access permissions>")
    logger.error(3, "RuntimeError", "  File \"src/llm_client.py\", line 130\n" + tail)

    event = json.loads(Path(logger.log_path).read_text(encoding="utf-8").splitlines()[-1])
    assert event["message"].endswith(tail)
