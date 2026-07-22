"""
Context Memory Management / 上下文记忆管理

三种策略：
1. Sliding Window  — 保留最近 N 条消息，丢弃旧的
2. Summarization   — LLM 压缩旧消息为摘要
3. Hybrid          — 滑窗 + 摘要：保留 system + 最近 K 轮 + 旧轮摘要

面试话术：'和 LangChain 的 ConversationSummaryBufferMemory 原理相同，但手写实现'
"""

from __future__ import annotations
from typing import Callable


class ContextMemory:
    """
    上下文记忆管理器。

    策略:
        - "sliding": 滑动窗口，保留最近 window_size 条消息
        - "summary": LLM 摘要压缩（需要传入 summarizer 函数）
        - "hybrid":  保留 system + 最近 K 轮 + 旧轮摘要（默认）

    用法:
        mem = ContextMemory(strategy="hybrid", window_size=10)
        compressed = mem.compact(messages)
    """

    def __init__(
        self,
        strategy: str = "hybrid",
        window_size: int = 10,
        summarizer: Callable | None = None,
    ):
        self.strategy = strategy
        self.window_size = window_size
        self.summarizer = summarizer
        self.compaction_count = 0
        self.messages_discarded = 0

    def should_compact(self, messages: list[dict]) -> bool:
        """判断是否需要压缩"""
        return len(messages) > self.window_size + 4  # system + user + 缓冲

    def compact(self, messages: list[dict]) -> list[dict]:
        """
        压缩消息列表。返回新列表（不修改原列表）。

        保留：
            - messages[0]: system prompt（永远不删）
            - messages[1]: 用户原始 query（保留上下文）
            - 最近 window_size 条消息
            - 中间部分：压缩为一条摘要
        """
        if not self.should_compact(messages):
            return messages

        self.compaction_count += 1

        if self.strategy == "sliding":
            return self._sliding_window(messages)

        elif self.strategy == "summary" and self.summarizer:
            return self._summarize(messages)

        else:  # hybrid
            return self._hybrid(messages)

    def _sliding_window(self, messages: list[dict]) -> list[dict]:
        """滑动窗口：system + user + 最近 N 条"""
        system_and_user = [m for m in messages if m["role"] in ("system",)]
        if not system_and_user:
            system_and_user = messages[:2]  # fallback: first 2 messages
        rest = [m for m in messages if m["role"] not in ("system",)]
        kept = rest[-self.window_size:]
        discarded = len(rest) - len(kept)
        self.messages_discarded += discarded
        return system_and_user + kept

    def _summarize(self, messages: list[dict]) -> list[dict]:
        """LLM 摘要压缩"""
        system_msg = [m for m in messages if m["role"] == "system"]
        user_msg = [m for m in messages if m["role"] == "user"][:1]
        recent = messages[-self.window_size:]

        # 找出需要摘要的部分（中间的消息）
        to_summarize = [
            m for m in messages
            if m not in system_msg and m not in user_msg and m not in recent
        ]
        if to_summarize and self.summarizer:
            summary_text = "\n".join(
                f"[{m['role']}]: {str(m.get('content', ''))[:200]}"
                for m in to_summarize
            )
            summary = self.summarizer(summary_text)
            self.messages_discarded += len(to_summarize)
            return system_msg + user_msg + [
                {"role": "system", "content": f"[Context Summary]: {summary}"}
            ] + recent

        return messages

    def _hybrid(self, messages: list[dict]) -> list[dict]:
        """
        混合策略：
        1. 保留 system + 最近 window_size 条
        2. 中间部分不删，但截断每条消息的 content 至 200 字符
        """
        system_msg = [m for m in messages if m["role"] == "system"]
        user_msg = [m for m in messages if m["role"] == "user"][:1]
        recent = messages[-self.window_size:]

        middle = [
            m for m in messages
            if m not in system_msg and m not in user_msg and m not in recent
        ]
        # 截断中间消息
        truncated = []
        for m in middle:
            content = str(m.get("content", m.get("tool_calls", "")))
            if len(content) > 300:
                content = content[:150] + f"...[truncated {len(content)} chars]"
            truncated.append({**m, "content": content})

        self.messages_discarded += max(0, len(middle) - len(truncated))
        return system_msg + user_msg + truncated + recent

    @property
    def stats(self) -> dict:
        return {
            "strategy": self.strategy,
            "window_size": self.window_size,
            "compactions": self.compaction_count,
            "discarded": self.messages_discarded,
        }
