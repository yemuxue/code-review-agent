"""
Bug 注入测试文件 / Bug Injection Test Sample

这个文件故意包含 30 个不同类型的 bug，用于测试 Code Review Agent 的检测能力。
覆盖: BUG / SECURITY / PERF / STYLE 四大类，以及 High/Medium/Low 三种严重度。

使用方法:
    python -m src.app.cli analyze tests/bug_injection_sample.py
    python -m src.app.cli_multi analyze tests/bug_injection_sample.py
    # 或在 Streamlit UI 中选择此文件分析

Bug 清单（共 30 个）:
  BUG      (15 个)
    1.  KeyError: dict 无默认值直接取键              (L1)
    2.  IndexError: 空列表直接取 [0]                 (L7)
    3.  除零错误: 未检查分母                           (L17)
    4.  int() 转换无 try/except                       (L25)
    5.  json.loads 无异常处理                          (L31)
    6.  变量未定义就使用                                (L41)
    7.  忘记 return                                    (L50)
    8.  += 与全局变量混淆 (nonlocal 缺失)             (L58)
    9.  无限递归: 缺终止条件                           (L67)
    10. 文件未关闭 (no with)                          (L76)
    11. 逻辑错误: and/or 混用                          (L84)
    12. 浅拷贝修改原对象                               (L92)
    13. 循环内修改迭代列表                             (L100)
    14. str 和 int 拼接 TypeError                     (L110)
    15. 参数默认值为可变对象                           (L118)
  SECURITY (7 个)
    16. eval() 执行不可信输入                          (L126)
    17. 硬编码 API 密钥                                (L131)
    18. SQL 注入: 字符串拼接查询                       (L137)
    19. subprocess 命令注入 (shell=True)               (L143)
    20. 密码明文存储                                   (L150)
    21. 文件路径穿越 (无路径校验)                      (L157)
    22. pickle.loads 不可信数据                        (L166)
  PERF     (5 个)
    23. 循环内重复打开文件                             (L176)
    24. O(n²) 列表查找                                 (L183)
    25. 不必要全局锁                                   (L191)
    26. 大字符串 += 拼接                               (L200)
    27. 缓存失效: 每次重算                            (L207)
  STYLE    (3 个)
    28. 裸 except: 吞所有异常                         (L214)
    29. import 不在顶部                                 (L219)
    30. 魔法数字无注释                                 (L225)
"""

import os
import json
import pickle
import subprocess
import threading
from typing import Optional

# ═══════════════════════════════════════════════════════════
# BUG 类 (15 个)
# ═══════════════════════════════════════════════════════════

# Bug 1: KeyError — 无默认值直接取键
def get_user_name(users: dict, user_id: str) -> str:
    return users[user_id]  # ← BUG: KeyError if user_id missing


# Bug 2: IndexError — 空列表直接取 [0]
def get_first_item(items: list) -> str:
    return items[0]  # ← BUG: IndexError if items is empty


# Bug 3: 除零错误 — 未检查分母
def divide(a: float, b: float) -> float:
    return a / b  # ← BUG: ZeroDivisionError if b == 0


# Bug 4: int() 转换无异常处理
def parse_port(port_str: str) -> int:
    return int(port_str)  # ← BUG: ValueError if port_str not numeric


# Bug 5: json.loads 无异常处理
def parse_config(config_str: str) -> dict:
    return json.loads(config_str)  # ← BUG: JSONDecodeError if invalid JSON


# Bug 6: 变量未定义就使用
def process_data(data: list) -> int:
    total = sum(data)
    return total + extra  # ← BUG: NameError — 'extra' never defined


# Bug 7: 忘记 return
def calculate_discount(price: float, rate: float) -> float:
    discounted = price * rate
    # ← BUG: missing return statement, returns None


# Bug 8: nonlocal 缺失 — 闭包内修改外层变量
def counter():
    count = 0

    def increment():
        count += 1  # ← BUG: UnboundLocalError, need nonlocal count
        return count

    return increment


# Bug 9: 无限递归 — 缺终止条件
def factorial(n: int) -> int:
    return n * factorial(n - 1)  # ← BUG: infinite recursion, no base case


# Bug 10: 文件未关闭
def read_first_line(filepath: str) -> str:
    f = open(filepath, "r")  # ← BUG: file never closed, resource leak
    return f.readline()


# Bug 11: 逻辑错误 — and/or 混用
def is_valid_input(value: Optional[str]) -> bool:
    return value is not None or value != ""  # ← BUG: should be 'and', always True


# Bug 12: 浅拷贝修改原对象
def add_item(original: list, item: str) -> list:
    new_list = original  # ← BUG: shallow copy, mutates original
    new_list.append(item)
    return new_list


# Bug 13: 循环内修改迭代列表
def remove_evens(numbers: list) -> list:
    for num in numbers:  # ← BUG: modifying list while iterating
        if num % 2 == 0:
            numbers.remove(num)
    return numbers


# Bug 14: str 和 int 拼接
def build_message(user_id: int) -> str:
    return "User " + user_id + " logged in"  # ← BUG: TypeError, str + int


# Bug 15: 可变默认参数
def add_tag(tags: list = []) -> list:  # ← BUG: mutable default shared across calls
    tags.append("new")
    return tags


# ═══════════════════════════════════════════════════════════
# SECURITY 类 (7 个)
# ═══════════════════════════════════════════════════════════

# Bug 16: eval() 执行不可信输入
def evaluate_expression(expression: str) -> float:
    return eval(expression)  # ← SECURITY: code injection via eval


# Bug 17: 硬编码 API 密钥
API_SECRET_KEY = "sk-3e2320248f72407cb134cc7ef39cc303"  # ← SECURITY: hardcoded secret


# Bug 18: SQL 注入
def search_users(db_conn, keyword: str) -> list:
    query = f"SELECT * FROM users WHERE name = '{keyword}'"  # ← SECURITY: SQL injection
    cursor = db_conn.execute(query)
    return cursor.fetchall()


# Bug 19: subprocess 命令注入
def run_grep(pattern: str, filepath: str) -> str:
    cmd = f"grep '{pattern}' {filepath}"  # ← SECURITY: shell injection
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout


# Bug 20: 密码明文存储
def store_password(username: str, password: str) -> None:
    with open(f"{username}_pass.txt", "w") as f:
        f.write(password)  # ← SECURITY: plaintext password, no hashing


# Bug 21: 文件路径穿越
def read_user_file(user_id: str, filename: str) -> str:
    path = f"/data/users/{user_id}/{filename}"  # ← SECURITY: path traversal via '..'
    with open(path, "r") as f:
        return f.read()


# Bug 22: pickle.loads 不可信数据
def load_pickle_data(data: bytes):
    return pickle.loads(data)  # ← SECURITY: arbitrary code execution via pickle


# ═══════════════════════════════════════════════════════════
# PERF 类 (5 个)
# ═══════════════════════════════════════════════════════════

# Bug 23: 循环内重复打开文件
def count_lines(filepaths: list) -> int:
    total = 0
    for fp in filepaths:
        with open(fp, "r") as f:  # ← PERF: open/close per iteration, reuse instead
            total += len(f.readlines())
    return total


# Bug 24: O(n²) 列表查找
def find_duplicates(items: list) -> list:
    dupes = []
    for i in range(len(items)):
        if items[i] in items[:i]:  # ← PERF: O(n²), use set
            dupes.append(items[i])
    return dupes


# Bug 25: 不必要全局锁
_global_lock = threading.Lock()
_results: list = []


def add_result(result: str) -> None:
    with _global_lock:  # ← PERF: lock contention, no shared state
        _results.append(result)


# Bug 26: 大字符串 += 拼接
def build_large_string(parts: list) -> str:
    result = ""
    for p in parts:
        result += p  # ← PERF: O(n²) string concat, use join
    return result


# Bug 27: 缓存失效 — 每次重算
def get_square(n: int) -> int:
    # ← PERF: no memoization, recomputes every call
    return n * n


# ═══════════════════════════════════════════════════════════
# STYLE 类 (3 个)
# ═══════════════════════════════════════════════════════════

# Bug 28: 裸 except
def safe_parse(value: str) -> Optional[int]:
    try:
        return int(value)
    except:  # ← STYLE: bare except swallows KeyboardInterrupt too
        return None


# Bug 29: import 不在顶部
def use_math():
    import math  # ← STYLE: import inside function
    return math.pi


# Bug 30: 魔法数字
def get_retry_delay(attempt: int) -> int:
    return attempt * 30  # ← STYLE: magic number 30, no constant/comment


# ═══════════════════════════════════════════════════════════
# 测试入口：运行此文件确认 bug 存在
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Bug injection sample loaded. 30 bugs present.")
    print("Categories: BUG(15) SECURITY(7) PERF(5) STYLE(3)")
