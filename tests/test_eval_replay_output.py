"""真实评测输出不得被 Windows 本地代码页中的模型字符中断。"""

from __future__ import annotations

import io
import sys

from tests.eval_llm_agent_qa import _print_progress


def test_progress_replaces_characters_unsupported_by_stdout(monkeypatch):
    class GbkOnly(io.StringIO):
        encoding = "gbk"

        def write(self, value):
            value.encode("gbk")
            return super().write(value)

    output = GbkOnly()
    monkeypatch.setattr(sys, "stdout", output)
    _print_progress("模型返回非断行连字符：\u2011")
    assert "?" in output.getvalue()
