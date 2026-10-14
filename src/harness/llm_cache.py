"""
LLM 响应缓存 / LLM Response Cache
避免相同查询重复调用 API，基于内存 + TTL

设计说明：'用 LRU + TTL 做 LLM 缓存，相同 prompt 直接返回缓存，节省 50%+ Token'
"""

import hashlib
import json
import time
import threading
from collections import OrderedDict
from typing import Any, Optional


class LLMCache:
    """
    线程安全的 LLM 响应缓存。

    用法:
        cache = LLMCache(max_size=100, ttl_seconds=300)
        cache.set(prompt, response)
        cached = cache.get(prompt)  # None if not found or expired
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_size = max_size
        self.ttl = ttl_seconds
        self.hits = 0
        self.misses = 0

    def _make_key(self, messages: list, model: str, variant: str = "") -> str:
        """用 messages + model + variant 生成唯一 key。

        `variant` 承载会改变结果的请求参数（例如思考深度 effort、协议）。
        旧实现只用 messages+model：把 reasoning_effort 从 low 改成 high 时
        会直接命中旧深度的缓存，**回答与参数不一致却看不出原因**
        （实测：改 effort 后第二次调用 0.0s 返回完全相同的内容）。
        """
        content = (json.dumps(messages, ensure_ascii=False, sort_keys=True)
                   + model + "\x00" + variant)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def get(self, messages: list, model: str = "", variant: str = "") -> Optional[str]:
        key = self._make_key(messages, model, variant)
        with self._lock:
            if key in self._cache:
                timestamp, response = self._cache[key]
                if time.time() - timestamp < self.ttl:
                    # Move to end (LRU)
                    self._cache.move_to_end(key)
                    self.hits += 1
                    return response
                else:
                    del self._cache[key]
            self.misses += 1
            return None

    def set(self, messages: list, response: str, model: str = "", variant: str = ""):
        key = self._make_key(messages, model, variant)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (time.time(), response)
            # Evict oldest if over max
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict:
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{self.hit_rate:.1%}",
        }
