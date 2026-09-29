"""模型价格表与 token 成本换算。

价格必须由调用方在报告中记录版本/来源；未知模型宁可返回 None，也不猜测成本。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_million_usd: float
    output_per_million_usd: float


# 仅保留可审计的显式配置；新模型由维护者按供应商价格页更新。
MODEL_PRICES: dict[str, ModelPrice] = {}


def set_model_price(model: str, input_per_million_usd: float,
                    output_per_million_usd: float) -> None:
    """登记本次评测使用的审计价格；拒绝负数以避免报告出现伪造成本。"""
    if input_per_million_usd < 0 or output_per_million_usd < 0:
        raise ValueError("模型单价不能为负数")
    MODEL_PRICES[model] = ModelPrice(input_per_million_usd, output_per_million_usd)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """按输入/输出 token 计算美元成本；未知模型返回 None。"""
    price = MODEL_PRICES.get(model)
    if price is None:
        return None
    return round((input_tokens * price.input_per_million_usd
                  + output_tokens * price.output_per_million_usd) / 1_000_000, 8)
