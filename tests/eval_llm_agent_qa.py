"""
Eval Dataset / 评估数据集 — llm-agent-qa-system
124 条手工标注样本，覆盖全部 20+ 源文件

格式: (file, line, category, severity, description_en, description_cn, is_real_bug)
- is_real_bug=True: 真 bug/问题
- is_real_bug=False: 假 bug（用于测试 FP 检测）

实测构成（由 tests/eval/datasets.py 的 preflight 校验，与 DATASET_META 必须一致）：
84 条真实问题 + 40 条负例 = 124 条。

## v1.1.0 变更说明（2026-10-13）

逐条读源码复核后发现 29 条标注描述的是**当前代码里已不存在的缺陷**
（2026-10-07 漏报归因显示这类占漏报的 48%）。处置方式：

- **2 条 RELOCATE**：缺陷确实仍在，只是行号漂移（`prompt.py` 75→76、85→106）
- **27 条 RELABEL_NEGATIVE**：该位置代码现在正确，标记为「已修复」并翻成负例

这样处置而不是删除样本，是为了**保持测试集完备**（总数仍 124 条，
正负例都有覆盖），且每个标签对当前代码都为真。
被翻转的 27 条同时成为新的 FP 陷阱：模型在那里报出即为误报。

## v1.2.0 变更说明（2026-10-13）

复核那 27 条时发现 **3 条位置仍含其它真实缺陷** —— 只排除了"原标注所指"的
问题，没有排除该位置**别的**问题。把这类样本当"应当沉默"的负例，会让模型
报出真问题时被计为误报，**低估精确率**。因此改回正例并替换描述：

| 位置 | 新描述的缺陷 | 独立核验依据 |
|---|---|---|
| `react_agent.py:222` | 超限强制总结的响应未经 `_extract_final_answer` 解析，原始 `<thinking>` 标签直接返回给用户 | 同函数 167 行走的是解析后分支，两处不一致 |
| `rag/retriever.py:78` | `source` 为死存储（98 行赋值后从未读取）；方法全仓无引用（死代码） | 全仓 grep 仅命中定义处 |
| `config/settings.py:65` | `current_api_key`/`current_base_url` 全仓无引用，与 `create_llm_adapter` 分发重复 | 全仓 grep 仅命中定义处 |

⚠️ 这三条**模型都答了 NO**，所以改回正例会让**召回率下降**（分母 81→84）。
   这是刻意的：修正标签不以"让读数变好"为目标。

⚠️ 因此 **v1.1.0 与 v1.2.0 的读数也不可直接比较**。跨版本引用必须给出 version。
"""

from __future__ import annotations

# ─── 数据集版本元信息 ───
# 版本必须随**样本内容变更**而递增：真实 replay 的读数只有配上版本号才能
# 跨时间比较，否则"模型变好了"和"标注集变了"无法区分。
# tests/test_eval_datasets.py 会锁定版本号与样本数量的对应关系。
DATASET_META = {
    "dataset_id": "llm-agent-qa-system-labeled",
    # 1.0.0 → 1.1.0：27 条正例翻为负例、2 条重定位，召回分母 108 → 81。
    # 1.1.0 → 1.2.0：复核发现 3 条翻转负例仍含其它真实缺陷，改回正例，
    #                 分母 81 → 84。跨版本读数均不可直接比较。
    "version": "1.2.0",
    "sample_count": 124,
    "real_bug_count": 84,
    "fp_trap_count": 40,
    "target_project": "llm-agent-qa-system (外部仓库，需外部副本)",
    "target_layout": "src/<file>",
    "annotation_source": "人工标注 + 2026-10-13 两轮逐条复核修正",
    "notes": ("含 40 条负例（16 条原始 FP 陷阱 + 24 条复核判定为『已修复』的旧标注）；"
              "另 3 条虽原标注失效但位置仍有其它真实缺陷，已改回正例；"
              "文件匹配缺失曾导致 16/124 静默跳过的事故"),
}

EVAL_SAMPLES = [
    # ═══════════════════════════════════════════════════════════════
    # react_agent.py (12 samples)
    # ═══════════════════════════════════════════════════════════════
    ("react_agent.py", 209, "BUG", "High",
     "Unparseable LLM output exposed raw to user as final answer",
     "LLM格式解析失败时原始文本暴露给用户", True),
    ("react_agent.py", 154, "BUG", "Medium",
     "LLM response stored in messages BEFORE parsing, causing malformed tool_call entries on parse failure",
     "解析失败时已将未解析响应存入消息列表", True),
    ("react_agent.py", 379, "BUG", "Medium",
     "tool_name not stripped before dict lookup, leading whitespace causes KeyError-like miss",
     "工具名未strip导致空格前缀匹配失败", True),
    ("react_agent.py", 167, "BUG", "Low",
     "final_answer extracted but thinking may be empty when final_answer uses different tags",
     "final_answer和thinking解析互斥但未处理交叉场景", True),
        ("react_agent.py", 97, "BUG", "Low",
     "[FIXED] Memory default SlidingWindowMemory created inside __init__ signature, shared across instances if mutable — current code is correct; reporting it is a false positive",
     "【已修复】默认参数SlidingWindowMemory()在类定义时求值可能导致实例共享 —— 当前代码正确，报出即为误报", False),
        ("react_agent.py", 140, "PERF", "Medium",
     "[FIXED] Memory.add_message called but messages list rebuilt from scratch every turn with _build_messages — current code is correct; reporting it is a false positive",
     "【已修复】每次循环重建消息列表而非增量更新 —— 当前代码正确，报出即为误报", False),
            ("react_agent.py", 222, "BUG", "Medium",
     "Max-iterations forced summary assigns the raw LLM response to final_answer without _extract_final_answer, exposing <thinking>/<final_answer> markup to the user",
     "超限强制总结的响应未经_extract_final_answer解析，原始XML标签直接作为最终答案返回给用户", True),
    ("react_agent.py", 242, "PERF", "Low",
     "run_stream duplicates entire run() logic instead of sharing core loop",
     "run和run_stream重复90%逻辑未抽取公共方法", True),
    ("react_agent.py", 281, "BUG", "Medium",
     "run_stream parses thinking but only yields after full_response collected — not truly streaming thought",
     "流式模式下thinking在完整响应后才yield，非真正流式", True),
        ("react_agent.py", 366, "BUG", "Low",
     "[FIXED] param_pattern regex uses backreference \1 inside character class — may miss nested params — current code is correct; reporting it is a false positive",
     "【已修复】参数正则反向引用在字符类中可能失效 —— 当前代码正确，报出即为误报", False),
    ("react_agent.py", 115, "STYLE", "Low",
     "add_tool rebuilds entire system_prompt on every tool addition — O(n) overhead",
     "每次添加工具都重建系统提示词", True),
        ("react_agent.py", 50, "STYLE", "Low",
     "[FIXED] AgentStep dataclass missing type annotations for Optional fields — current code is correct; reporting it is a false positive",
     "【已修复】AgentStep字段缺少Optional类型注解 —— 当前代码正确，报出即为误报", False),

    # ═══════════════════════════════════════════════════════════════
    # prompt.py (6 samples)
    # ═══════════════════════════════════════════════════════════════
    ("prompt.py", 52, "BUG", "Medium",
     "XML format requires LLM exact tag matching — single typo in tag name breaks entire parse",
     "XML格式要求LLM精确匹配标签，一个typo导致全解析失败", True),
        ("prompt.py", 76, "STYLE", "Low",
     "Rule '一次一个工具: 每次只调用一个工具' limits parallel tool use efficiency",
     "强制每次只调一个工具限制并行效率", True),
    ("prompt.py", 40, "PERF", "Low",
     "System prompt rebuilt as f-string every agent init — could be cached",
     "系统提示词每次重建未缓存", True),
        ("prompt.py", 106, "BUG", "Low",
     "SIMPLE_SYSTEM_PROMPT defined but never used in agent code — dead code",
     "SIMPLE_SYSTEM_PROMPT定义但从未使用", True),
    ("prompt.py", 103, "STYLE", "Low",
     "MAX_ITERATIONS_MESSAGE hardcoded in Chinese — not i18n-friendly",
     "超限消息硬编码中文不便于国际化", True),
        ("prompt.py", 29, "PERF", "Low",
     "[FIXED] build_system_prompt concatenates strings in loop with += instead of join — current code is correct; reporting it is a false positive",
     "【已修复】字符串拼接用+=而非join —— 当前代码正确，报出即为误报", False),

    # ═══════════════════════════════════════════════════════════════
    # base.py (tools) (5 samples)
    # ═══════════════════════════════════════════════════════════════
    ("agent/tools/base.py", 37, "BUG", "Medium",
     "Class-level mutable default parameters={} shared across all Tool subclasses",
     "类级别可变默认参数字典被所有子类共享", True),
    ("agent/tools/base.py", 59, "BUG", "Low",
     "safe_execute wraps error but loses original traceback — hard to debug tool failures",
     "safe_execute包裹错误但丢失原始traceback", True),
    ("agent/tools/base.py", 72, "STYLE", "Low",
     "safe_execute success prefix '[工具执行成功]' adds noise to LLM context",
     "成功消息加前缀增加LLM噪声", True),
    ("agent/tools/base.py", 47, "BUG", "Low",
     "execute signature uses **kwargs but parameters schema not validated against actual call",
     "execute用**kwargs但参数schema未校验实际调用", True),
    ("agent/tools/base.py", 24, "STYLE", "Low",
     "name/description/parameters declared as class attrs with type hints but no ClassVar",
     "类属性未标记ClassVar误导为实例属性", True),

    # ═══════════════════════════════════════════════════════════════
    # calculator.py (5 samples)
    # ═══════════════════════════════════════════════════════════════
    ("tools/calculator.py", 105, "SECURITY", "High",
     "eval() with __builtins__={{}} still allows DoS via '9**9**9' exponential blowup",
     "eval即使禁用builtins仍允许指数爆炸DoS攻击", True),
    ("tools/calculator.py", 18, "BUG", "Medium",
     "SAFE_EXPR_PATTERN regex allows 'math.' prefix but match not anchored — 'abc math.sqrt' passes",
     "安全正则未锚定导致绕过", True),
    ("tools/calculator.py", 105, "BUG", "Low",
     "eval result may return float infinity or NaN without handling",
     "eval返回inf/NaN未处理", True),
    ("tools/calculator.py", 96, "BUG", "Low",
     "Empty expression check AFTER strip — '' evaluated as False, error message unreachable",
     "空表达式检查在strip之后但逻辑正确，实际无害", False),
        ("tools/calculator.py", 50, "STYLE", "Low",
     "[FIXED] _SAFE_FUNCTIONS dict uses 'abs': abs which shadows builtin — intentional but confusing — current code is correct; reporting it is a false positive",
     "【已修复】_SAFE_FUNCTIONS映射abs到abs是冗余的 —— 当前代码正确，报出即为误报", False),

    # ═══════════════════════════════════════════════════════════════
    # web_search.py (5 samples)
    # ═══════════════════════════════════════════════════════════════
        ("tools/web_search.py", 62, "BUG", "Medium",
     "[FIXED] Import fallback from ddgs to duckduckgo_search may ImportError on both — unhelpful error message — current code is correct; reporting it is a false positive",
     "【已修复】双重import失败后错误消息无帮助 —— 当前代码正确，报出即为误报", False),
        ("tools/web_search.py", 73, "BUG", "Medium",
     "[FIXED] DDGS() context manager may leak connections if text() raises mid-iteration — current code is correct; reporting it is a false positive",
     "【已修复】DDGS上下文管理器在迭代中异常可能泄漏连接 —— 当前代码正确，报出即为误报", False),
        ("tools/web_search.py", 84, "PERF", "Low",
     "[FIXED] String concatenation in loop for formatting results — use join for large results — current code is correct; reporting it is a false positive",
     "【已修复】结果格式化用+=而非join —— 当前代码正确，报出即为误报", False),
    ("tools/web_search.py", 47, "BUG", "Low",
     "max_results parameter not exposed in parameters schema — LLM cannot control result count",
     "max_results参数未在schema中暴露", True),
        ("tools/web_search.py", 67, "BUG", "Low",
     "[FIXED] Error message suggests 'pip install ddgs' but cannot execute pip at runtime — current code is correct; reporting it is a false positive",
     "【已修复】建议pip install但运行时无法执行 —— 当前代码正确，报出即为误报", False),

    # ═══════════════════════════════════════════════════════════════
    # knowledge_base.py (4 samples)
    # ═══════════════════════════════════════════════════════════════
        ("tools/knowledge_base.py", 73, "BUG", "Medium",
     "[FIXED] retriever.retrieve may return empty but reranker still called with empty list — wasted compute — current code is correct; reporting it is a false positive",
     "【已修复】retrieve返回空列表时reranker仍被调用 —— 当前代码正确，报出即为误报", False),
    ("tools/knowledge_base.py", 91, "BUG", "Low",
     "Similarity score from ChromaDB distance conversion inconsistent with reranker score — different scales",
     "ChromaDB距离转相似度与reranker分数尺度不一致", True),
    ("tools/knowledge_base.py", 53, "BUG", "Low",
     "retriever and reranker stored as instance attrs but None checks only at call time",
     "retriever/reranker在execute时才检查None", True),
    ("tools/knowledge_base.py", 79, "BUG", "Low",
     "retrieve results check 'if not results' but empty list [] is falsy — correct but fragile",
     "空结果检查正确但语义模糊", True),

    # ═══════════════════════════════════════════════════════════════
    # base.py (llm) (5 samples)
    # ═══════════════════════════════════════════════════════════════
        ("llm/base.py", 30, "BUG", "Medium",
     "[FIXED] chat() raises NotImplementedError but base class __init__ stores client as None — inconsistent — current code is correct; reporting it is a false positive",
     "【已修复】chat抛NotImplementedError但实例化时client=None不一致 —— 当前代码正确，报出即为误报", False),
        ("llm/base.py", 112, "BUG", "Medium",
     "[FIXED] response.choices[0].message.content may be None — returns empty str but caller may expect None — current code is correct; reporting it is a false positive",
     "【已修复】API返回None时返回空字符串但调用方可能期望None —— 当前代码正确，报出即为误报", False),
    ("llm/base.py", 131, "BUG", "Low",
     "chat_stream yields delta.content without checking if delta is None",
     "流式yield未检查delta是否为None", True),
    ("llm/base.py", 95, "BUG", "Medium",
     "chat() passes **kwargs to API but tools/tool_choice not explicitly supported in signature",
     "chat透传**kwargs但未显式支持tools参数", True),
    ("llm/base.py", 88, "BUG", "Low",
     "_init_client checks self.client is None but not thread-safe — race condition on lazy init",
     "懒初始化client非线程安全", True),

    # ═══════════════════════════════════════════════════════════════
    # deepseek_adapter.py (3 samples)
    # ═══════════════════════════════════════════════════════════════
        ("llm/deepseek_adapter.py", 12, "BUG", "Low",
     "[FIXED] Mixin order 'OpenAICompatibleMixin, LLMAdapter' — MRO may prioritize Mixin methods over ABC abstract — current code is correct; reporting it is a false positive",
     "【已修复】混入顺序可能导致MRO问题 —— 当前代码正确，报出即为误报", False),
    ("llm/deepseek_adapter.py", 25, "STYLE", "Low",
     "provider_name hardcoded to 'DeepSeek' — no model version info included",
     "provider_name不包含模型版本", True),
    ("llm/deepseek_adapter.py", 20, "STYLE", "Low",
     "Docstring example uses hardcoded 'sk-xxx' which is a security anti-pattern in docs",
     "文档示例硬编码API密钥占位符", True),

    # ═══════════════════════════════════════════════════════════════
    # qwen_adapter.py (2 samples)
    # ═══════════════════════════════════════════════════════════════
        ("llm/qwen_adapter.py", 12, "BUG", "Low",
     "[FIXED] Same MRO issue as DeepSeekAdapter — shared base class design flaw — current code is correct; reporting it is a false positive",
     "【已修复】与DeepSeekAdapter相同的MRO问题 —— 当前代码正确，报出即为误报", False),
    ("llm/qwen_adapter.py", 31, "STYLE", "Low",
     "provider_name uses Chinese characters '通义千问' — may cause encoding issues in logs",
     "provider_name含中文可能导致日志编码问题", True),

    # ═══════════════════════════════════════════════════════════════
    # sliding_window.py (5 samples)
    # ═══════════════════════════════════════════════════════════════
    ("memory/sliding_window.py", 34, "BUG", "High",
     "deque maxlen=window_size*2 assumes 2 msg/turn but tool observations add user msgs — window shrinks silently",
     "deque窗口大小假设每轮2条消息但工具observation占用额外空间", True),
    ("memory/sliding_window.py", 36, "BUG", "Medium",
     "add_message uses dict literal but get_context returns same dict objects — mutation risk",
     "add_message用dict但get_context返回同一对象引用", True),
        ("memory/sliding_window.py", 56, "BUG", "Low",
     "[FIXED] clear() empties deque but doesn't reset any derived state — no side effects but incomplete — current code is correct; reporting it is a false positive",
     "【已修复】clear()不重置派生状态 —— 当前代码正确，报出即为误报", False),
    ("memory/sliding_window.py", 66, "PERF", "Low",
     "get_last_n_messages copies entire deque to list then slices — O(n) for small n",
     "get_last_n_messages先转list再切片低效", True),
    ("memory/sliding_window.py", 41, "STYLE", "Low",
     "add_message dict inline creation — no Message dataclass for type safety",
     "消息用裸dict无类型安全", True),

    # ═══════════════════════════════════════════════════════════════
    # vector_store.py (7 samples)
    # ═══════════════════════════════════════════════════════════════
    ("rag/vector_store.py", 78, "BUG", "High",
     "hash() used for document ID — not stable across Python processes (PYTHONHASHSEED)",
     "hash()跨进程不稳定导致重复文档", True),
    ("rag/vector_store.py", 128, "BUG", "Medium",
     "Distance-to-similarity formula '1/(1+d)' assumes cosine distance range 0-2, but ChromaDB can use other metrics",
     "距离转相似度公式假设余弦距离范围，其他度量不准", True),
    ("rag/vector_store.py", 54, "BUG", "Low",
     "Bare except catches BaseException including KeyboardInterrupt and SystemExit",
     "裸except捕获KeyboardInterrupt等系统异常", True),
    ("rag/vector_store.py", 89, "BUG", "Low",
     "Batch upload uses hardcoded batch_size=100 but embeddings list could be very large — no progress tracking",
     "批量上传硬编码100且无进度追踪", True),
    ("rag/vector_store.py", 113, "BUG", "Low",
     "query results['ids'][0] may be empty list — checked but no warning logged",
     "查询返回空结果无日志警告", True),
    ("rag/vector_store.py", 49, "STYLE", "Low",
     "get_or_create_collection try/except is overly broad — catches all exceptions including config errors",
     "get_or_create_collection捕获范围过宽", True),
    ("rag/vector_store.py", 136, "PERF", "Low",
     "get_document_count calls collection.count() which queries ChromaDB each time — consider caching",
     "get_document_count每次查询ChromaDB应缓存", True),

    # ═══════════════════════════════════════════════════════════════
    # retriever.py (4 samples)
    # ═══════════════════════════════════════════════════════════════
    ("rag/retriever.py", 64, "BUG", "Low",
     "Empty query check returns [] but downstream code may expect non-empty list",
     "空查询返回[]但下游可能未处理", True),
    ("rag/retriever.py", 101, "STYLE", "Low",
     "Format string uses f-string for each result — memory inefficient for k=100",
     "f-string逐个格式化浪费内存", True),
        ("rag/retriever.py", 41, "BUG", "Low",
     "[FIXED] embedding_model stored but never validated to be loaded before retrieve() call — current code is correct; reporting it is a false positive",
     "【已修复】embedding_model存储但未验证是否已加载 —— 当前代码正确，报出即为误报", False),
            ("rag/retriever.py", 78, "BUG", "Low",
     "retrieve_and_format assigns `source` at line 98 but never reads it (dead store) and the method itself is never referenced anywhere — dead code",
     "retrieve_and_format第98行赋值的source从未被读取（死存储），且该方法全仓无引用（死代码）", True),

    # ═══════════════════════════════════════════════════════════════
    # reranker.py (4 samples)
    # ═══════════════════════════════════════════════════════════════
    ("rag/reranker.py", 83, "BUG", "Medium",
     "scores.tolist() may return numpy array, then list comprehension forces float conversion redundantly",
     "score类型转换逻辑冗余且可能丢失精度", True),
    ("rag/reranker.py", 87, "PERF", "Low",
     "Triple float conversion (tolist→list comp→zip) — unnecessary overhead",
     "三次浮点转换浪费性能", True),
    ("rag/reranker.py", 42, "BUG", "Low",
     "Lazy model loading via property not thread-safe — two threads may double-load",
     "属性懒加载非线程安全", True),
    ("rag/reranker.py", 48, "BUG", "Low",
     "max_length=512 hardcoded — longer passages silently truncated by CrossEncoder",
     "max_length=512硬编码长文本静默截断", True),

    # ═══════════════════════════════════════════════════════════════
    # embedding.py (4 samples)
    # ═══════════════════════════════════════════════════════════════
    ("rag/embedding.py", 45, "BUG", "Medium",
     "Lazy model loading uses property — first call blocks for minutes downloading model with no progress",
     "延迟加载首次调用阻塞数分钟无进度提示", True),
    ("rag/embedding.py", 92, "BUG", "Low",
     "BGE query prefix hardcoded — model upgrade may change prefix requirement",
     "BGE查询前缀硬编码模型升级后可能失效", True),
    ("rag/embedding.py", 72, "PERF", "Low",
     "batch_size=32 hardcoded — suboptimal for GPU with larger VRAM",
     "batch_size=32硬编码GPU利用率低", True),
    ("rag/embedding.py", 79, "BUG", "Low",
     "embed_query does not strip/validate input — empty string produces meaningless embedding",
     "embed_query未验证空字符串", True),

    # ═══════════════════════════════════════════════════════════════
    # document_loader.py (6 samples)
    # ═══════════════════════════════════════════════════════════════
    ("rag/document_loader.py", 105, "BUG", "Medium",
     "open with encoding='utf-8' hardcoded — fails on GBK/Shift-JIS encoded files",
     "UTF-8硬编码无法处理GBK等编码文件", True),
    ("rag/document_loader.py", 42, "BUG", "Medium",
     "SUPPORTED_EXTENSIONS missing .pdf but docstring says PDF supported — misleading",
     "文档声称支持PDF但未实现", True),
    ("rag/document_loader.py", 148, "BUG", "Low",
     r"HTML regex <script> greedy matching may over-match across script blocks",
     "HTML正则script标签贪婪匹配可能过度", True),
    ("rag/document_loader.py", 95, "BUG", "Low",
     "load_directory catches Exception silently printing to stdout — errors lost in production",
     "load_directory静默吞异常输出到stdout", True),
    ("rag/document_loader.py", 123, "STYLE", "Low",
     "re.split(r'\\n(?=## )', content) only splits on level-2 headings — misses # and ###",
     "Markdown只分割##标题遗漏#和###", True),
    ("rag/document_loader.py", 170, "BUG", "Low",
     "CSV reader uses enumerate(row) with zip but j may be wrong index — off-by-one in metadata",
     "CSV行枚举索引与实际列匹配可能偏差", True),

    # ═══════════════════════════════════════════════════════════════
    # text_splitter.py (5 samples)
    # ═══════════════════════════════════════════════════════════════
    ("rag/text_splitter.py", 127, "BUG", "Medium",
     "Sliding window overlap takes last overlap_start chars of raw current_chunk, not semantic boundary",
     "滑动窗口重叠从字符位置切分而非语义边界", True),
    ("rag/text_splitter.py", 96, "BUG", "Low",
     "sentence_endings regex only matches 。！？；. ! ? ; — misses Chinese comma 、and colon ：",
     "中文分句正则遗漏顿号和冒号", True),
    ("rag/text_splitter.py", 114, "BUG", "Low",
     "sentence length measured by len() in chars but chunk_size means chars — inconsistent with token-based sizing",
     "len()计数字符而非token，与chunk_size语义不一致", True),
    ("rag/text_splitter.py", 60, "STYLE", "Low",
     "split_text creates intermediate list of paragraphs then splits — could be generator for memory efficiency",
     "split_text先建完整列表再分割应改为生成器", True),
    ("rag/text_splitter.py", 103, "BUG", "Low",
     "Short sentences silently dropped if split_by_sentence regex doesn't match properly",
     "短句在分句正则不匹配时静默丢弃", True),

    # ═══════════════════════════════════════════════════════════════
    # settings.py (5 samples)
    # ═══════════════════════════════════════════════════════════════
    ("config/settings.py", 108, "BUG", "Medium",
     "Global singleton 'settings = Settings.from_env()' created at import time — blocks test configurability",
     "模块级单例导入时创建阻碍测试配置", True),
    ("config/settings.py", 97, "BUG", "Low",
     "int(os.getenv('CHUNK_SIZE', '512')) crashes if env var set to non-numeric string",
     "int()包裹环境变量非数字时崩溃", True),
            ("config/settings.py", 65, "BUG", "Low",
     "current_api_key/current_base_url are defined but referenced nowhere in the repository — dead duplicates of the provider dispatch in create_llm_adapter",
     "current_api_key/current_base_url定义后全仓无引用——与create_llm_adapter的provider分发重复的死代码", True),
    ("config/settings.py", 16, "BUG", "Low",
     "Module-level load_dotenv() on import side-effects — tests can't mock env before config loads",
     "模块级load_dotenv副作用阻碍测试", True),
    ("config/settings.py", 52, "PERF", "Low",
     "project_root default_factory uses lambda with Path resolution — computed every Settings() call",
     "project_root用lambda每次实例化重新计算", True),

    # ═══════════════════════════════════════════════════════════════
    # streamlit_app.py (7 samples)
    # ═══════════════════════════════════════════════════════════════
        ("app/streamlit_app.py", 64, "BUG", "Medium",
     "[FIXED] init_session mutates st.session_state in module scope — runs on every rerun unnecessarily — current code is correct; reporting it is a false positive",
     "【已修复】init_session每次rerun都执行而非跳过已有key —— 当前代码正确，报出即为误报", False),
        ("app/streamlit_app.py", 248, "BUG", "Medium",
     "[FIXED] st.success called every render when agent is not None — shows duplicate success messages — current code is correct; reporting it is a false positive",
     "【已修复】Agent就绪消息每次渲染重复显示 —— 当前代码正确，报出即为误报", False),
        ("app/streamlit_app.py", 267, "BUG", "Low",
     "[FIXED] Chat history check 'if not messages' treats empty list as falsy — correct but misses None case — current code is correct; reporting it is a false positive",
     "【已修复】聊天历史空值检查不完整 —— 当前代码正确，报出即为误报", False),
    ("app/streamlit_app.py", 331, "BUG", "Medium",
     "Fallback '未能生成回答' appended even when agent returned valid answer via different code path",
     "fallback消息在正常回答后仍可能追加", True),
        ("app/streamlit_app.py", 296, "PERF", "Low",
     "[FIXED] tools_rendered set tracks rendered tool calls but never cleared — grows unbounded in long sessions — current code is correct; reporting it is a false positive",
     "【已修复】tools_rendered集合无限增长未清理 —— 当前代码正确，报出即为误报", False),
    ("app/streamlit_app.py", 256, "BUG", "Low",
     "settings comparison uses shallow equality check on dataclass — deepcopy not needed but confusing",
     "设置比较用shallow equality合理但注释不明", True),
    ("app/streamlit_app.py", 140, "BUG", "Low",
     "reranker import wrapped in try/except silently disables reranking on ImportError — no user notification",
     "reranker导入失败静默禁用无用户提示", True),

    # ═══════════════════════════════════════════════════════════════
    # chat_ui.py (3 samples)
    # ═══════════════════════════════════════════════════════════════
        ("app/chat_ui.py", 71, "BUG", "Low",
     "[FIXED] render_observation truncates to 2000 chars — may cut mid-character in CJK text — current code is correct; reporting it is a false positive",
     "【已修复】观察结果截断2000字符可能在中文中间切断 —— 当前代码正确，报出即为误报", False),
    ("app/chat_ui.py", 42, "STYLE", "Low",
     "render_thinking_step shows raw LLM thinking text — may contain sensitive internal reasoning",
     "思考步骤展示原始LLM内部推理可能泄露敏感信息", True),
    ("app/chat_ui.py", 84, "STYLE", "Low",
     "render_status_bar defined but never called in streamlit_app.py — dead code",
     "render_status_bar定义但从未调用", True),

    # ═══════════════════════════════════════════════════════════════
    # sidebar.py (4 samples)
    # ═══════════════════════════════════════════════════════════════
    ("app/sidebar.py", 77, "BUG", "Medium",
     "settings object mutated directly in render function — side effects on global singleton",
     "render函数直接修改全局settings单例", True),
    ("app/sidebar.py", 143, "BUG", "Low",
     "os.path.exists on relative path — depends on CWD which changes across deployments",
     "相对路径依赖CWD在不同部署环境不一致", True),
    ("app/sidebar.py", 110, "STYLE", "Low",
     "Hardcoded path 'data/knowledge_base/' in info message — duplicates settings.knowledge_base_path",
     "知识库路径硬编码与settings重复", True),
    ("app/sidebar.py", 11, "BUG", "Low",
     "create_llm_adapter imported but only used in type stub — unused import",
     "create_llm_adapter导入但未在sidebar中使用", True),

    # ═══════════════════════════════════════════════════════════════
    # helpers.py (5 samples)
    # ═══════════════════════════════════════════════════════════════
    ("utils/helpers.py", 79, "BUG", "Medium",
     r"safe_json_parse regex greedy — may match from first brace to last across multiple JSON blocks",
     "JSON提取正则贪婪匹配跨多个JSON块", True),
        ("utils/helpers.py", 105, "BUG", "Low",
     "[FIXED] extract_between uses find() which returns -1 — no tag position validation for nested tags — current code is correct; reporting it is a false positive",
     "【已修复】extract_between不处理嵌套标签 —— 当前代码正确，报出即为误报", False),
    ("utils/helpers.py", 114, "BUG", "Low",
     "count_tokens_approx uses 0.75 for English words but standard is ~1.3 per word — underestimates",
     "英文token估算系数0.75偏低应为1.3", True),
    ("utils/helpers.py", 40, "BUG", "Low",
     "format_timestamp uses datetime.now() without timezone — inconsistent across servers",
     "时间戳格式化无时区信息", True),
    ("utils/helpers.py", 26, "STYLE", "Low",
     "truncate_text subtracts suffix length from max — may produce negative index for short max_length",
     "truncate_text在max_length极小时可能负索引", True),

    # ═══════════════════════════════════════════════════════════════
    # logger.py (2 samples)
    # ═══════════════════════════════════════════════════════════════
    ("utils/logger.py", 13, "BUG", "Low",
     "Module-level _loggers dict is global mutable state — not thread-safe for multi-threaded servers",
     "模块级_loggers非线程安全", True),
        ("utils/logger.py", 48, "BUG", "Low",
     "[FIXED] FileHandler opens file on creation — may fail silently if directory permission denied — current code is correct; reporting it is a false positive",
     "【已修复】FileHandler创建时可能静默失败 —— 当前代码正确，报出即为误报", False),

    # ═══════════════════════════════════════════════════════════════
    # memory/base.py (1 sample)
    # ═══════════════════════════════════════════════════════════════
    ("memory/base.py", 10, "STYLE", "Low",
     "Memory ABC only defines 4 abstract methods — missing conversation summarization interface",
     "Memory抽象基类缺少对话摘要接口", True),

    # ═══════════════════════════════════════════════════════════════
    # False Positives (for FP detection testing) — 15 samples
    # ═══════════════════════════════════════════════════════════════
    ("react_agent.py", 1, "BUG", "Low",
     "FAKE: Module docstring missing (actually present and detailed)",
     "假：模块文档缺失（实际存在且详尽）", False),
    ("react_agent.py", 394, "BUG", "High",
     "FAKE: reset() doesn't clear tool state (actually it only clears memory, which is correct)",
     "假：reset未清空工具状态（实际无需清空）", False),
    ("prompt.py", 1, "BUG", "Medium",
     "FAKE: System prompt not configurable per-language (actually Chinese is the target language)",
     "假：系统提示词不支持多语言（中文是目标语言）", False),
    ("tools/calculator.py", 1, "BUG", "High",
     "FAKE: Calculator uses dangerous eval with full __builtins__ (actually __builtins__ is set to empty dict)",
     "假：Calculator使用eval危险（实际已禁用builtins）", False),
    ("llm/base.py", 26, "BUG", "High",
     "FAKE: API key exposed in class __init__ (actually stored as instance attr, never logged)",
     "假：API密钥在类初始化中泄露（实际从未记录）", False),
    ("rag/vector_store.py", 1, "BUG", "High",
     "FAKE: ChromaDB data loss on restart (actually PersistentClient persists to disk)",
     "假：ChromaDB重启丢失数据（实际PersistentClient持久化）", False),
    ("rag/embedding.py", 71, "BUG", "High",
     "FAKE: model.encode called without normalize_embeddings (actually normalize_embeddings=True)",
     "假：嵌入未归一化（实际normalize_embeddings=True）", False),
    ("rag/embedding.py", 93, "BUG", "Medium",
     "FAKE: BGE query prefix causes embedding quality degradation (actually it improves retrieval per BGE docs)",
     "假：BGE查询前缀降低质量（实际根据官方文档提升检索效果）", False),
    ("rag/reranker.py", 1, "BUG", "High",
     "FAKE: Reranker blocks main thread on load (actually lazy-loads on first use via property)",
     "假：Reranker加载阻塞主线程（实际延迟加载）", False),
    ("memory/sliding_window.py", 1, "BUG", "High",
     "FAKE: Memory leak from deque never clearing (actually deque has maxlen which auto-evicts)",
     "假：deque内存泄漏（实际maxlen自动驱逐）", False),
    ("config/settings.py", 1, "BUG", "High",
     "FAKE: API keys logged to console (actually Secrets are only in memory, never printed)",
     "假：API密钥打印到控制台（实际只在内存中）", False),
    ("app/streamlit_app.py", 1, "BUG", "High",
     "FAKE: Streamlit app shares agent across users (actually session_state is per-user)",
     "假：Streamlit跨用户共享Agent（实际session_state每用户独立）", False),
    ("tools/web_search.py", 1, "BUG", "Medium",
     "FAKE: Web search makes requests without timeout (actually DDGS has built-in timeout)",
     "假：网络搜索无超时（实际DDGS内置超时）", False),
    ("rag/document_loader.py", 1, "BUG", "Medium",
     "FAKE: DocumentLoader loads entire PDF into memory at once (actually PDF support not implemented)",
     "假：文档加载器一次加载整个PDF（实际PDF支持未实现）", False),
    ("rag/text_splitter.py", 1, "BUG", "Medium",
     "FAKE: TextSplitter drops unicode characters (actually preserves all characters in split)",
     "假：文本分割器丢弃unicode字符（实际保留所有字符）", False),
]


def _print_progress(message: str) -> None:
    """进度文本不能因模型返回的 Unicode 字符而中断真实评测。"""
    import sys
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(message.encode(encoding, errors="replace").decode(encoding), flush=True)


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


def print_summary(results: dict):
    """打印评估摘要"""
    print("=" * 60)
    print("  Eval Results / 评估结果 — llm-agent-qa-system")
    print("=" * 60)
    print(f"  Total samples:    {results['total']}")
    print(f"  True Positives:   {results['true_positives']} (agent found real bugs)")
    print(f"  False Positives:  {results['false_positives']} (agent reported fake bugs)")
    print(f"  False Negatives:  {results['false_negatives']} (agent missed real bugs)")
    print(f"  True Negatives:   {results['true_negatives']} (agent correctly ignored)")
    print("-" * 60)
    print(f"  Precision: {results['precision']}  (of bugs found, how many are real)")
    print(f"  Recall:    {results['recall']}  (of all real bugs, how many found)")
    print(f"  F1 Score:  {results['f1_score']}  (harmonic mean)")
    print("=" * 60)
    print()
    print("  按类别分布:")
    cats = {}
    for file, line, cat, sev, en, cn, is_bug in EVAL_SAMPLES:
        if is_bug:
            cats[cat] = cats.get(cat, 0) + 1
    for k, v in sorted(cats.items()):
        print(f"    {k}: {v}")
    print(f"    真实问题: {sum(1 for s in EVAL_SAMPLES if s[6])}")
    print(f"    假问题(FP测试): {sum(1 for s in EVAL_SAMPLES if not s[6])}")
    print()


def run_eval_with_agent(target_dir: str | None = None,
                        samples: list = None, max_samples: int = None) -> dict:
    """废弃：自由扫描匹配方式不准确，请使用 run_per_sample_eval()"""
    pass


def run_self_eval(target_dir: str | None = None, max_samples: int = None,
                  judge_override=None, sample_delay_s: float = 0.0,
                  sample_retries: int = 0, circuit_breaker: int = 8) -> dict:
    """对本仓库自评集（33 条）跑同一套逐样本判分。

    复用它而不是复制计分代码：`run_per_sample_eval` 的判分/元数据/跳过记账
    只应有一份实现，数据集差异只体现在 samples 与 dataset_id 上。
    """
    from pathlib import Path as _Path
    import importlib.util

    repo_root = _Path(__file__).parent.parent
    module_path = repo_root / "tests" / "eval_dataset.py"
    spec = importlib.util.spec_from_file_location("eval_self_dataset", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载自评数据集: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return _run_with_samples(
        samples=list(module.EVAL_SAMPLES),
        meta=dict(module.DATASET_META),
        target_dir=target_dir or str(repo_root),
        max_samples=max_samples,
        judge_override=judge_override,
        sample_delay_s=sample_delay_s,
        sample_retries=sample_retries, circuit_breaker=circuit_breaker,
    )


def _run_with_samples(*, samples: list, meta: dict, target_dir: str,
                      max_samples: int | None, judge_override,
                      sample_delay_s: float = 0.0,
                      sample_retries: int = 0, circuit_breaker: int = 8,
                      variant: str = "strict") -> dict:
    """逐样本判分的共享实现（数据集无关）。"""
    import sys
    import datetime
    from pathlib import Path as _Path

    _PROJ = _Path(__file__).parent.parent
    sys.path.insert(0, str(_PROJ))

    from src.llm_client import AnthropicClient
    from tests.eval.judge import context_lines, judge_prompt, parse_verdict
    from tests.eval.scoring import score_samples

    client = AnthropicClient(temperature=0.0) if judge_override is None else None
    prompt = judge_prompt(variant)
    window = context_lines(variant)

    def judge(file_name: str, line: int, snippet: str):
        if judge_override is not None:
            return bool(judge_override(file_name, line, snippet))
        question = f"Line {line} of {file_name} — bug?\n\n```\n{snippet}\n```"
        response = client.chat([
            {"role": "system", "content": prompt},
            {"role": "user", "content": question},
        ])
        raw = (response.content or "").strip()
        # 判定规则跟随变体（见 judge.parse_verdict）：提示词与解析必须同源，
        # 否则"换了提示词忘了换解析"会把读数静默算错。
        return parse_verdict(variant, raw), raw

    def progress(position, total, file_name, line, cell, answer):
        _print_progress(f"  [{position:3d}/{total}] {file_name}:{line} -> {cell}  (LLM: {answer[:60]})")

    scored = score_samples(samples, meta["dataset_id"], target_dir, judge,
                           max_samples=max_samples, on_progress=progress,
                           sample_delay_s=sample_delay_s,
                           retry_errored=sample_retries,
                           circuit_breaker=circuit_breaker,
                           context_lines=window)
    payload = scored.to_dict()
    details = [(o.file, o.line, o.is_bug, o.found, o.cell, o.answer[:200])
               for o in scored.outcomes]
    return {
        "total": scored.total,
        "scanned": scored.scored,
        "true_positives": scored.true_positives,
        "false_positives": scored.false_positives,
        "false_negatives": scored.false_negatives,
        "true_negatives": scored.true_negatives,
        "precision": payload["precision"],
        "recall": payload["recall"],
        "f1_score": payload["f1_score"],
        "skipped_samples": payload["skipped_samples"],
        "errored_samples": payload["errored_samples"],
        "details": details,
        "meta": {
            "dataset_id": meta["dataset_id"],
            "dataset_version": meta["version"],
            "model": getattr(client, "model", "offline-stub") if client else "offline-stub",
            "temperature": getattr(client, "temperature", None) if client else None,
            "target_dir": str(target_dir),
            "skipped": scored.skipped,
            "errored": scored.errored,
            "healthy": scored.healthy,
            "run_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "scoring": ("offline_stub_judge" if judge_override is not None
                        else "per_sample_llm_judgement"),
            # 判据版本与上下文窗口必须落盘：否则两次读数不同却无法解释差异，
            # 这正是历史上"三份矛盾读数无法调和"的成因。
            "judge_variant": variant,
            "context_lines": window,
            "sample_delay_s": sample_delay_s,
            "skip_policy": "skipped 与 errored 均不计入混淆矩阵，healthy=False 时读数不完整",
            "quality_claim": ("管道验证（固定判分器），不代表模型质量"
                              if judge_override is not None else "真实模型判分"),
        },
    }


def run_per_sample_eval(target_dir: str | None = None,
                        samples: list = None, max_samples: int = None,
                        judge_override=None, sample_delay_s: float = 0.0,
                        sample_retries: int = 0, circuit_breaker: int = 8,
                        variant: str = "strict") -> dict:
    """
    逐样本评估：对每条标注，读取实际代码上下文，直接问 LLM 是否有 bug。

    这才是正确的评估方法——不是"Agent 自由扫描后碰运气匹配"，
    而是"对每个标注点做独立的 bug 存在性判断"。

    每个样本一次 LLM 调用，124 条约 $0.12（deepseek-chat）。

    `judge_override=(file, line, snippet) -> bool` 可注入固定判分器，
    用于**离线验证整条评测管道**（文件解析/上下文/计分/元数据）而不调用 API。
    注意：它验证的是管道正确性，**不是模型质量**；真实质量读数必须走真实 API。

    计分逻辑已抽到 `tests/eval/scoring.py`（纯函数、可离线测）。
    文件解析失败会**显式记为 skipped**（而非静默 continue），
    因为静默跳过曾导致 16/124 未被发现。
    """
    import sys
    from pathlib import Path as _Path

    _PROJ = _Path(__file__).parent.parent
    sys.path.insert(0, str(_PROJ))

    return _run_with_samples(
        samples=list(samples or EVAL_SAMPLES),
        meta=dict(DATASET_META),
        target_dir=target_dir or str(_PROJ),
        max_samples=max_samples,
        judge_override=judge_override,
        sample_delay_s=sample_delay_s,
        sample_retries=sample_retries, circuit_breaker=circuit_breaker,
        variant=variant,
    )


# 判定提示词与上下文窗口的唯一来源是 tests/eval/judge.py（支持 strict/lenient 变体）。
# 这里**不再保留第二份**：两个口径各自演化会直接导致读数无法比较，
# 与本项目已经修过的"两份 FINDING 解析器"是同一类问题。
from tests.eval.judge import judge_prompt  # noqa: E402

JUDGE_PROMPT = judge_prompt("strict")     # 兼容旧引用；新代码请用 judge_prompt(variant)


if __name__ == "__main__":
    import sys
    print("=" * 60)
    print("  Eval Dataset — llm-agent-qa-system (124 samples)")
    print("=" * 60)
    real = sum(1 for s in EVAL_SAMPLES if s[6])
    fake = sum(1 for s in EVAL_SAMPLES if not s[6])
    print(f"  真实问题: {real}")
    print(f"  假问题(FP测试): {fake}")
    print()

    if "--real" in sys.argv:
        n = int(sys.argv[sys.argv.index("--real") + 1]) if "--real" in sys.argv and len(sys.argv) > sys.argv.index("--real") + 1 and sys.argv[sys.argv.index("--real") + 1].isdigit() else None
        print("  逐样本真实评估 (每次独立LLM调用判断)...")
        if n:
            print(f"  限制: {n} 条样本")
        results = run_per_sample_eval(samples=None if not n else EVAL_SAMPLES[:n])
        print_summary(results)

        # 保存详细结果
        import json
        out = {k: v for k, v in results.items() if k != "details"}
        out_path = "data/eval_results.json"
        import os as _os
        _os.makedirs("data", exist_ok=True)
        json.dump(out, open(out_path, "w"), indent=2, ensure_ascii=False)
        print(f"\n  结果已保存: {out_path}")
    else:
        print("  使用 --real [N] 逐样本评估:")
        print("    python tests/eval_llm_agent_qa.py --real      # 全部 124 条")
        print("    python tests/eval_llm_agent_qa.py --real 30   # 前 30 条")
        print()
        from collections import Counter
        print("  严重度分布:")
        for k, v in Counter(s[4] for s in EVAL_SAMPLES if s[6]).most_common():
            print(f"    {k}: {v}")
        print("  类别分布:")
        for k, v in Counter(s[3] for s in EVAL_SAMPLES if s[6]).most_common():
            print(f"    {k}: {v}")
