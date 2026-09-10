# 企业级 Agent 测评体系 — 实施方案与项目改造计划

> 2026-09-10 | 适用项目:code-review-agent(Multi-Agent 代码审查)
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

### 4.6 指标-阶段对照(每个指标在第几阶段落地)

| 指标 | 计算落地 | 依赖的前置采集(P0 增量) | 用例验证(P2) | 门禁(P4) |
|------|----------|--------------------------|--------------|-----------|
| Plan 可执行率 | P1 | planner 输出/解析结果埋点(存成败与计数,不存全文) | 畸形 FINDING 注入 | — |
| 工具调用成功率 | **P1(历史 500 条 tool_call_end 即可出基线)** | 无(已有);P0 后可按 role 拆分 | 工具抛异常/超时/未知工具名 | ✅ ≥98% |
| 参数合法率 | P1 | ⚠️ tool_call_start 的 args 现被 `_truncate_dict` 截断,需改存"schema 校验结果"而非原始 args | 参数缺字段/类型错注入 | — |
| 空转率 | P1 | role + turn 事件(P0) | 无工具空转注入 | — |
| 循环率 | P1 | ⚠️ 历史日志中 tool_call_start **为 0 条**(扫描发现,采集缺口比预想大),需 P0 补齐并记录 args 指纹 | 重复相同调用注入 | 0 |
| VERDICT 解析率 | P1 | reviewer 解析结果埋点(布尔/计数) | 畸形 VERDICT 注入 | — |
| fix 成功率 | P1 | node_stats 持久化(P0) | 修复失败注入 | — |
| 回归引入率 | P1 | 同上 | 修复破坏既有行为注入 | — |
| 端到端成功率 | P1 | 无(现有 session_end/error 可算;P0 补全后覆盖多 Agent 运行) | — | ✅ ≥90% |

**读法**:P0 只解决"数据从哪来";P1 统一出数(含基线);P2 用故障注入证明每个指标**真能抓住对应故障**;P4 只把两个硬指标(工具成功率、端到端成功率)变成门禁;P5 之后所有指标附带稳定性口径(k 次重复的方差)。fix 类指标的深度版(真实 auto-fix 质量)在 P6。

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

> **实施记录(2026-09-10,commit f0433ce)**:已完成,含三项计划外但必要的补全——
> (a) `AgentHarness._execute_single_tool` 此前**从不发 tool_call_start**(历史 84 个日志 0 条),已补齐并统一出口;
> (b) HITL 拦截路径此前完全静默,现落 `failure_kind=hitl_blocked`;
> (c) `TimedToolCall.__enter__ + execute()` 双记 tool_call_start(评测集已知缺陷)已修复。
> 另修:参数校验只观测不拦截(参数合法率数据源),`session_end` 增 `fix_status` 计数(fix 类指标数据源)。
> 测试:`tests/test_telemetry_roles.py`(8)+`tests/test_harness_telemetry.py`(10),全量 133 passed。

### Phase 1 — 指标库 + 基线报告(1 人日)

**任务**:`src/eval/log_parser.py`(JSONL → 事件流,兼容无 role 旧日志)+ `src/eval/metrics.py`(4.2–4.4 指标计算 + F-01~F-09 分类);扫描现有 84 个日志 + 重跑 5 个样本,产出**基线报告** `reports/eval_baseline_<date>.md`。
**验收**:报告含全部指标数值与 Top 失败类;旧日志(无 role)可降级统计不报错。

> **实施记录(2026-09-10)**:已完成——`src/eval/{log_parser,metrics,report}.py`,
> 产出 `reports/eval_baseline_20260910.md`(84 个历史日志),测试 `tests/test_eval_metrics.py`(19)。
> 基线读数:工具调用成功率 **99.0%**(495/500)✅、空转率 **0.0%**(0/135)✅、
> 端到端成功率 **8.3%**(7/84,缺口即 P0 前的未收尾运行 77 个)、失败分布 F-04×5 + F-05×4。
> 其余指标 n/a 且标注原因(历史日志无 role/node_stats/parse_result/tool_call_start)。
> **方法学要点**:测不到的指标一律 `None`+原因,不用 0 冒充;未收尾运行归"采集缺口"
> 而不计入 F-08,否则日志问题会被伪装成模型失败。

> **真实运行验证(2026-09-10)**:用真实 LLM 跑 2 次多 Agent 审查(task=审 `src/eval`,
> 产出 `reports/eval_real_run_20260910.md`),用真实日志反查指标可信度。读数:
> 工具调用成功率 **100.0%**(153/153)、参数合法率 **100.0%**(153/153,该分母只有 P0 后新日志才有)、
> Plan 可执行率 **100.0%**(2/2)、VERDICT 解析率 **100.0%**(34/34)、空转率 **0.0%**(0/115)、
> 循环率 **0.0%**(0/2)、端到端成功率 **100.0%**(2/2)、总 token 241,642(≈120k/次)。
> **真实运行暴露的指标空白**(已补):(a) `_force_finish` 兜底路径此前无任何指标覆盖——
> 现补 `node_stats.max_turns` + `forced_finish` 事件 + `turn 预算耗尽率` 指标,实测
> **42.1%**(8/19 节点用满 turns 上限,其中 5 次末轮仍在调工具被强制收尾);(b) 新增
> `parallel_efficiency`(节点耗时合计 ÷ 挂钟)实测 **2.2×**,量化 Send 并行的真实收益。
> **未测到则如实标注**:fix 类指标需 `--auto_fix` 运行、成本需 Phase 5 单价表,报告里保持 n/a
> ——不猜价格、不用 0 冒充。样本量提醒:run 级指标 n=2,统计上不足以支撑结论,只作链路验证。

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

---

## 10. 各阶段技术要点与面试问答

### 10.0 通用回答框架(先记这个)

- **万能五步**:定义指标 → 设计用例 → 自动化执行 → 归因报告 → 门禁回归。任何评测问题都往这个框架里装。
- **数据说话**:先给数字,再给结论。例如"我先扫了 84 个日志文件,发现 82 个只有一行 session_start——采集不全,后面所有指标都是空中楼阁,所以我先做 Phase 0"。
- **诚实边界**:没做过的说"当前方案是 X,升级路径是 Y",不硬编经历。面试官问深了,真诚的"这是下一步计划"比含糊遮掩加分。

### Phase 0 — 遥测补全

**技术清单**:

| 技术点 | 说明 |
|--------|------|
| 结构化日志(JSONL) | 每行一条 JSON,事件类型 + 字段演进(新增可选字段不破坏旧解析) |
| 向后兼容的 API 设计 | keyword-only 参数默认 `None`;`role=None` 时字段不写入,行为逐字节不变 |
| 依赖注入 | logger 从入口(Streamlit/API)→ 工厂 → 编排器 → 各节点 agent 传递 |
| 视图对象模式 | `for_role(role)` 返回共享同一文件/锁的轻量视图,避免多文件碎片 |
| 并发写安全 | `threading.Lock` 串行化写入(已有,需保证并行 Send 节点下的正确性) |
| LangGraph 执行模型 | Send fan-out 并行子状态、节点级状态合并(reducer) |
| 兼容性测试 | byte-identical 守卫 + 全量回归(参照 `test_skills_orchestrator.py` 模式) |

**面试问答**:

> **Q:为什么评测工作要先改代码,而不是直接跑评测?**
> A:我先盘点了现有数据基础:84 个日志文件里 82 个只有一行 session_start,节点统计只在内存里不落盘,事件没有角色归因。评测的前提是数据——没有过程数据,就只能评"最终对不对",评不了"哪一步开始错"。所以我第一阶段是补齐遥测,而不是急着写评测脚本。

> **Q:给核心链路加日志,怎么保证不引入回归?**
> A:三个手段:一是所有新参数 keyword-only 且默认 None,不传就和改造前逐字节一致;二是有 byte-identical 守卫测试,专门断言"无遥测时输出不变";三是 Phase 0 单独一个 PR,全量回归通过才合并。

> **Q:多 Agent 并行节点(executor 分片并行)的日志怎么归因?**
> A:每个节点创建一个绑定了 role 的 logger 视图,共享同一个文件句柄和锁,事件带角色标签。这样并行执行时事件可以乱序到达,但每条都能回答"是哪个角色的哪一步"。

### Phase 1 — 指标库 + 基线报告

**技术清单**:

| 技术点 | 说明 |
|--------|------|
| 日志解析 | JSONL → 事件流;`errors="replace"` 容错;旧日志无 role 时降级统计 |
| 统计计算 | 混淆矩阵、P/R/F1、分位数 p50/p95(手写或 `statistics`) |
| 纯函数模块设计 | `metrics.py` 零项目依赖、不碰 IO,输入事件流输出指标 dict——可测试性第一 |
| 失败分类 | 规则引擎:按事件特征映射到 F-01~F-09 taxonomy |
| 报告生成 | markdown 模板拼装,产出 `reports/eval_baseline_*.md` |

**面试问答**:

> **Q:你已经有 P/R/F1 了,为什么还要做一套指标?**
> A:结果指标回答"错没错",轨迹指标回答"错在哪"。比如 Recall 掉了 10 个点,只看结果不知道是 planner 计划没产出来、executor 工具调用失败、还是 reviewer 漏判。我加了节点级指标之后,失败的归因路径是:指标异常 → 失败分类 → 翻该节点的遥测事件 → 定位到具体 turn。

> **Q:指标怎么定义才算"可复现"?**
> A:每个指标三件套:明确的公式、明确的数据来源字段、明确的目标阈值。比如"工具调用成功率 = 1 − tool_call_end.error 占比,数据来自 telemetry 的 error 字段,目标 ≥98%"。换个人跑,结果必须一样。

> **Q:基线报告有什么价值?没有对比对象啊。**
> A:基线本身就是对比对象。后面每次改动 prompt、换模型、接 skill,都拿新报告和基线比,才能回答"这次改动到底变好了还是变差了"。没有基线,所有优化都是感觉。

### Phase 2 — 故障注入用例集

**技术清单**:

| 技术点 | 说明 |
|--------|------|
| Mock/Fake 注入 | 用 FakeLLM 替掉真实模型(duck typing,项目已有 RecordingMockLLM 先例) |
| 故障注入思想 | 按故障位置分层:LLM 输出层 / 工具层 / 网络层 / 状态层 |
| 有界降级断言 | 断言"不崩 + 有上限 + 错误可读",而不是断言具体错误文本 |
| 变异测试 | 故意破坏降级逻辑,验证对应用例必须变红——证明用例真的有效 |
| 边界设计 | 空输入、超长输入、非法字符、并发、重复调用 |

**面试问答**:

> **Q:异常场景千千万,你怎么决定测哪些?**
> A:按"故障发生的位置"分层覆盖,而不是穷举现象:LLM 输出层(畸形 JSON、幻觉工具名、格式不合法)、工具层(抛异常、超时、空结果)、网络层(429/5xx 风暴、连接被拒、流中断)、状态层(死循环、上下文超限、状态残留)。每一层选最典型的 1-2 个,保证"每类故障都有用例兜底"。

> **Q:怎么证明你的异常用例是有效的,不是摆设?**
> A:变异验证。写完用例后我故意把降级逻辑改坏(比如去掉 max_turns 上限),对应用例必须变红;如果改坏了还全绿,说明用例没测到点子上。这也是我在项目里的习惯——新增保护逻辑必须配能杀掉它的测试。

> **Q:死循环这类问题你怎么测?**
> A:让 FakeLLM 连续返回相同的工具调用,断言:达到 turn 上限后流程必须终止、必须产生可读的失败原因、turn 数不超过预设上限。测的不是"LLM 不发疯",而是"发疯了系统有边界"。

### Phase 3 — 统一 Runner + 报告生成

**技术清单**:

| 技术点 | 说明 |
|--------|------|
| CLI 设计 | argparse 子命令:`--suite offline/replay/full`、`--model`、`--report` |
| 分层跑批 | 离线(CI 每次)/ 抽样真实 API(每晚)/ 全量多模型(手动) |
| 判定策略 | 规则优先 → LLM 裁判兜底 → 人工抽检 1%(三层裁判) |
| 可复现性 | 温度固定 0、固定数据集版本、报告记录运行参数 |
| 趋势对比 | 报告读历史 JSON,自动生成"较上次变化"列 |

**面试问答**:

> **Q:评测脚本和评测体系有什么区别?**
> A:脚本是一次性的,体系是可持续的。区别在三个词:可复现(一条命令、任何机器、同样结果)、可对比(有基线、有趋势)、可执行(报告直接回答"下一步改什么")。我做的重点是让评测变成每天都愿意跑的事,而不是面试前跑一次。

> **Q:真实 API 评测很贵,你怎么控制?**
> A:分层:CI 每次跑离线 mock 模式,零成本;每晚跑 20 条真实 API 抽样;全量多模型对比只在 prompt 大改或模型切换时手动触发。不同频率对应不同的置信度需求。

> **Q:结果怎么判定?LLM 裁判靠谱吗?**
> A:三层裁判:能用规则匹配的绝不用模型(比如"报告的 bug 和标注行号是否命中"),规则覆盖不了的语义判断才用 LLM 裁判,并且随机抽 1% 人工复核校准裁判质量。LLM 裁判本身也要被评测。

### Phase 4 — CI 门禁

**技术清单**:

| 技术点 | 说明 |
|--------|------|
| GitHub Actions | workflow 触发条件(`push`/`pull_request`)、step 依赖、缓存加速 |
| 门禁设计 | 阈值脚本:指标跌穿阈值 exit 1;fail-fast |
| 平台差异防护 | CI Ubuntu 为准,本地新用例先用 junction/容器验证 |
| 现有基础 | ruff + mypy + pytest --cov + codecov 已就绪,只加评测 step |

**面试问答**:

> **Q:为什么一定要把评测放进 CI?**
> A:评测不进 CI 就会腐烂——没人会记得手动跑。只有每次 PR 自动跑、指标跌穿自动红,评测才真正有约束力。我的项目里有现成教训:一个 symlink 测试因为 Windows 权限被 skip 了几个月,直到 CI 在 Linux 上首次运行才暴露它从来没验证过任何东西。

> **Q:CI 上指标波动导致误报怎么办?**
> A:三招:离线 mock 模式本身是确定性的,不会波动;真实 API 评测只在 nightly 跑、且带重复运行;对已知 flaky 用例单独标记隔离,不让它阻塞门禁,但必须记录在案定期治理。

### Phase 5 — 稳定性与成本

**技术清单**:

| 技术点 | 说明 |
|--------|------|
| 非确定性度量 | pass@k(重复 k 次全过)、run 间方差、flaky 率 |
| 温度控制 | 评测固定 temperature=0,记录参数进报告 |
| 成本核算 | usage token × 模型单价表;token/任务、成本/任务 |

**面试问答**:

> **Q:LLM 每次输出都不一样,评测怎么做才可信?**
> A:接受非确定性,度量非确定性:温度固定 0 减少波动,重复 k 次用 pass@k 和方差描述"稳定通过率",而不是单次结果。结果在 k 次之间翻转的用例标记为 flaky 单独治理——flaky 本身就是一个值得报告的指标。

> **Q:评测为什么要统计成本?**
> A:成本是生产可用性的硬约束。同一个任务,模型 A 成功率 90%/次成本 0.3 元,模型 B 95%/0.8 元,选哪个是产品决策不是技术决策。评测报告给数字,让决策有依据——这也是我在项目里做多模型对比时的方法。

### Phase 6 — 扩展评测

**技术清单**:

| 技术点 | 说明 |
|--------|------|
| fix 质量评测 | 注入已知 bug → auto-fix → verify 回归;修复率/回归引入率/无损率 |
| 检索评测 | recall@k、MRR;embedding 检索 vs FTS5 关键词 A/B |
| 诚实口径 | 当前检索层是 FTS5 关键词方案,升级路径已设计(embedding + ChromaDB) |

**面试问答**:

> **Q:你怎么评一个 Agent 的"修复"能力?**
> A:三个指标:修复率(注入的 bug 修好了多少)、回归引入率(修复过程有没有弄坏别的)、无损率(不需要修的地方有没有被误改)。判定靠 verify 节点的自动化行为验证(pytest 重跑),不靠"看起来对了"。

> **Q:你项目的 RAG 部分是什么水平?**
> A:如实说:检索层当前是 SQLite FTS5 关键词方案,不是向量语义检索,接口层为 embedding 升级预留了一致的 API。我不会把它叫 RAG——RAG 是"检索增强生成"的完整链路,我现在是关键词检索 + 生成,差在后半段。如果岗位需要,我下一步就是把 embedding 和检索质量评测补上。

### 技术复盘:面试前必须能脱口而出的三句话

1. **"先看数据基础,再谈评测方法"** —— 发现 82/84 个日志是空的,所以 Phase 0 是补采集而不是写脚本。
2. **"指标必须带公式、来源、阈值"** —— 没有这三样,指标就是感觉。
3. **"用例要能被'改坏'验证"** —— 只会在正确代码上变绿的用例,不是用例。
