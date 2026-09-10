# 企业级 Agent 测评体系 — 实施方案与项目改造计划

> 2026-09-04 | 适用项目:code-review-agent(Multi-Agent 代码审查)
> 目标岗位:AI 智能体评测(评测集/指标/报告/异常链路/问题闭环)

---

## 0. 现状诊断(盘点结论)

| 能力 | 现状 | 证据 | 判定 |
|------|------|------|------|
| 结果评测(数据集+指标) | 124 条标注集(含 16 条 FP 陷阱)→ P/R/F1 + 多模型对比 | [eval_llm_agent_qa.py](../../tests/eval_llm_agent_qa.py#L481)、README 结果表 | ✅ 已有,需工程化 |
| 节点级统计 | 每节点 turns/tools/tokens/elapsed_ms 已采集 | `_node_stats()` [langgraph_orchestrator.py:195](../../src/multi_agent/langgraph_orchestrator.py#L195) | ⚠️ 只在内存,未持久化 |
| 遥测日志 | 84 个 JSONL,但多 Agent 运行**只有 session_start 一行** | 全量扫描:tool_call_end 500 / turn 135 / session_start 84 | ❌ 关键缺口 |
| 角色归因 | 事件无 planner/executor/reviewer/fixer 标记 | telemetry 事件字段无 role | ❌ 关键缺口 |
| 失败分类 | 仅 error 事件(9 条),无 taxonomy | logs/ 全量 | ❌ 缺失 |
| 评测复现 | 目标路径硬编码 `X:/VScode/...` | [eval_llm_agent_qa.py:544](../../tests/eval_llm_agent_qa.py#L544) | ❌ 换机即废 |
| CI 评测门禁 | eval 仅做 `import` 检查,等于没跑 | [ci.yml](../../.github/workflows/ci.yml) | ⚠️ 形同虚设 |
| 故障注入 | 零散回归(重试/逃逸/映射),无体系 | tests/ 现有 116 用例 | ⚠️ 需成体系 |
| 报告产物 | reports/ 15+ 份运行报告,但无评测报告 | reports/*.md | ⚠️ 需补评测口径 |
| 稳定性/成本 | 无重复运行、无方差、无成本口径 | — | ❌ 缺失 |

**核心结论**:结果层评测已达同届上游(真数据、真故障复盘、真 CI);短板集中在**过程层(轨迹/工具调用/失败归因)**与**工程化(复现、门禁、报告)**——这恰是 JD 点名的部分。

---

## 1. 目标与原则

**四个目标**:
1. **可复现** — 任何一台机器、任何一次 CI,一条命令跑出同一套评测
2. **可归因** — 每个失败能自动定位到具体节点(plan/execute/review/fix/verify)与失败类别
3. **可回归** — 指标跌穿阈值时 CI 拦截;每个生产事故沉淀为回归用例
4. **可度量** — 结果/轨迹/效率/稳定性四层指标,历史趋势可追踪

**四条原则**:
- **分层跑批**:离线 FakeLLM(每次 CI,零成本)→ 真实 API 抽样(每晚)→ 全量多模型(手动,出对比表)
- **规则优先判定**:结果判定优先规则匹配,LLM 裁判兜底,人工抽检 1%(三层裁判)
- **零回归改造**:所有代码改动关键字参数默认 `None`,行为与改造前逐字节一致(沿用本项目既有 byte-identical 测试守卫模式)
- **用例即资产**:用例有 id、标签、来源、闭环链,不允许"孤儿用例"

---

## 2. 体系架构(六层)

```
┌─ L6 门禁层  CI 阈值拦截 / nightly 定时 / 手动全量 ────────────┐
├─ L5 报告层  eval_report.md(混淆矩阵/轨迹指标/趋势/失败Top) ─┤
├─ L4 归因层  失败分类 taxonomy × telemetry 事件溯源           │
├─ L3 执行层  runner: offline(FakeLLM) | replay(真实API抽样)  │
├─ L2 指标层  结果 / 轨迹 / 效率成本 / 稳定性                 │
└─ L1 数据层  主链路集 / 边界集 / 故障注入集 / 对抗集 / 回归集 ┘
```

---

## 3. 数据集设计

### 3.1 分类矩阵(五集,初始目标 35+ 用例)

| 集合 | 目标数 | 覆盖内容 | 判定方式 |
|------|--------|----------|----------|
| **主链路** | 5 | 正常项目全流程、无 bug 仓库、只读模式、auto-fix 开、上传副本修复 | 规则断言 |
| **边界** | 8 | 空目录、单文件、>1MB 大文件、CJK 文件名/内容、CRLF/BOM、二进制混入、深层目录、符号链接 | 规则断言 |
| **故障注入** | 10 | 畸形 tool_call JSON、未知工具名、工具抛异常、工具超时、空结果、429/5xx 风暴、连接被拒(10013)、流式中断、重复调用死循环、上下文超限 | FakeLLM 注入 + 行为断言 |
| **对抗/精度** | 4 | 假 bug 陷阱(FP)、语义级 bug(漏报探测)、干净仓库、TODO 噪音 | 与标注比对 |
| **回归** | 持续追加 | 每个生产事故 ≥1 条(现有模板:10013 → 2 条回归) | 规则断言 |

### 3.2 统一用例 Schema

```python
@dataclass(frozen=True)
class EvalCase:
    id: str                    # EV-001(全局唯一,只增不改)
    name: str                  # 一句话描述
    layer: str                 # result | trajectory
    tags: tuple[str, ...]      # boundary / fault / adversarial / regression / happy
    setup: str                 # 环境构造说明(空目录/注入点/样本文件)
    inject: dict | None        # 故障注入配置(FakeLLM 场景),主链路为 None
    expect: dict               # 断言: 终态断言 + 轨迹断言(见 5.x 指标)
    source: str                # labeling | incident:<链接> | injected
    severity: str              # blocker / major / minor
    added: str                 # 加入日期(YYYY-MM-DD)
```

**存储结构**:

```
tests/eval/
├── __init__.py
├── cases.py            # EvalCase 定义 + 五集合清单
├── faults.py           # FakeLLM 故障注入场景(复用 RecordingMockLLM 模式)
├── runner.py           # 执行器: offline | replay 两模式, 输出 JSON
├── report.py           # 生成 reports/eval_<date>.md
└── test_eval_suite.py  # 评测体系自测(CI 每次跑 offline 模式)
```

## 4. 指标体系

### 4.1 结果指标(已有,形式化)

| 指标 | 公式 | 数据来源 | 目标 |
|------|------|----------|------|
| Precision | TP/(TP+FP) | 标注集比对 | ≥ 90% |
| Recall | TP/(TP+FN) | 同上 | ≥ 60%(现状基线) |
| F1 | 2PR/(P+R) | 同上 | 不劣于基线 -5% |
| per-node 检出率 | 节点贡献的 TP / 总 TP | 需 P0 归因 | 建立基线 |

### 4.2 轨迹指标(新建,这是 JD 核心)

| 指标 | 定义 | 数据来源 | 目标 |
|------|------|----------|------|
| Plan 可执行率 | planner 输出可解析出合法 finding 的 run 占比 | 遥测 role=planner 输出 + 解析器 | ≥ 95% |
| 工具调用成功率 | 1 − tool_call_end.error 占比 | `tool_call_end.error`(已有 500 条历史) | ≥ 98% |
| 参数合法率 | 通过 schema 校验的工具调用占比 | tool_call_start args + ToolDefinition 校验 | 建立基线 |
| 空转率 | 无 tool_call 且未终结的 turn / 总 turn | turn_start + turn_end | ≤ 10% |
| 循环率 | 同一 (tool, args) 重复 ≥3 次的会话占比 | tool_call_start 序列 | 0 |
| VERDICT 解析率 | reviewer 输出含合法 VERDICT 行占比 | role=reviewer 输出 | ≥ 95% |
| fix 成功率 | verify_fix 通过 / fix 触发 | node_stats verify_fix | ≥ 70% |
| 回归引入率 | fix 后行为验证失败占比 | verify_fix 结果 | 建立基线 |
| 端到端成功率 | 无 error 事件完成的 run 占比 | session_end + error | ≥ 90% |

### 4.3 效率与成本指标

| 指标 | 数据来源 | 说明 |
|------|----------|------|
| turns/任务、tokens/任务 | node_stats(已有) | 按节点拆解 |
| 延迟 p50/p95 | turn_end 时间戳差 / node elapsed_ms | 分节点、分模型 |
| 成本/任务 | usage × 模型单价表(新增 `src/eval/pricing.py`) | 让评测带上钱的口径 |

### 4.4 稳定性指标

| 指标 | 定义 |
|------|------|
| pass@k | 同一用例重复 k=5 次全部通过的占比 |
| 结果方差 | 同一用例 P/R 的 run 间标准差 |
| flaky 率 | 结果在 k 次间翻转的用例占比(必须归零或标记) |

### 4.5 失败分类 Taxonomy(L4 归因层)

```
F-01 格式解析失败   LLM 输出无法解析为预期结构(FINDING/VERDICT/tool_call)
F-02 幻觉工具名     调用了不存在的工具
F-03 参数错误       工具参数缺字段/类型不符
F-04 工具执行异常   工具内部抛错(含超时)
F-05 模型API故障    429/5xx/529 重试耗尽、连接失败(含 10013)
F-06 空转/死循环    无进展 turn 连续 ≥3 或无工具产出
F-07 上下文超限     请求被拒(超长)
F-08 状态污染       run 间状态残留(如 _role_blocks/_write_receipts 未重置类)
F-09 平台差异       仅特定 OS/环境复现(如 Windows skip 遮蔽类)
```

每条失败事件在报告中自动归入一类,并给出 Top 失败类分布——这是"复现、定位逻辑异常/决策错误"的直接产出。

---

## 5. 实施步骤(Phase 0–6,每阶段含验收标准)

### Phase 0 — 遥测补全(前置,1 人日)⭐ 全部轨迹指标的前提

**问题**:`_make_agent` 创建的 agent 无 logger → 多 Agent 运行日志几乎为空;事件无角色归因。
**任务**:
1. `AgentLogger` 增加 `role` 参数与 `for_role(role)` 视图方法(共享同一文件/锁,事件带 `role` 字段;`role=None` 时字段不写入 → 零回归)
2. `AgentLogger` 新增 `node_stats(stats: dict)` 事件方法,run 结束时持久化节点统计
3. `LangGraphOrchestrator.__init__(..., logger=None)`;`_make_agent` 传 `logger=self.logger.for_role(role)`;`run()` 末尾 `self.logger.node_stats(...)`
4. `factory.create_langgraph_orchestrator(..., logger=None)` 透传;`streamlit_app.py` / `server.py` 把已建的 AgentLogger 传入(各一行)

**验收**:真实跑一次多 Agent 流程,JSONL 含带 role 的 turn/tool 事件与 node_stats;`logger=None` 时全部现有测试绿(byte-identical 守卫)。

### Phase 1 — 指标库 + 基线报告(1 人日)

**任务**:`src/eval/log_parser.py`(JSONL → 事件流,兼容无 role 旧日志)+ `src/eval/metrics.py`(4.2–4.4 指标计算 + F-01~F-09 分类);扫描现有 84 个日志 + 重跑 5 个样本,产出**基线报告** `reports/eval_baseline_<date>.md`。
**验收**:报告含全部指标数值与 Top 失败类;旧日志(无 role)可降级统计不报错。

### Phase 2 — 故障注入用例集(1.5 人日)

**任务**:`tests/eval/faults.py` 实现 10 条注入场景(基于 FakeLLM 返回畸形输出/抛错/死循环);每条断言**有界降级行为**(不崩、错误可读、turn 有上限)。
**验收**:10 条用例全绿;故意破坏降级逻辑时对应用例必红(变异验证)。

### Phase 3 — 统一 Runner + 报告生成(1.5 人日)

**任务**:`tests/eval/runner.py` 支持 `--suite {offline,replay,full} --model --report`;离线模式进 CI,replay 模式真实调 API 抽样(默认 20 条);`report.py` 生成含混淆矩阵、轨迹指标、失败 Top、与上次对比(趋势)的 markdown。
**验收**:`python -m tests.eval.runner --suite offline --report` 一条命令出报告;replay 模式在真实 API 下可跑通并落盘。

### Phase 4 — CI 门禁(0.5 人日)

**任务**:`ci.yml` 增加 step:`pytest tests/eval/test_eval_suite.py`(offline)+ 阈值校验脚本(P<90% 或工具成功率<98% 则 fail);`eval_llm_agent_qa.py` 去掉硬编码路径改为参数。
**验收**:本地构造一次指标劣化,CI 变红;恢复正常后转绿。(已有先例:CI 首次运行即抓出 Windows skip 遮蔽的 symlink 测试缺陷——平台差异必须由 CI 兜底。)

### Phase 5 — 稳定性与成本(1 人日)

**任务**:runner 支持 `--repeat k`;温度固定 0 的可复现模式;统计 pass@k/方差/flaky;接入定价表输出成本列。
**验收**:基线报告含 pass@5 与成本/任务。

### Phase 6 — 扩展评测(1.5 人日,按需)

**任务**(二选一或都做):
- **fix 质量评测**:对注入 bug 样本开 auto-fix,评"修复率+回归引入率+修复无损率"
- **检索评测**(为 RAG 加分项正名):`vector_store` 是 FTS5 关键词方案,如升级 embedding 则补 recall@k 检索质量评测
**验收**:对应报告章节产出。

**总计约 8 人日**(可并行压缩到一周)。

---

## 6. 项目修改方案(文件级)

### 6.1 新增文件

| 文件 | 内容 |
|------|------|
| `src/eval/__init__.py` | 包入口(re-export) |
| `src/eval/log_parser.py` | JSONL → 事件流;兼容旧日志(无 role 降级) |
| `src/eval/metrics.py` | 四层指标计算 + 失败分类(纯函数,零项目依赖) |
| `src/eval/pricing.py` | 模型单价表 + 成本换算 |
| `tests/eval/cases.py` | EvalCase schema + 五集合用例 |
| `tests/eval/faults.py` | FakeLLM 故障注入场景 |
| `tests/eval/runner.py` | 执行器(offline/replay/repeat) |
| `tests/eval/report.py` | 评测报告生成器 |
| `tests/eval/test_eval_suite.py` | 评测体系自测(进 CI) |
| `docs/eval-report-template.md` | 报告模板 |
| `reports/eval_*.md` | 产出物(基线/回归) |

### 6.2 修改文件

| 文件 | 改动 | 兼容性 |
|------|------|--------|
| [telemetry.py](../../src/harness/telemetry.py) | `AgentLogger(role=None)` + `for_role()` + `node_stats()` 事件 | `role=None` 不写字段,行为不变 |
| [langgraph_orchestrator.py](../../src/multi_agent/langgraph_orchestrator.py) | `__init__(..., logger=None)`;`_make_agent` 接 role 级 logger;`run()` 末持久化 node_stats | 默认 None → 零注入 |
| [factory.py](../../src/multi_agent/factory.py) | keyword-only `logger=None` 透传 | 现有三入口调用不变 |
| [streamlit_app.py](../../src/app/streamlit_app.py) | 已有 `AgentLogger(LOGS_DIR)` 传入工厂(1 行) | 无 UI 变化 |
| [server.py](../../src/api/server.py) | 同上(1 行) | 无接口变化 |
| [eval_llm_agent_qa.py](../../tests/eval_llm_agent_qa.py) | 去硬编码路径 → CLI 参数;输出 JSON 供 runner 消费 | 保留旧调用方式兜底 |
| [eval_dataset.py](../../tests/eval_dataset.py) | 迁移进 `tests/eval/cases.py`(保留原文件兼容) | 渐进迁移 |
| [ci.yml](../../.github/workflows/ci.yml) | 增加 offline 评测 + 阈值门禁 step | 失败才拦截 |

### 6.3 零回归策略

沿用本项目已验证的三板斧:
1. **byte-identical 守卫**:`logger=None`/`role=None` 时系统提示与输出逐字节不变(参照 [test_skills_orchestrator.py](../../tests/test_skills_orchestrator.py#L62) 的 no-op 断言模式)
2. **新旧日志双兼容**:log_parser 对无 role 旧日志降级统计
3. **每个改造点配回归测试**:本次三个错误处理修复的回归测试([test_telemetry.py](../../tests/test_telemetry.py) 等)即为模板

### 6.4 数据集治理规则

- 用例 id 全局唯一、只增不改(废弃打 `deprecated` 标签保留)
- 每个生产事故必须闭环成链:**事故记录 → 修复 commit → 回归用例 → 评测用例**(现有 10013 案例即完整模板:[frontend-e2e-run-2026-08-17.md](frontend-e2e-run-2026-08-17.md) → commit → 2 条回归测试)
- 数据集版本化:每次增删用例在报告头部记录版本号与变更摘要

---

## 7. 风险与对策

| 风险 | 对策 |
|------|------|
| 真实 API 评测成本高/限速 | 分层:CI 只跑 FakeLLM;replay 抽样 20 条;全量多模型手动触发 |
| LLM 非确定性导致 flaky | 温度 0 + `--repeat k` + flaky 用例单独标记隔离 |
| 数据集太小(124/50) | 生产日志转化:从 logs/ 的 error 事件反向构造用例(9 条 error 即 9 个候选) |
| 平台差异(Windows/Linux) | CI(Ubuntu)为准,本地新用例先经 junction/容器验证(已交学费) |
| 改造触碰主链路 | 阶段化:Phase 0 单独提交 + 全量回归 + byte-identical 守卫 |

---

## 8. 时间表(10 个工作日)

| 日 | 内容 | 产出 |
|----|------|------|
| D1 | Phase 0 遥测补全 + 回归 | 带 role 的日志样本 |
| D2 | Phase 1 指标库 | `src/eval/metrics.py` |
| D3 | Phase 1 基线报告 | `reports/eval_baseline_*.md` |
| D4–D5 | Phase 2 故障注入 10 条 | `tests/eval/faults.py` 全绿 |
| D6–D7 | Phase 3 Runner + 报告生成 | 一条命令出报告 |
| D8 | Phase 4 CI 门禁 | 劣化即红的 CI |
| D9 | Phase 5 稳定性/成本 | pass@k + 成本列 |
| D10 | Phase 6 扩展 + 文档 | fix 质量或检索评测章节 |

---

## 9. 与岗位 JD 的映射(自检)

| JD 要求 | 本方案对应 |
|---------|-----------|
| 针对规划/工具调用/任务链路设计评测集 | Phase 0+1+2(轨迹指标 + 故障注入) |
| 整理评测指标,输出评测报告 | Phase 1+3(四层指标 + 自动报告) |
| 边界 case、异常链路测试 | Phase 2(10 类故障注入)+ 边界集 |
| 复现、定位逻辑异常/决策错误/任务失败 | L4 归因层(F-01~F-09)+ telemetry 溯源 |
| 整理评测数据集,维护测试用例库 | 3.2 Schema + 6.4 治理规则 |
| 沉淀评测流程 | Phase 4 CI 门禁 + 报告趋势 |
| 自动化测试脚本 | 全部 pytest + CI |
| 多 Agent 编排评测(加分) | 节点级成功率 + 端到端链路指标 |
| RAG 评测(加分) | Phase 6 检索评测(需先补 embedding) |
