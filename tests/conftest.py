"""测试全局夹具 / Shared test fixtures.

## 为什么需要"保证当前事件循环"这个 shim

项目声明 `requires-python >=3.9`，CI 也跑 3.9 与 3.11 矩阵，因此测试必须
在 3.9 上通过。但 3.9 与 3.10+ 在这里有一个行为差异：

- `asyncio.run()` 结束时会把当前事件循环**置空**；
- 此后 `fastapi.testclient.TestClient(app)` 作为上下文管理器进入时
  （内部通过 anyio 起 portal）需要主线程存在一个可用的事件循环；
- Python 3.9 在这种情况下抛
  `RuntimeError: There is no current event loop in thread 'MainThread'`，
  而 3.10+ 不再要求预先存在循环。

表现为：**单个测试文件跑通过，整套跑却大面积 ERROR** —— 因为先前某个
测试调用过 `asyncio.run()`，把循环清掉了，之后所有用 `TestClient`
的夹具都在 setup 阶段失败。

这个 shim 只对 <3.10 生效：在没有循环、或当前循环已被关闭时装一个新的。
不在 3.12+ 上无条件调用 `get_event_loop()`，因为那里在没有运行中循环时
它会直接抛错，反而制造问题。
"""

from __future__ import annotations

import asyncio
import sys

import pytest


def _ensure_usable_event_loop() -> None:
    """确保主线程存在一个未关闭的事件循环（仅 Python <3.10 需要）。"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
        return
    if loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def _current_event_loop():
    """在每个测试前保证事件循环可用。

    autouse + function 作用域：夹具顺序上它先于本测试显式请求的夹具
    实例化，因此 `client` 这类需要循环的夹具能拿到可用的循环。
    """
    if sys.version_info < (3, 10):
        _ensure_usable_event_loop()
    yield
