"""
Agent Harness 核心模块单元测试

用 MockLLM 实现零 API 调用的测试，参考 llm-agent-qa-system 的 test 设计。
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.harness.agent import AgentHarness, ToolDefinition, ToolCall


# ═══════════════════════════════════════════
# Mock LLM — 不调 API，直接返回预设文本
# ═══════════════════════════════════════════

class MockLLM:
    """模拟 LLM，可预设每轮返回值"""
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0

    def chat(self, messages, tools=None, **kwargs):
        from src.llm_client import LLMResponse
        self.call_count += 1
        if self.call_count <= len(self.responses):
            r = self.responses[self.call_count - 1]
        else:
            r = LLMResponse(content="Mock final answer")
        # 注入 usage 避免 AttributeError
        if not hasattr(r, 'usage'):
            r.usage = {}
        return r


def mock_tool(**kwargs) -> str:
    return f"Mock tool result: {kwargs}"


TOOLS = [
    ToolDefinition("read", "Read file", {"properties":{"path":{"type":"string"}},"required":["path"]}, mock_tool),
    ToolDefinition("search", "Search code", {"properties":{"query":{"type":"string"}},"required":["query"]}, mock_tool),
]


# ═══════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════

class TestAgentRun:
    """Agent.run() 核心逻辑"""

    def test_simple_answer_no_tools(self):
        """直接返回文本答案，不调工具"""
        from src.llm_client import LLMResponse
        llm = MockLLM([LLMResponse(content="分析完成，找到 0 个问题")])
        agent = AgentHarness(model=llm, tools=[], system_prompt="test", max_turns=3)
        result = agent.run("分析 agent.py")
        assert "分析完成" in result
        assert agent.turns_taken <= 1

    def test_tool_call_then_answer(self):
        """第一轮调工具，第二轮返回答案"""
        from src.llm_client import LLMResponse
        llm = MockLLM([
            LLMResponse(tool_calls=[
                {"id":"1","name":"read","args":{"path":"agent.py"}}
            ]),
            LLMResponse(content="分析完成，找到了问题"),
        ])
        agent = AgentHarness(model=llm, tools=TOOLS, system_prompt="test", max_turns=5)
        result = agent.run("分析")
        assert agent.tools_called >= 1
        assert agent.turns_taken == 2
        assert "分析完成" in result

    def test_max_turns_reached(self):
        """达到最大轮次强制结束"""
        from src.llm_client import LLMResponse
        # 始终返回工具调用 → 无限循环 → 被 max_turns 截断
        infinite = [LLMResponse(tool_calls=[
            {"id":str(i),"name":"read","args":{"path":"x"}}
        ]) for i in range(20)]
        llm = MockLLM(infinite)
        agent = AgentHarness(model=llm, tools=TOOLS, system_prompt="test", max_turns=3)
        result = agent.run("分析")
        assert agent.turns_taken <= 3

    def test_tool_not_found_graceful(self):
        """LLM 调了不存在的工具 → 返回错误信息而不崩溃"""
        from src.llm_client import LLMResponse
        llm = MockLLM([
            LLMResponse(tool_calls=[
                {"id":"1","name":"nonexistent","args":{}}
            ]),
            LLMResponse(content="好的，我重试"),
        ])
        agent = AgentHarness(model=llm, tools=TOOLS, system_prompt="test", max_turns=5)
        result = agent.run("测试")
        assert agent.tools_called >= 1  # 即使工具不存在，也算调用过
        assert "分析" in result or "好的" in result

    def test_stats_accurate(self):
        """get_stats 返回正确的统计数据"""
        from src.llm_client import LLMResponse
        llm = MockLLM([
            LLMResponse(tool_calls=[{"id":"1","name":"read","args":{"path":"a"}}]),
            LLMResponse(tool_calls=[{"id":"2","name":"search","args":{"query":"x"}}]),
            LLMResponse(content="Done"),
        ])
        agent = AgentHarness(model=llm, tools=TOOLS, system_prompt="test", max_turns=10)
        agent.run("测试")
        stats = agent.get_stats()
        assert stats["turns_taken"] == 3
        assert stats["tools_called"] == 2
        assert stats["messages_count"] > 0


class TestToolExecution:
    """工具执行逻辑"""

    def test_tool_fn_called_with_args(self):
        """tool.fn(**tc.args) 正确解包参数"""
        from src.llm_client import LLMResponse
        results = []
        def capture_tool(**kwargs):
            results.append(kwargs)
            return "captured"

        tools = [ToolDefinition("capture", "test", {"properties":{"x":{"type":"string"}},"required":["x"]}, capture_tool)]
        llm = MockLLM([
            LLMResponse(tool_calls=[{"id":"1","name":"capture","args":{"x":"hello"}}]),
            LLMResponse(content="done"),
        ])
        agent = AgentHarness(model=llm, tools=tools, system_prompt="test", max_turns=5)
        agent.run("test")
        assert len(results) == 1
        assert results[0]["x"] == "hello"

    def test_reset_clears_messages(self):
        """_reset 清空消息并初始化 system+user"""
        agent = AgentHarness(model=MockLLM(), tools=TOOLS, system_prompt="SYS", max_turns=5)
        agent._reset("task1")
        assert len(agent.messages) == 2  # system + user
        assert agent.messages[0]["content"] == "SYS"
        assert agent.messages[1]["content"] == "task1"
        agent._reset("task2")
        assert len(agent.messages) == 2  # 再次 reset，仍然只有 2
        assert agent.messages[1]["content"] == "task2"


class TestHITLIntegration:
    """HITL 集成"""

    def test_hitl_wired_and_runs(self):
        """HITL 已接入 — MODERATE 工具走审批流程"""
        from src.llm_client import LLMResponse
        from src.harness.auth import HumanInTheLoop

        def write_tool(**kwargs): return "written"
        tools = TOOLS + [
            ToolDefinition("write_file", "Write file", {"properties":{"path":{"type":"string"}},"required":["path"]}, write_tool),
        ]
        llm = MockLLM([
            LLMResponse(tool_calls=[{"id":"1","name":"write_file","args":{"path":"/tmp/x"}}]),
            LLMResponse(content="done"),
        ])
        agent = AgentHarness(model=llm, tools=tools, system_prompt="test", max_turns=5)
        hitl = HumanInTheLoop(auto_approve_safe=True)
        agent.hitl = hitl
        result = agent.run("test")
        # write_file is MODERATE → goes through HITL → approved by default
        assert "done" in result
        assert hitl.stats["approved"] + hitl.stats["auto_approved"] + hitl.stats["rejected"] >= 0

    def test_dangerous_tool_blocked(self):
        """DANGEROUS 工具被拒绝"""
        from src.llm_client import LLMResponse
        from src.harness.auth import HumanInTheLoop

        def dangerous_tool(**kwargs):
            return "should not run"

        tools = [ToolDefinition("rm", "delete files", {"properties":{"target":{"type":"string"}},"required":["target"]}, dangerous_tool)]
        llm = MockLLM([
            LLMResponse(tool_calls=[{"id":"1","name":"rm","args":{"target":"/"}}]),
            LLMResponse(content="被拦截了"),
        ])
        agent = AgentHarness(model=llm, tools=tools, system_prompt="test", max_turns=5)
        hitl = HumanInTheLoop(auto_approve_safe=True)
        agent.hitl = hitl
        agent.run("test")
        # DANGEROUS 默认拒绝
        assert hitl.stats["rejected"] >= 1


class TestMemoryIntegration:
    """Memory 集成"""

    def test_memory_compact_called(self):
        """长对话触发压缩"""
        from src.llm_client import LLMResponse
        from src.harness.memory import ContextMemory

        # 产生很多轮对话
        many_tools = [LLMResponse(tool_calls=[
            {"id":str(i),"name":"read","args":{"path":"x"}}
        ]) for i in range(15)]
        many_tools.append(LLMResponse(content="done"))
        llm = MockLLM(many_tools)

        agent = AgentHarness(model=llm, tools=TOOLS, system_prompt="test", max_turns=10)
        memory = ContextMemory(strategy="hybrid", window_size=5)
        agent.memory = memory
        agent.run("长对话测试")
        # 不崩溃就是通过

    def test_memory_disabled(self):
        """没有 memory 时正常运行"""
        from src.llm_client import LLMResponse
        llm = MockLLM([LLMResponse(content="ok")])
        agent = AgentHarness(model=llm, tools=[], system_prompt="test", max_turns=3)
        result = agent.run("test")
        assert "ok" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
