"""
Eval Dataset / 评估数据集
用于量化 Agent 代码分析准确率

面试话术：'标注了 50 条真实 bug，用精确率/召回率/F1 量化 Agent 效果'
"""

EVAL_SAMPLES = [
    # Format: (file, line, category, severity, description_en, description_cn, known_bug)
    # == sandbox.py ==
    ("sandbox.py", 34, "BUG", "High", "Bare except: pass swallows KeyboardInterrupt", "裸 except 吞掉 KeyboardInterrupt", True),
    ("sandbox.py", 42, "BUG", "High", "env or os.environ.copy() treats empty dict as falsy", "空字典被当作 falsy 替换为全环境变量", True),
    ("sandbox.py", 49, "BUG", "High", "Thread race condition on result object", "线程竞态条件", True),
    ("sandbox.py", 37, "BUG", "Medium", "timeout or self.timeout treats 0 as falsy", "timeout=0 被当作 falsy", True),
    ("sandbox.py", 28, "SECURITY", "Medium", "os.chdir is not thread-safe", "os.chdir 非线程安全", True),
    ("sandbox.py", 41, "SECURITY", "Low", "Arbitrary command execution via subprocess", "任意命令执行", True),
    # == agent.py ==
    ("agent.py", 39, "BUG", "High", "No exception handling around model.chat()", "model.chat() 无异常处理", True),
    ("agent.py", 81, "BUG", "Medium", "_reset does not reset total_tokens_used", "_reset 未重置 total_tokens_used", True),
    ("agent.py", 84, "BUG", "Medium", "ToolCall(**tc) crashes on malformed dict", "格式错误的 dict 导致崩溃", True),
    ("agent.py", 106, "SECURITY", "Medium", "tool.fn(**tc.args) passes LLM args without validation", "LLM 参数直接传入无校验", True),
    ("agent.py", 97, "PERF", "Low", "import time inside method body", "方法内重复 import time", True),
    ("agent.py", 33, "PERF", "Low", "run() is sync but asyncio imported", "同步 run() 却导入了 asyncio", True),
    # == streaming.py ==
    ("streaming.py", 72, "BUG", "Medium", "json.loads may lose partial tool call on JSONDecodeError", "JSON 解析失败丢失部分工具调用", True),
    ("streaming.py", 39, "BUG", "Low", "Buffers not reset after message_stop", "message_stop 后缓冲区未清理", True),
    ("streaming.py", 26, "PERF", "Low", "_get helper redefined inside feed() on every call", "_get 每次调用重新定义", True),
    # == telemetry.py ==
    ("telemetry.py", 132, "BUG", "High", "TimedToolCall double-logs tool_call_start", "重复记录 tool_call_start", True),
    ("telemetry.py", 164, "BUG", "Medium", "datetime.now() without timezone in session ID", "会话 ID 使用无时区时间戳", True),
    ("telemetry.py", 114, "PERF", "Low", "_write opens/closes file per event", "每次写入都打开关闭文件", True),
    # == config.py ==
    ("config.py", 16, "BUG", "Medium", "Module-level for loop mutates os.environ on import", "模块级循环修改 os.environ", True),
    ("config.py", 4, "BUG", "Low", "partition(=) corrupts values containing =", "partition(=) 截断含等号的值", True),
    # == llm_client.py ==
    ("llm_client.py", 19, "BUG", "High", "b[type] direct index crashes on malformed blocks", "直接索引 b[type] 遇到未知块类型崩溃", True),
    ("llm_client.py", 24, "BUG", "Medium", "XML regex w+ misses tool names with hyphens", "XML 正则不匹配含连字符的工具名", True),
    ("llm_client.py", 38, "BUG", "Medium", "Tool call ID fxml_{len(tcs)} not globally unique", "工具调用 ID 非全局唯一", True),
    ("llm_client.py", 74, "BUG", "Medium", "Only catches HTTPError, misses URLError", "只捕获 HTTPError，遗漏 URLError", True),
    ("llm_client.py", 66, "PERF", "Low", "max_tokens=4096 hardcoded", "max_tokens 硬编码", True),
    # == git_tools.py ==
    ("git_tools.py", 7, "SECURITY", "Medium", "Hardcoded proxy 127.0.0.1:7897 in source", "源码中硬编码代理地址", True),
    ("git_tools.py", 17, "BUG", "Medium", "clone_repo uses . which is destructive if cwd not sandbox", "clone_repo 克隆到当前目录具有破坏性", True),
    ("git_tools.py", 28, "BUG", "Low", "get_diff doesn't check if dir is git repo", "get_diff 未检查是否为 git 仓库", True),
    # == orchestrator.py ==
    ("orchestrator.py", 25, "BUG", "Medium", "parse_findings splits on |, breaks if desc contains |", "parse_findings 用 | 分割，描述含 | 时出错", True),
    ("orchestrator.py", 18, "STYLE", "Low", "try-except ImportError pattern repeated across files", "重复的 try/except ImportError 模式", True),

    # False positives (to test FP detection)
    ("agent.py", 999, "BUG", "Low", "FAKE: non-existent bug for FP testing", "假 bug 用于误报测试", False),
    ("sandbox.py", 1, "BUG", "High", "FAKE: docstring missing (actually present)", "假 bug：文档字符串缺失（实际存在）", False),
    ("streaming.py", 50, "SECURITY", "Medium", "FAKE: hardcoded password (not in code)", "假 bug：硬编码密码（代码中不存在）", False),
]


def evaluate_agent(agent_fn, samples: list = None) -> dict:
    """
    评估 Agent 效果。
    agent_fn 接受 (file_path, line) 返回是否发现 bug
    """
    samples = samples or EVAL_SAMPLES
    tp = fp = fn = tn = 0

    for file, line, cat, sev, en, cn, is_bug in samples:
        found = agent_fn(file, line)
        if is_bug and found:
            tp += 1
        elif is_bug and not found:
            fn += 1
        elif not is_bug and found:
            fp += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "total": len(samples),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": f"{precision:.1%}",
        "recall": f"{recall:.1%}",
        "f1_score": f"{f1:.2f}",
    }
