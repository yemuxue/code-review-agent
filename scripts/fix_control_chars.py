"""修数据集描述里的控制字符。

## 为什么一开始没找到

源码里写的是 **`\\x01`（四字符转义）**，Python 解析后才变成 U+0001。
所以：
  - 扫源码找原始 U+0001 字节 → 找不到（源码里没有那个字节）
  - 扫源码找 `\\1` → 也找不到（源码里是 `\\x01`）
只有**导入后检查解析结果**才能发现，或者在源码层精确匹配 `\\x01`。

## 目标
把 `backreference \\x01 inside` 改成 `backreference \\\\1 inside`，
使解析结果是字面的「反斜杠 + 1」（原本想表达的正则反向引用）。
注意源码里必须写 `\\\\1`（两个反斜杠加 1），否则 `\\1` 会被解析成八进制转义
U+0001 —— 又变回控制符。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

DATASET = Path("tests/eval_llm_agent_qa.py")
# 源码层的四字符转义：\ x 0 1
SOURCE_ESCAPE = chr(92) + "x01"
# 目标源码层写法：\ \ 1  → 解析后为 反斜杠+1
SOURCE_TARGET = chr(92) * 2 + "1"


def _load():
    spec = importlib.util.spec_from_file_location("ds_verify", DATASET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    text = DATASET.read_text(encoding="utf-8")
    count = text.count(SOURCE_ESCAPE)
    print(f"源码中 {SOURCE_ESCAPE!r} 出现: {count} 处")
    if count == 0:
        print("源码层无需修改；检查解析结果是否仍有控制字符……")
    else:
        DATASET.write_text(text.replace(SOURCE_ESCAPE, SOURCE_TARGET),
                           encoding="utf-8")
        print(f"已替换为源码级 {SOURCE_TARGET!r}（解析后为字面反斜杠+1）")

    module = _load()
    control = [i for i, s in enumerate(module.EVAL_SAMPLES, 1)
               if any(ord(ch) == 1 for ch in str(s[4]) + str(s[5]))]
    print(f"重新导入后仍含 U+0001 的样本: {control or '无'}")

    target = next((s for s in module.EVAL_SAMPLES
                   if "param_pattern regex" in str(s[4])), None)
    if target is not None:
        fragment = str(target[4])
        start = fragment.find("backreference")
        print(f"样本 #10 描述片段: {fragment[start:start + 46]!r}")
    return 0 if not control else 1


if __name__ == "__main__":
    raise SystemExit(main())
