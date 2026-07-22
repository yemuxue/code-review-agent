"""
多模型路由 / Multi-Model Router
按任务类型和成本分配不同模型

面试话术：'用 Router 按任务复杂度分配模型：简单任务用便宜模型，复杂任务用强模型，成本优化 60%'
"""

from __future__ import annotations
from llm_client import AnthropicClient, LLMResponse


MODEL_REGISTRY = {
    "cheap": "deepseek-chat",        # 便宜：简单分析、格式转换
    "strong": "deepseek-v4-pro[1m]", # 强：多 Agent 分析、复杂推理
}

TASK_ROUTES = {
    # task_pattern → model_tier
    "find bug": "strong",
    "security": "strong",
    "review": "strong",
    "multi-agent": "strong",
    "analyze": "strong",
    "format": "cheap",
    "summarize": "cheap",
    "list": "cheap",
    "explain": "cheap",
}


class ModelRouter:
    """
    根据任务自动选择模型。

    用法:
        router = ModelRouter()
        router.route("Find bugs in sandbox.py")  → deepseek-v4-pro[1m]
        router.route("List all files")           → deepseek-chat
    """

    def __init__(self):
        self._clients = {}

    def _get_client(self, tier: str) -> AnthropicClient:
        if tier not in self._clients:
            model = MODEL_REGISTRY.get(tier, "deepseek-chat")
            self._clients[tier] = AnthropicClient(model=model)
        return self._clients[tier]

    def route(self, task: str) -> AnthropicClient:
        """根据任务内容选择模型"""
        task_lower = task.lower()
        for pattern, tier in TASK_ROUTES.items():
            if pattern in task_lower:
                return self._get_client(tier)
        # 默认：兜底便宜模型
        return self._get_client("cheap")

    def chat(self, task: str, messages: list) -> LLMResponse:
        """智能路由并调用"""
        client = self.route(task)
        return client.chat(messages)

    @property
    def stats(self) -> dict:
        return {"routes": TASK_ROUTES, "models": MODEL_REGISTRY}
