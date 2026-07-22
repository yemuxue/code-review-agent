"""
LLM 响应缓存 / LLM Response Cache
避免相同查询重复调用 API，基于内存 + TTL

面试话术：'用 LRU + TTL 做 LLM 缓存，相同 prompt 直接返回缓存，节省 50%+ Token'
"""

import hashlib, json, time, threading
from collections import OrderedDict
from typing import Optional


class LLMCache:
    """
    线程安全的 LLM 响应缓存。

    用法:
        cache = LLMCache(max_size=100, ttl_seconds=300)
        cache.set(prompt, response)
        cached = cache.get(prompt)  # None if not found or expired
    """

    def __init__(self, max_size: int = 100, ttl_seconds: int = 300):
        self._cache: OrderedDict[str, tuple[float, any]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_size = max_size
        self.ttl = ttl_seconds
        self.hits = 0
        self.misses = 0

    def _make_key(self, messages: list, model: str) -> str:
        """用 messages + model 生成唯一 key"""
        content = json.dumps(messages, ensure_ascii=False, sort_keys=True) + model
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def get(self, messages: list, model: str = "") -> Optional[str]:
        key = self._make_key(messages, model)
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

    def set(self, messages: list, response: str, model: str = ""):
        key = self._make_key(messages, model)
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
