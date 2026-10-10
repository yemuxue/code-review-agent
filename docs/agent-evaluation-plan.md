# 企业级 Agent 测评体系 — 实施方案与项目改造计划

> 2026-09-10 | 适用项目:code-review-agent(Multi-Agent 代码审查)
> 目标岗位:AI 智能体评测(评测集/指标/报告/异常链路/问题闭环)

---

## 0. 改造前盘点与当前状态

> 左列保留 2026-09-10 的改造前诊断，便于复盘；右列是截至 2026-09-27 的实际状态。不能再把左列红黄项理解为当前未完成事项。

| 能力 | 改造前盘点 | 当前状态（截至 2026-09-11） | 证据 |
|------|------------|-----------------------------|------|
| 结果评测（数据集+指标） | 124 条标注集（含 16 条 FP 陷阱），已有 P/R/F1，但未工程化 | ✅ runner 支持 `offline/replay/full`；2026-09-11 与 2026-09-27 均有 124/124 完整扫描产物；最新运行有 3 条 API_ERROR，被旧口径计作 FN | [runner.py](../tests/eval/runner.py)、[2026-09-27 replay 报告](../reports/eval_replay_20260927.md) |
| 节点级统计 | 已采集但只在内存 | ✅ `node_stats` 已持久化，支持节点耗时/token/turn 指标 | [metrics.py](../../src/eval/metrics.py) |
| 遥测日志 | 多 Agent 运行仅有 `session_start` | ✅ 角色事件、工具 start/end、解析结果、强制收尾与节点统计均写 JSONL | [telemetry.py](../../src/harness/telemetry.py) |
| 角色归因 | 无 planner/executor/reviewer/fixer 标签 | ✅ `AgentLogger.for_role()` 角色视图已接入编排器 | [test_telemetry_roles.py](../../tests/test_telemetry_roles.py) |
| 失败分类 | 仅有 error 事件，无 taxonomy | ✅ F-01 至 F-09 规则归因与 Top 失败类报告已实现 | [metrics.py](../../src/eval/metrics.py) |
| 评测复现 | 目标路径硬编码，换机即废 | ✅ 目标目录 CLI 参数化；新增目标仓库/revision/样本匹配预检治理规则 | [eval_llm_agent_qa.py](../../tests/eval_llm_agent_qa.py)、§6.4 |
| CI 评测门禁 | 仅 import 检查 | 🟡 CI 已接入离线故障集与健康链路阈值门禁；Precision 门禁和实际 Actions 运行证明待补 | [ci.yml](../../.github/workflows/ci.yml) |
| 故障注入 | 零散回归，无体系 | 🟡 10 条真实 Harness/编排器故障注入已覆盖核心降级与归因；尚缺变异验证 | [faults.py](../../tests/eval/faults.py) |
| 报告产物 | 无统一评测报告 | 🟡 基线、真实运行、离线/真实链路验证报告已产出；尚缺自动历史趋势比较 | [reports](../../reports) |
| 稳定性/成本 | 无重复运行、方差、成本口径 | 🟡 `--repeat k`、pass@k/flaky、显式模型单价换算已支持；尚缺方差、pass@5 与真实单价成本报告 | [pricing.py](../../src/eval/pricing.py) |

**当前结论**:Phase 0–1 已按原验收标准完成；Phase 2–5 的主链路已落地并验证，但仍有明确验收缺口，见 §11.1。Phase 6 是按需扩展，**尚未实施** auto-fix 质量评测或 embedding 检索评测。真实 124 条 replay 后续已完整扫描；2026-09-27 运行的运行口径 P/R/F1 为 95.3%/38.0%/0.54，其中 3 条 API_ERROR 被 runner 当作 FN，详见 §11.8。该结果是单次运行，不构成稳定性或跨仓库泛化结论。

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
- **目标仓库可验证**:真实 replay 前必须记录目标仓库路径与 revision，并预检 `样本文件匹配数 / 样本总数`；匹配不全时直接失败，禁止把跨项目数据集的部分结果写成模型能力结论。

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

## 10. 各阶段技术要点与项目问答

### 10.0 通用回答框架(先记这个)

- **万能五步**:定义指标 → 设计用例 → 自动化执行 → 归因报告 → 门禁回归。任何评测问题都往这个框架里装。
- **数据说话**:先给数字,再给结论。例如"我先扫了 84 个日志文件,发现 82 个只有一行 session_start——采集不全,后面所有指标都是空中楼阁,所以我先做 Phase 0"。
- **诚实边界**:没做过的说"当前方案是 X,升级路径是 Y",不把计划写成事实。读者追问细节时,真诚的"这是下一步计划"比含糊遮掩更可靠。

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

**项目问答**:

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

**项目问答**:

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

**项目问答**:

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

**项目问答**:

> **Q:评测脚本和评测体系有什么区别?**
> A:脚本是一次性的,体系是可持续的。区别在三个词:可复现(一条命令、任何机器、同样结果)、可对比(有基线、有趋势)、可执行(报告直接回答"下一步改什么")。重点是让评测变成每天都愿意跑的事,而不是发布前临时跑一次。

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

**项目问答**:

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

**项目问答**:

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

**项目问答**:

> **Q:你怎么评一个 Agent 的"修复"能力?**
> A:三个指标:修复率(注入的 bug 修好了多少)、回归引入率(修复过程有没有弄坏别的)、无损率(不需要修的地方有没有被误改)。判定靠 verify 节点的自动化行为验证(pytest 重跑),不靠"看起来对了"。

> **Q:你项目的 RAG 部分是什么水平?**
> A:如实说:检索层当前是 SQLite FTS5 关键词方案,不是向量语义检索,接口层为 embedding 升级预留了一致的 API。我不会把它叫 RAG——RAG 是"检索增强生成"的完整链路,我现在是关键词检索 + 生成,差在后半段。如果岗位需要,我下一步就是把 embedding 和检索质量评测补上。

### 技术复盘:必须明确的三句话

1. **"先看数据基础,再谈评测方法"** —— 发现 82/84 个日志是空的,所以 Phase 0 是补采集而不是写脚本。
2. **"指标必须带公式、来源、阈值"** —— 没有这三样,指标就是感觉。
3. **"用例要能被'改坏'验证"** —— 只会在正确代码上变绿的用例,不是用例。

---

## 11. 已完成工作总览(截至 2026-09-11)

> 本节是**事实清单**:只记录已经落地并验证过的内容,与上面各阶段的"计划"区分开；完成状态更新至 2026-09-27。
> 分支 `feat/agent-evaluation`(main 未被触碰),commits:`f0433ce`(P0)→`9ee0fc7`(P1)→`7b17e09`(真实运行验证)。

### 11.1 阶段完成度

| 阶段 | 状态 | 已完成 | 待改进 / 未闭环验收 |
|------|------|----------|-------------------|
| Phase 0 遥测补全 | ✅ 完成 | 角色归因、工具 start/end、节点统计、三入口接线；真实多 Agent 日志验证 | 无 |
| Phase 1 指标库 + 基线报告 | ✅ 完成 | 轨迹/效率/失败分类、旧日志降级、基线和真实运行报告 | 无 |
| 真实运行验证（计划外） | ✅ 完成 | `reports/eval_real_run_20260910.md` 证实 P0/P1 新采集字段可用 | 运行级样本仅 2 次，不用于稳定性结论 |
| Phase 2 故障注入 | 🟡 已落地 | 10 条真实 Harness/编排器离线注入，验证有界降级与 F-01..F-07 归因 | 未做“故意改坏降级逻辑后用例必红”的变异验证；未覆盖流式中断、连接拒绝等计划场景 |
| Phase 3 统一 Runner | 🟡 已落地 | `offline/replay/full`、`--repeat`、报告、真实 replay JSON 明细落盘、`--allow-live` 授权保护；2026-09-11 和 2026-09-27 均有 124/124 扫描产物 | 未实现与上次报告的自动趋势比较；最新 JSON 未固化完整模型/提示词/仓库 revision 元数据；3 条 API_ERROR 被计作 FN |
| Phase 4 CI 门禁 | 🟡 已接入 | CI 运行离线 runner，健康链路工具成功率 ≥98%、端到端成功率 ≥90% | 原计划的 Precision <90% 门禁未实现；尚未取得 GitHub Actions 实际运行记录证明 |
| Phase 5 稳定性与成本 | 🟡 已落地 | `--repeat k`、pass@k/flaky、显式输入/输出单价计算、未知价格 n/a | 未计算结果方差；未生成 pass@5；真实模型单价未提供，未产出真实成本/任务 |
| 真实 replay 链路验证（计划外） | ✅ 全量扫描已完成 | 9/11 阶段快照为 77/124；后续 9/11 full 报告及 9/27 replay JSON 均记录 124/124；最新运行含 3 条 API_ERROR | 最新运行 P/R/F1 受 API_ERROR 计分口径影响；需重复运行、补元数据和趋势比较 |
| Phase 6 扩展评测 | ⬜ 未开始 | 无 | auto-fix 质量评测或 embedding 检索评测尚未选型、实施、出报告 |

### 11.2 代码改动清单

**新建**

| 文件 | 作用 |
|------|------|
| [src/eval/log_parser.py](../../src/eval/log_parser.py) | JSONL → `RunLog`;显式标注采集能力(capabilities),缺失即降级;坏行跳过计数不抛异常;`load_logs(since=)` 分离新老日志 |
| [src/eval/metrics.py](../../src/eval/metrics.py) | 11 个轨迹指标 + 效率/成本/并行 + F-01..F-09 分类 + pass@k/flaky;数据源缺失一律返回 `None`+原因 |
| [src/eval/report.py](../../src/eval/report.py) | 基线报告渲染(覆盖度/轨迹/效率/失败分类/稳定性/结论);`--logs --since --out` |
| [src/eval/__init__.py](../../src/eval/__init__.py) | re-export |
| [tests/test_telemetry_roles.py](../../tests/test_telemetry_roles.py)(8) | role 归因、`for_role` 视图共享单文件、`node_end`/`session_end` 语义、旧 payload 逐字节不变 |
| [tests/test_harness_telemetry.py](../../tests/test_harness_telemetry.py)(10) | 工具事件配对、四类 `failure_kind`、参数校验只观测不拦截、`logger=None` 零写入 |
| [tests/test_eval_metrics.py](../../tests/test_eval_metrics.py)(23) | 旧日志降级、指标真值、失败分类去重、离线全链路端到端 |
| [reports/eval_baseline_20260910.md](../../reports/eval_baseline_20260910.md) | 全量基线(86 个日志) |
| [reports/eval_real_run_20260910.md](../../reports/eval_real_run_20260910.md) | 真实 API 运行验证(2 次) |
| [reports/eval_chain_validation_20260911.md](../../reports/eval_chain_validation_20260911.md) | 离线门禁 + 77/124 条真实 replay 的链路验证；明确标注部分样本边界 |
| [reports/eval_replay_20260927.md](../reports/eval_replay_20260927.md) | 2026-09-27 完整扫描 124/124 的 replay 复盘；区分运行口径与排除 API_ERROR 的模型响应口径 |
| [reports/eval_replay_20260927_replay.json](../reports/eval_replay_20260927_replay.json) | 124 条逐样本真实 replay 明细 |
| [tests/eval/cases.py](../../tests/eval/cases.py) · [faults.py](../../tests/eval/faults.py) · [runner.py](../../tests/eval/runner.py) | 10 条离线故障注入、统一执行器、真实 replay 明细落盘与 `--allow-live` 显式授权 |
| [src/eval/pricing.py](../../src/eval/pricing.py) | 显式单价表与成本换算；未知模型不猜价格 |

**修改**

| 文件 | 改动 |
|------|------|
| [src/harness/telemetry.py](../../src/harness/telemetry.py) | `role` 参数 + `for_role(role)` 共享文件/锁/会话;`node_stats`/`parse_result`/`forced_finish` 事件;`args_fingerprint`(规范化 JSON sha1 前 16 位);`failure_kind`;修复 `TimedToolCall` 双记 `tool_call_start` |
| [src/harness/agent.py](../../src/harness/agent.py) | 补齐**从未发出过**的 `tool_call_start`(历史 84 日志 0 条);统一 `_log_tool_end` 出口;HITL 拦截不再静默;`_force_finish` 落事件 |
| [tests/eval_llm_agent_qa.py](../../tests/eval_llm_agent_qa.py) | 真实 replay 的单样本 API 异常降级为 `API_ERROR` 并继续后续样本；Windows 本地代码页不支持的模型字符替换输出，防止进度日志导致整批中断 |
| [src/multi_agent/langgraph_orchestrator.py](../../src/multi_agent/langgraph_orchestrator.py) | `logger=` 参数;`_make_agent` 按角色派发视图;`parse_result` 埋点(planner/executor/fixer);`node_stats` 含 `max_turns`;`session_end` 带 `fix_status` |
| [src/multi_agent/factory.py](../../src/multi_agent/factory.py) · [streamlit_app.py](../../src/app/streamlit_app.py) · [server.py](../../src/api/server.py) · [cli_multi.py](../../src/app/cli_multi.py) | 日志器逐层透传(各 1–2 行) |

零回归策略:全部新参数 keyword-only + 默认 `None`;`logger=None` 时不产生任何日志、行为与改造前逐字节一致(有精确相等测试守护)。

### 11.3 真实读数(两批,均来自真实 JSONL)

| 指标 | 全量基线(86 个日志) | 真实运行(since 20260910,2 次) | 目标 |
|------|---------------------|------------------------------|------|
| 工具调用成功率 | 99.2%(648/653) | 100.0%(153/153) | ≥98% |
| 参数合法率 | 100.0%(153/153,仅新日志有分母) | 100.0%(153/153) | 建基线 |
| Plan 可执行率 | 100.0%(2/2) | 100.0%(2/2) | ≥95% |
| VERDICT 解析率 | 100.0%(34/34) | 100.0%(34/34) | ≥95% |
| 空转率 | 0.0%(0/250) | 0.0%(0/115) | ≤10% |
| 循环率 | 0.0%(0/2) | 0.0%(0/2) | 0% |
| turn 预算耗尽率 | 42.1%(8/19) | 42.1%(8/19) | 建基线 |
| 端到端成功率 | 10.5%(9/86,缺口=77 个未收尾运行) | 100.0%(2/2) | ≥90% |

- **失败分类**:全量 F-04 工具执行异常×5(`read_file(offset=...)` 参数不兼容,同一 bug 反复出现)、F-05 模型 API 故障×4(老日志在 500 字处截断,按栈帧归因)。真实运行 0 条。
- **成本（当时）**:241,642 tokens(2 次有数据运行,≈12.1 万/次);当时尚未配置单价。现已由 Phase 5 runner 通过显式模型与输入/输出单价参数核算，未知价格仍保持 n/a。
- **并行效率**:节点耗时合计 352.3 s ÷ 挂钟 160.2 s = **2.2×**,Send 并行的实测收益。
- **样本量边界**:工具级 n=153 可看,运行级 n=2 只够验证链路,不足以下结论——要下结论得靠 Phase 2(证明指标抓得住故障)+ Phase 5(`--repeat k`)。

### 11.4 真实运行暴露、并已补上的盲区

真实跑才发现的空白(纸面设计看不出来):

1. **`_force_finish` 兜底路径零覆盖** —— turn 用满后强制收尾(末轮工具调用被丢弃、只让模型"给最好的答案")是明确的**质量降级点**,此前无任何指标。现补 `node_stats.max_turns` + `forced_finish` 事件 + `turn 预算耗尽率`:实测 8/19 节点用满预算,其中 5 次真被强制收尾。
2. **`tool_call_start` 历史上从未被发出** —— 轨迹指标全链路缺口,工具级指标此前只有"结果"没有"发起"。已补齐。
3. **多 Agent 运行完全无日志** —— 三入口都没传 logger,多 Agent(项目主体)在评测里是黑洞。已接线。
4. **HITL 拦截静默** —— 被策略/人工拦下的动作无处统计。现落 `failure_kind=hitl_blocked`。
5. **`TimedToolCall` 双记** —— 评测数据集里已知的一条缺陷,一并在 P0 修掉。

### 11.5 方法学要点(可复用的判断)

1. **测不到 ≠ 零故障**:数据源缺失的指标返回 `None` + 原因,绝不用 0 冒充。空转率第一次算出 12.6% 就是误判——真因是"一个日志文件里含多次运行、turn 号从 1 重来",改成线性扫描事件流后真值是 0.0%。
2. **采集缺口不进失败分类**:77 个运行没有 `session_end`(P0 前多 Agent 没接 logger),若按 F-08"状态污染"计,等于把**日志问题伪装成模型失败**。它属于覆盖度问题,单列。
3. **去重按 (run, turn)**:`tool_call_end(error=True)` 与 `error` 事件描述同一次故障,不去重会把 F-04 从 9 条虚增到 14 条。
4. **分母即语义**:token 平均值只除以"真有 token 数据"的运行数,否则 84 个无数据日志会把均值摊薄成一个看似精确的假数。
5. **指标空白要靠真跑发现**:turn 预算耗尽率、并行效率都不是设计阶段想出来的,是真跑之后回头看日志才发现的。
6. **全量样本不等于有效样本**:先前误把 124 条 `llm-agent-qa-system` 标注集指向当前仓库，实际仅匹配 16/124 个文件；正确目标 `X:\VScode\llm-agent-qa-system` 才匹配 124/124。目标仓库与 revision 是评测输入的一部分，必须落盘并预检。

### 11.6 真实 API replay 链路验证(2026-09-11)

> 本节保留当日 77/124 阶段性链路快照。后续生成的 [eval_full_20260911.md](../reports/eval_full_20260911.md) 已记录 124/124；2026-09-27 再次完成 124/124，见 §11.8。阶段快照不再代表最终运行状态。

- **执行范围**:数据集共 124 条（108 条正例、16 条 FP 陷阱）；正确目标为 `X:\VScode\llm-agent-qa-system`，文件匹配率 124/124。运行使用已配置的 `deepseek-v4-flash` / DeepSeek API。
- **阶段快照**:离线故障注入 10/10 通过；健康门禁的工具调用成功率与端到端成功率均为 100.0%；当时真实 replay 运行到 77/124 后暂停，API 错误数为 0。后续 full 产物完成了全量扫描。
- **部分样本读数（不可外推为全量）**:TP=21、FP=1、FN=55、TN=0；Precision=95.5%、Recall=27.6%、F1=0.43。低 Recall 中存在标注行落在 docstring、注释或空行附近的情况，需要先复核标注定位，再区分模型漏报与数据集漂移。
- **真实运行修复**:首次 API 异常路径因 `answer` 未初始化而中断；其次，Windows GBK 输出遇到模型文本中的非断行连字符会中断进度打印。两项均已修复并有回归测试，真实 runner 现会持久化 replay JSON 明细，避免终端输出丢失后无法复核。
- **产物**:[eval_chain_validation_20260911.md](../reports/eval_chain_validation_20260911.md)（阶段快照）；[eval_full_20260911.md](../reports/eval_full_20260911.md)（后续全量报告）；逐条运行日志为 `reports/eval_full_live.out.log`（本地运行产物，不纳入基线）。

### 11.8 真实 API replay 全量运行（2026-09-27）

- **完成状态**: `reports/eval_replay_20260927_replay.json` 记录 `total=124`、`scanned=124`，全部样本均有结果明细。本次属于完整样本扫描，不再沿用“仅跑到 77 条、没有全量 P/R/F1”的旧结论。
- **运行口径**: TP=41、FP=2、FN=67、TN=14；Precision=95.3%、Recall=38.0%、F1=0.54。
- **API 异常口径**:3 条明细为 `API_ERROR`。现有 `run_per_sample_eval()` 在异常时设 `found=False`，正例 API_ERROR 因此被算入 FN。若仅为分析模型响应而排除这 3 条，TP=41、FP=2、FN=64、TN=14，Precision=95.3%、Recall=39.0%、F1=0.55；此排除口径不得替代包含服务可用性的运行口径。
- **旧日志未收尾说明**:2026-09-10 历史基线的 77/86 个未收尾运行仍是该批旧数据的事实，并属于当时的遥测覆盖缺口。2026-09-27 离线报告显示新故障注入批次 10/10 有 `session_end`；这是新日志采集状态，不会改写旧基线。
- **证据边界**:本次 replay JSON 没有完整固化模型版本、提示词版本、目标仓库 revision 等复现元数据；当前仅支持描述本次运行结果。3 条 API_ERROR 和标注位置质量也需单独治理。
- **产物**:[eval_replay_20260927.md](../reports/eval_replay_20260927.md)、[逐样本 JSON](../reports/eval_replay_20260927_replay.json)。

### 11.7 待改进优先级

1. **P4 门禁闭环**:补 Precision ≥90% 的确定性离线门禁，并在 GitHub Actions 实际跑一次“劣化变红、恢复转绿”。这是最接近生产约束、投入最小的缺口。
2. **P2 用例有效性**:对每类保护逻辑做一次变异验证；补流式中断、连接拒绝等尚未覆盖的计划场景，证明测试不只是“正确代码上变绿”。
3. **P3 真实结果可比性**:全量 replay 已完成；剩余工作是实现与上次 JSON/Markdown 的趋势比较，并在每次执行时固化目标仓库 revision、模型/提示词版本、评测集版本和样本匹配率。
4. **P5 统计与成本闭环**:对稳定的离线集跑 `--repeat 5` 并计算结果方差；提供真实模型的输入/输出单价后，生成成本/任务报告。当前未知价格一律 n/a，避免伪精确。
5. **P6 扩展评测**:在 auto-fix 质量（修复率、回归引入率、无损率）与 embedding 检索质量（recall@k、MRR）中选择一个业务优先方向后再实施。
6. **展示口径**:先讲“我先量化了旧日志的采集缺口（77/86 缺少 session_end）”，再讲“补采集后新离线批次 10/10 均收尾”，最后说明“真实 124 条 replay 已全量扫描，但单次运行含 3 条 API_ERROR，稳定性、趋势比较和统计闭环仍待完善”。

-----
回答问题

我做的项目是一个多 Agent 代码审查系统，评测工作我按“数据采集、指标计算、故障注入、自动化门禁、问题闭环”来推进。

**1. 功能测试、边界 case 和异常链路怎么做？**

我先把 Agent 的关键链路拆成模型输出、工具调用、编排状态和最终收敛四层，再为每层设计场景。当前已落地 10 条离线故障注入，例如畸形 tool call、未知工具、缺失参数、工具异常和超时、429 限流、上下文超限、重复调用死循环、空回答、畸形 FINDING。

断言不依赖具体报错文案，主要看三件事：系统不能崩、错误必须可读、执行必须有上限。例如死循环场景会让 FakeLLM 重复发同一个工具调用，验证达到 `max_turns` 后触发 `forced_finish`，同时日志能归类为 F-06。

这部分核心链路已经跑通，但我不会说“异常测试全部完成”。下一步是补流式中断、连接拒绝等场景，并做变异验证，也就是故意移除降级逻辑，确保对应测试必然失败。

**2. 如何设计评测集、指标和评测报告？**

我把评测分成结果、轨迹、效率成本和稳定性四层。

结果层使用标注集计算 Precision、Recall、F1；轨迹层关注工具调用成功率、参数合法率、Plan 可执行率、VERDICT 解析率、空转率、循环率和端到端成功率。为了让指标可归因，我先补了 telemetry：每条事件带角色标签，工具调用有 start/end，节点统计和解析结果会落到 JSONL。

执行上分三层：CI 跑零成本 offline，抽样真实 API 跑 replay，需要时跑 full。报告会输出指标、失败分类和样本量。2026-09-27 的 full replay 完成 124/124 条扫描，运行口径 Precision=95.3%、Recall=38.0%、F1=0.54；其中 3 条 API 超时被当前脚本计作 FN。排除 API_ERROR 的模型响应口径 Recall=39.0%、F1=0.55。由于缺少重复运行及完整版本元数据，这是一份单次评测读数，不是稳定基线。

**3. 如何和后端、提示词开发一起定位 Agent 异常和决策错误？**

我的闭环是：先复现，再从指标异常定位角色和 turn，再看结构化日志与输入输出，最后确定是提示词、工具、编排还是数据集问题，并沉淀回归用例。

例如我发现多 Agent 运行日志过去只有 `session_start`，无法知道 planner、executor 还是 reviewer 出了问题，于是将 logger 从入口透传到编排器，并通过 `for_role()` 给事件绑定角色。另一个例子是运行真实评测时，发现单条 API 异常会导致整批评测中断，以及 Windows GBK 输出遇到特殊字符会中断进度打印；我把它们改成单样本记录 `API_ERROR` 后继续运行，并增加回归测试。

和提示词开发协作时，我不会只说“模型效果不好”，而是给出可复现任务、对应角色、原始失败证据、指标变化和最小修复建议。修复后必须回到同一用例和同一指标验证。

**4. 如何维护评测数据集和测试用例库？**

每条评测用例都有全局 ID、标签、注入配置、预期行为、来源和严重级别；ID 只增不改。生产问题的链路要求是：事故记录 → 修复 commit → 回归测试 → 评测用例。

我还补了一条治理规则：真实 replay 前必须记录目标仓库和 revision，并检查“样本文件匹配数 / 样本总数”。这是因为我实际遇到过 124 条标注集误指向当前仓库、只有 16 条文件匹配的情况；正确目标仓库才是 124/124。这个预检能避免把跨项目数据集的结果误当成模型能力。

目前 P0、P1 已完整验收；P2 到 P5 主链路已落地。真实 124 条 replay 已完整扫描，但仍保留变异验证、趋势对比、Precision 门禁、pass@5、方差和真实成本报告等改进项。我会区分“全量扫描完成”和“评测体系完全闭环”，不把前者说成后者。
