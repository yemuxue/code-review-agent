# 当前项目整改清单

> 审计日期：2026-08-14  
> 范围：代码审查、已执行的真实 LangGraph 修复流程、当前工作区状态。  
> 说明：本清单只包含已验证的问题；未把尚未验证的生产化设想列为缺陷。
> 最近更新：2026-08-18（代码级整改）→ 2026-10-07（评测闭环 / 企业级落地 / 产品化）

## 整改原则

- 新增或修改的复杂逻辑必须使用简洁中文注释，说明设计原因、边界或风险；不为显然代码逐行注释。
- 新增和更新文档使用 UTF-8 编码，中文为主。API 标识符、命令、许可证、第三方错误保持原文并用中文解释。
- 修复必须有对应测试。涉及写入、权限、用户隔离或命令执行的变更，必须覆盖失败路径。
- 未经明确批准，不提交或覆盖用户已有的未提交文件。

---

## 2026-10-07 整改：评测闭环 / 企业级落地 / 产品化

本轮补的不是代码 bug，而是**闭环与治理**：指标算出来了但拦不住人、
交付面把密钥打进镜像、产品指标树没有数据源。每项都有对应测试或机器可验证断言。

### 一、评测闭环

| 缺口 | 现象 | 处理 | 证据 |
|---|---|---|---|
| 阈值与门禁脱节 | 目标值只在报告渲染层（`TARGETS`），`--gate` 只硬编码校验 2 个指标，其余"算出来但永远不拦人" | 门禁改为直接消费 `TARGETS`；必需指标无样本判 **fail-closed**；稳定性与采集健康度一并纳入 | `src/eval/baseline.py::evaluate_targets`、`tests/eval/test_eval_gate.py` |
| 无回归比对 | 调整提示词后"变好还是变差"只能人眼看 markdown | 新增机器可读基线快照 + 逐指标回归判定（含失败计数与采集覆盖度） | `src/eval/baseline.py::compare_to_baseline`、`reports/eval_offline_baseline.json` |
| 报告自相矛盾 | 第 2 节"工具成功率 37.5% ❌"与第 8 节"门禁 100% 通过"并存（主表含注入故障、门禁用健康链路） | 主表与门禁**同源**（健康链路），故障注入数据单独成节并标注"非门禁口径" | `tests/eval/report.py` |
| 健康链路样本量过小 | 只跑 1 次工具调用 → "≥98%"退化为"那一次必须成功" | 健康链路扩展为 3 个不同工具调用，分母 ≥3 | `tests/eval/faults.py::run_healthy_case` |
| 采集退化无人管 | `unknown_events` 有采集无消费；坏行率/配对率/未收尾率完全不进门禁 | 新增"采集健康度"一层指标，带目标值并参与门禁与回归判定 | `src/eval/metrics.py::tool_pairing_rate` 等 |
| 失败静默丢弃 | `hitl_blocked` 被发出但归因链没有分支，失败凭空消失；且旧测试把这次丢失固化为断言 | 引入 `FAILURE_KINDS_EMITTED` ↔ `FAILURE_KIND_TO_CODE` 防丢弃契约；未映射的 kind 归 F-04 并标注"需补规则" | `tests/test_eval_metrics.py` |
| 分类误导 | F-08/F-09 无任何规则产生，却与其它分类并列展示 | 报告显式标注"尚无归因规则，不代表本次为 0" | `src/eval/report.py` |
| CI 未用基线 | `--baseline/--write-baseline` 未进 CI，评测产物无留存 | eval-gate 作业加基线比对 + `upload-artifact` | `.github/workflows/ci.yml` |
| **`/analyze` 零测试**（最长、最贵路径） | 端点在函数体内直接构造 `AnthropicClient()`，发真实网络请求 → 无法被任何测试触发 | 抽出 `_build_llm_client()` 依赖注入缝；新增 12 项离线端点测试（单/多 Agent、落库、索引、越界、鉴权） | `src/api/server.py`、`tests/test_analyze_endpoint.py` |
| **降级被当成成功** | 模型故障、turn 预算耗尽、空回答都返回 `HTTP 200 + 文本`，调用方无法区分"分析成功"与"模型 429" | `AgentHarness`/编排器显式标记 `degraded` + `degraded_reason`；响应体加 `degraded`/`error_code`；新增"降级率"指标并进 CI 门禁 | `src/harness/agent.py`、`src/multi_agent/langgraph_orchestrator.py`、`src/eval/metrics.py::degradation_rate` |
| 编排异常变孤儿会话 | 未预期异常 → 裸 500，但会话已建 → "有 session 无消息" | 捕获后落库两条消息 + 返回带 `error_code` 的可解释终态 | `src/api/server.py` |
| `mode` 拼错静默降级 | `mode` 是自由字符串，写成 `multii` 会静默走 single 分支 | 显式校验，非法值返回 422 | `src/api/server.py` |
| **标注集无版本、无预检** | 两份数据集是裸 7 元组列表，没有 id/版本/校验；文件找不到就 `continue`（历史事故：16/124 静默跳过） | 新增 `tests/eval/datasets.py`：数据集注册表 + `preflight()`（文件/行号/枚举/计数一致性/basename 歧义）+ CI 作业 | `tests/eval/datasets.py`、`tests/test_eval_datasets.py`、`.github/workflows/ci.yml` |
| **basename 兜底指错文件** | 外部项目 `agent/tools/base.py`、`llm/base.py`、`memory/base.py` 三个同名文件，旧实现按文件名兜底会静默指到错误位置 | 改为"唯一命中才接受"，歧义直接报错；修正 5 条被歧义影响的标注为全路径 | `tests/eval/datasets.py::resolve_sample_file` |
| **数据集元信息与实际不符** | 文件头写"109 真 + 15 FP"，实测 108 + 16 | 以实测为准并加一致性断言（改标注必须同步计数） | `tests/eval_llm_agent_qa.py::DATASET_META` |
| **结果层与轨迹层互不相干** | P/R/F1 只在真实 API 路径产出，且计分逻辑与网络调用耦合、无单元测试 | 抽出 `tests/eval/scoring.py`（纯函数 + 可注入判分）；跳过/异常显式记账（`healthy` 判据）；replay 产物写模型/版本/口径元数据 | `tests/eval/scoring.py`、`tests/test_eval_scoring.py` |
| **审计答不了"谁对哪个文件做了什么"** | 遥测无 actor；`AgentLogger` 不传用户；无请求 ID；登录成败零审计；`logs` 表无 file_path/actor | `request_context`（contextvar）+ 请求 ID 中间件 + 遥测带 actor/request_id/project_path + `logs` 表 Mig 005 加审计列 + `get_audit_trail()` + 登录/分析/越权/降级全部审计 | `src/harness/request_context.py`、`src/harness/telemetry.py`、`src/storage/database.py`、`tests/test_audit_trail.py` |
| **错误响应无稳定类别** | 全是自由文本 detail；未预期异常返回 HTML 纯文本 500，运维拿不到请求号 | 统一 `error_code` + 请求 ID；中间件层兜底未预期异常返回结构化 500 | `src/api/server.py` |
| **内部异常文本外泄** | `/analyze` 把 `RuntimeError: {exc}` 原样回显，而上游错误体可能含网关地址/密钥片段 | 调用方只拿到错误类别 + 请求号；完整细节进日志与审计 | `src/api/server.py` |
| **日志非结构化** | 全仓无 `basicConfig`，走 lastResort 明文 stderr，采集系统无法消费 | 单行 JSON 日志到 stdout，带请求号与操作者 | `src/api/server.py::_JsonLogFormatter` |

**仍未闭环**（诚实记录）：124/33 条标注集只在手动 `--allow-live` 路径被消费，
CI 不跑；真实评测路径不落遥测，因此结果层 P/R/F1 与轨迹指标**无法按样本 join**；
数据集无版本号、无 holdout、无标注一致性校验；replay 是重新打真实 API 而非录播。

### 一之二、2026-10-08 补：标注集预检与离线判分（第 2、3、4 项）

| 缺口 | 现象 | 处理 | 证据 |
|---|---|---|---|
| 标注集不可机器校验 | 无 id/版本/预检；文件找不到静默 `continue` | 注册表 + `preflight()`：文件存在、行号范围、枚举合法、计数一致、basename 歧义；CI 新增 `dataset-preflight` 作业 | `tests/eval/datasets.py`、`tests/test_eval_datasets.py`（20 项） |
| **同名文件指错位置** | `tools/base.py` 与 `llm/base.py`、`memory/base.py` 同名；旧实现取最后一个匹配 | 唯一命中才接受，歧义报错；`agent/tools/base.py` 全路径化修正 5 条标注 | 同上 |
| 数据集计数与实际不符 | 文件头声明 109 真 + 15 FP，实测 108 + 16 | 以实测为准 + 一致性断言 | `tests/eval_llm_agent_qa.py` |
| 计分逻辑无单测 | P/R/F1 与真实网络调用耦合 | 抽 `scoring.py` 纯函数 + 可注入判分；跳过/异常显式记账；`healthy` 作为可决策前提 | `tests/eval/scoring.py`、`tests/test_eval_scoring.py`（16 项） |
| replay 产物无元数据 | 同一标注集三份矛盾读数无法调和 | 产物写入 dataset version / model / temperature / target_dir / skipped / errored / healthy / run_at | `tests/eval_llm_agent_qa.py` |
| replay 门禁不校验完整性 | 跳过样本时 P/R 分母不完整却照样通过 | `--gate` 下 `healthy=False` 直接失败 | `tests/eval/runner.py` |

**仍未闭环**：124 条外部集在 CI 里不跑（需额外 checkout 与真实 API）；
离线判分用的是**注入桩**而非真实模型，因此它验证的是"计分与数据管道正确"，
**不是模型质量**。真实质量读数仍需 `--allow-live`。

### 一之三、2026-10-08 补：审计闭环（第 4 项）

| 缺口 | 处理 |
|---|---|
| 遥测无主体 | `request_context`（contextvar，异步安全）承载 request_id/actor/project_path；`AgentLogger` 自动带上，角色视图继承 |
| 无请求 ID | HTTP 中间件生成/沿用 `X-Request-ID`，回写响应头；日志、遥测、审计行三处共用 |
| 数据库审计缺列 | `logs` 表 Mig 005 增 `actor`/`request_id`/`file_path`/`outcome` + 索引；新增 `get_audit_trail()` 按 actor/request/file 取证 |
| 登录零审计 | 登录成功/失败、登出（含 token 已失效）全部落审计 |
| 越权无记录 | `project_path` 越界访问记 `analyze_rejected` + `FORBIDDEN` |
| 降级在审计里不可见 | 降级运行记 `analyze_complete` + `DEGRADED`，与 `SUCCESS` 区分 |
| 错误响应无类别 | 统一 `error_code`（`UNAUTHORIZED`/`NOT_FOUND`/`INVALID_REQUEST`/…）+ 请求号 |
| 未预期异常返回纯文本 500 | 中间件层兜底，返回结构化 500（starlette 的 ServerErrorMiddleware 会先于 FastAPI 异常中间件处理，故在自建中间件里捕获） |
| 内部异常文本外泄 | `/analyze` 失败只回"错误类别 + 请求号"，细节只进日志与审计 |
| 日志不可采集 | 单行 JSON 到 stdout，带 ts/level/request_id/actor |

**仍未处理**：吊销列表仍是进程内内存（重启/多 worker 失效）；
`/analyze` 仍同步阻塞（无 202/job/取消/全链路 deadline）；
超管无法代管或排障（无用户管理接口、`/stats` 只有全局聚合）。

### 一之四、2026-10-08 补：`/analyze` 异步任务化（第 5 项）

| 缺口 | 处理 |
|---|---|
| 同步阻塞分钟级 | 新增 `POST /analyze/async`（返回 202 + `job_id`）、`GET /jobs/{id}`、`DELETE /jobs/{id}`；同步端点保留兼容 |
| 无并发上限 | `JobRegistry` 用 `asyncio.Semaphore` 做闸门（`ANALYZE_MAX_CONCURRENT`，默认 4），超出的排队而不是一起冲进线程池 |
| 无单任务超时 | `ANALYZE_TIMEOUT_SECONDS`（默认 900s），超时置 `TIMEOUT` 并记录原因，不再让调用方无限等待 |
| 无幂等键 | 支持 `Idempotency-Key` 请求头，按 (actor, key) 作用域去重，重复提交返回同一任务、不重复消耗 token |
| 无进度/取消 | `progress` 字段（queued/running/orchestrating/analyzing/indexing/done）+ 取消落到终态 |
| 两条路径可能漂移 | 同步与异步共用 `_run_analysis()` 单一实现（同类问题：三入口编排统一） |
| 任务越权可见 | `GET /jobs/{id}` 非所有者返回 404（不泄露存在性） |

**仍未处理**：任务注册表是**进程内**存储（与吊销列表/用户库一致），
多 worker 下状态不共享——代码在检测到 `UVICORN_WORKERS>1` 时会显式告警，
生产多 worker 需外置到 Redis/DB。优雅关闭（in-flight 排空）仍未实现。

### 一之五、2026-10-08 补：真实 replay 尝试与离线管道验证

| 事项 | 结果 |
|---|---|
| 真实 replay | **被凭据阻塞**：`.env` 中的 `ANTHROPIC_AUTH_TOKEN` 返回 `API HTTP 401 INVALID_API_KEY`。这是凭据问题，不是代码问题 |
| 门禁表现 | ✅ 新增的 `healthy` 判据**拦住了这次运行**：124/124 全部 API 异常时旧实现会把它们静默计成 FN（表现为"模型漏报 108 条"），现在直接判失败并中止 |
| 错误可归因 | 修复了一个真实缺陷：判分异常只记异常类型，导致 401/429/超时全退化成同一个不可诊断的 `ERROR`。现在完整原因（含上游错误体）进产物 |
| 离线管道验证 | 新增 `--replay-judge stub-yes\|stub-no` 与 `--dataset external\|self`：**124 条与 33 条都能在不触网的情况下跑完整管道**（文件解析 → 上下文 → 计分 → 元数据）。产物带 `quality_claim` 明确标注"不代表模型质量" |

离线管道验证结果（**非模型质量**）：

| 数据集 | 样本 | 匹配 | stub-yes 结果 | stub-no 结果 |
|---|---|---|---|---|
| llm-agent-qa-system-labeled v1.0.0 | 124 | 124/124 | TP=108 FP=16 → P=87.1% R=100% | FN=108 TN=16 → R=0% |
| code-review-agent-self v1.0.0 | 33 | 33/33 | TP=30 FP=3 → P=90.9% R=100% | — |

两个方向的结果与数据集构成（108 真 + 16 FP / 30 真 + 3 FP）完全吻合，
说明文件解析、上下文构造、混淆矩阵与元数据链路都正确。

### 一之六、2026-10-08 补：接入真实网关暴露的三个真实缺陷

用户提供了新凭据（`https://ezcode.lalalands.uk`）后接入实测，暴露出代码缺陷——
**这些在离线测试里永远不会出现**：

| 缺陷 | 现象 | 处理 |
|---|---|---|
| **Anthropic 协议路径缺 User-Agent** | `responses` 路径显式设了 `User-Agent: curl/8.0`（注释写明"网关边缘过滤拒绝 urllib 默认 UA"），但 `chat()`/`stream()` 的 `/v1/messages` 路径**没设** → 被 Cloudflare 拦截返回 **HTTP 403 / error code 1010**（看起来像鉴权失败） | 抽出 `_HTTP_USER_AGENT` 常量并在三条请求路径统一设置；新增断言"请求 UA 不能是 python-urllib" |
| **重试没有总预算** | 尝试次数 × 请求超时(120s) 的乘积让**单个样本最坏耗时 8 分钟**；10 条样本跑到数小时仍未结束（实测 CPU 仅 0.08s，完全卡在等待） | 新增 `LLM_CALL_BUDGET_SECONDS`（单次调用总预算，默认 90s）、`LLM_HTTP_TIMEOUT_SECONDS`（单次请求超时，默认 60s）、`LLM_MAX_ATTEMPTS`（默认 4）；预算耗尽即停止重试并抛出 |
| **不遵守 Retry-After** | 网关过载时继续按固定退避加压，放大故障 | `_retry_delay()` 优先使用上游 `Retry-After`，无该头才指数退避 |

同时给评测批处理补了两层保护：

- `--sample-delay`（默认 1s）：样本间限速，避免连续高频请求触发成片 502；
- `--sample-retries` + `--circuit-breaker`（默认 8）：批末重试判分异常样本（成功则**改写**混淆矩阵），
  连续 N 个异常即**熔断整批**——没有熔断时"网关不可用"这个早已确定的结论要跑数小时才能得出。

**网关侧现状（非代码问题）**：`gpt-6-luna` 与 `gpt-5.6-luna` 稳定返回 503，
`gpt-6-sol` 间歇可用，`gpt-5.5` 是唯一相对稳定的模型；
但所有模型下网关约 **80% 请求返回 502**（成功请求耗时 6–24 秒，另有 400）。
因此**真实 124 条质量读数本轮无法获得**，需网关恢复后重跑：

```bash
python -m tests.eval.runner --suite replay --allow-live --dataset external \
  --target-dir <llm-agent-qa-system 路径> --samples 124 \
  --sample-delay 8 --sample-retries 2 --circuit-breaker 20 \
  --report reports/eval_replay_real.md --gate
```

### 一之七、2026-10-08 补：思考深度（reasoning effort）与缓存键缺陷

切换到 `gpt-6-sol` + 高思考度时又暴露一个**静默错配**缺陷：

| 缺陷 | 现象 | 处理 |
|---|---|---|
| **缓存键不含请求参数** | 把 `reasoning_effort` 从 `low` 改成 `high` 后，第二次调用**0.0s 返回与 low 完全相同的内容** —— 命中旧深度的缓存，回答与参数不一致却无任何提示 | `LLMCache._make_key` 增加 `variant` 参数；客户端用 `_cache_variant()` 把**协议 + 思考深度**纳入缓存键；新增 5 项测试锁定 |
| 两条协议思考写法不同 | 硬套一个参数名会让其中一条静默失效 | `responses` → `reasoning.effort`；`anthropic` → `thinking.budget_tokens`（按 effort 映射 1024/2048/4096/8192），且 `max_tokens` 自动留出余量避免正文被挤空 |

**实测证据（同一题目、同一路径对照）**：

| 协议 | 观测 | 可否验证 |
|---|---|---|
| `responses` + `effort=low → high` | `reasoning_tokens 88 → 106`，输出 `366 → 328` tokens | ✅ **参数确实生效** |
| `anthropic` + `thinking.enabled` | 响应中**没有** reasoning 用量字段 | ❌ 无法验证是否生效 |

结论：**要可验证的高思考度必须用 `responses` 协议**。这一点写进了 `.env.example`
与代码注释，避免后续再次误配（此前 `anthropic` 路径还会因缺 User-Agent 被 403 拦截）。

### 一之八、2026-10-08 真实 replay 结果（gpt-6-sol + high reasoning）

网关恢复后用 `gpt-6-sol` + `LLM_REASONING_EFFORT=high` 跑通真实评测。
先取 40 条（占 124 条的 32%），**全部判分成功、零跳过零异常**：

| 项 | 值 |
|---|---|
| 数据集 | `llm-agent-qa-system-labeled` v1.0.0（前 40 条） |
| 模型 / 温度 | `gpt-6-sol` / 0.0 |
| 判分方式 | `per_sample_llm_judgement`（真实模型） |
| 健康判分 | ✅ True（skipped 0、errored 0） |
| Precision | **94.1%** |
| Recall | **41.0%** |
| F1 | **0.57** |
| 混淆矩阵 | TP=16、FP=1、FN=23、TN=0 |

**必须随数字一起说明的口径**：

- 这是**前 40 条的读数，不是全量 124 条**，不能与历史全量读数直接比较；
- 本批 FP 陷阱集中在后段（15/16 条在 40 条之后），所以 FP=1 不代表全集表现；
- 历史全量 124 条（`reports/eval_replay_20260927_replay.json`，非本模型）为
  P=95.3% / R=38.0% / F1=0.54；本批 P/R 都略高，但**两者样本不同、模型不同、版本口径不同**，
  只能作为同量级的观察，不能声称改进。
- TN=0 是因为前 40 条不含 FP 陷阱被正确排除的情况。

**本轮同时修复的产物缺陷**：`--report` 在 replay-only 路径上被**静默忽略**
（`report_path` 只在 offline 分支赋值），产物落到默认文件名且报告文件根本没写。
已把报告路径计算提到分支之前，并让标题/口径随 `quality_claim` 变化——
桩判分器的产物不会被标成"真实评测报告"。另新增
`scripts/replay_report.py`，可从产物 JSON 重建报告（改格式不必重跑 API）。

**全量 124 条重跑命令**（约 25 分钟，需网关稳定）：

```bash
python -m tests.eval.runner --suite replay --allow-live --dataset external \
  --target-dir X:/VScode/llm-agent-qa-system --samples 124 \
  --sample-delay 2 --sample-retries 1 --circuit-breaker 15 \
  --report reports/eval_replay_sol_high_full.md --gate
```

### 一之九、2026-10-07 全量 124 条真实评测完成

`gpt-6-sol` + `LLM_REASONING_EFFORT=high`（responses 协议）跑通全量，
**124/124 全部判分，零跳过零异常，`healthy=True`**：

| 指标 | 本批（gpt-6-sol, high） | 历史（2026-09-27, 非本模型） |
|---|---|---|
| Precision | **94.0%**（47/50） | 95.3% |
| Recall | **43.5%**（47/108） | 38.0% |
| F1 | **0.59** | 0.54 |
| 混淆矩阵 | TP=47、FP=3、FN=61、TN=13 | TP=41、FP=2、FN=67、TN=14 |

产物：[`reports/eval_replay_sol_high_full_replay.json`](../../reports/eval_replay_sol_high_full_replay.json)
报告：[`reports/eval_replay_sol_high_full.md`](../../reports/eval_replay_sol_high_full.md)

**口径与可解释性（必须随数字说明）**：

- 两次运行**模型不同、提示词版本可能不同**，因此 Recall 38.0% → 43.5% 只能算
  同量级改善的**观察**，不能当作受控对比结论；要下结论需要固定模型/提示词重跑历史版本。
- Recall 仍显著偏低（**漏报 61/108**），这是当前最大的质量问题。
  按模块看漏报分布相当分散（rag 11、app 11、react_agent 7、tools 7、llm 7、
  prompt 6、memory 5、utils 3、agent 2、config 2），**没有集中在某一两个模块** ——
  说明这是系统性的判定偏保守问题，而不是某处实现缺陷。
- 3 条误报：`tools/calculator.py:96`、`llm/base.py:26`、`rag/vector_store.py:1`。
  16 条 FP 陷阱中 13 条被正确拒绝（TN=13），FP 控制仍然较好。
- 运行过程中 1 条样本（`#113`）因网络读超时失败，已用
  `scripts/replay_retry_samples.py` 单独补跑并合并（结果 TN），
  避免为一条样本重跑整批（约 75 分钟 + 重复 token 成本）。

**本轮新增的两个运维脚本**（都源于真实评测的操作痛点）：

| 脚本 | 解决的问题 |
|---|---|
| `scripts/replay_report.py` | 从产物 JSON 重建 Markdown 报告 —— 改报告格式不必重跑 API |
| `scripts/replay_retry_samples.py` | 只补跑失败样本并合并结果 —— 不必为个别超时重跑整批 |
| `scripts/replay_analyze.py` | 混淆矩阵 + 按模块的漏报分布 + FP/TN 明细 —— 回答"错在哪" |
| `scripts/fn_attribution.py` | 逐条列出漏报的标注原文与代码上下文，供归因 |
| `scripts/fn_attribution_summary.py` | 汇总归因分布，并量化"标注失效"这一评测集效力风险 |

### 一之十、2026-10-07 漏报归因：**Recall 43.5% 不能按字面读**

对 61 个漏报逐条读源码归因（8 个并行 agent，每条都引用具体行号与代码证据；
其中 2 条我人工复核确认）。结论颠覆了此前对该读数的解读：

| 归因 | 条数 | 占比 | 含义 |
|---|---|---|---|
| **A 标注失效** | 29 | 48% | 标注描述的缺陷在当前代码里**不存在/已被修复/与代码矛盾**（含行号漂移） |
| **C 口径分歧** | 27 | 44% | 维护性/风格类（死代码、未用导入、缺类型注解、微优化…），模型答 NO 合理 |
| B 上下文不足 | 2 | 3% | 缺陷真实，但证据在 ±15 行窗口之外 |
| **D 真实漏报** | **2** | **3%** | 可见代码里就有明确缺陷而模型答 NO |
| E 无法判定 | 1 | 2% | 需运行时数据 |

**人工复核的两条典型反例**（标注与代码直接矛盾）：

- `react_agent.py:222` 标注称"超限强制总结 LLM 调用无异常处理" →
  源码 221–225 行就是 `try: ... except Exception:`，**声称的反面就是事实**
- `react_agent.py:97` 标注称"默认参数在类定义时求值导致实例共享" →
  源码在 `__init__` 体内每实例新建 `SlidingWindowMemory`，**不存在共享**

**真正值得修的 2 条**（模型确实判断错误）：

- `rag/embedding.py:79` `embed_query` 对空串不校验就拼前缀 encode，
  而同文件 `embed_documents:68-69` 有 `if not texts: return []` —— 同一文件里不对称
- `rag/document_loader.py:103` 硬编码 `encoding="utf-8"` 且无 `errors` 回退
  （119/144/167 行的 markdown/html/csv 加载同样问题），GBK 文件直接抛 `UnicodeDecodeError`

**严重度交叉（决定这件事的紧迫性）**：

| 归因 | High | Medium | Low |
|---|---|---|---|
| 标注失效 | 0 | 9 | 20 |
| 口径分歧 | 0 | 4 | 23 |
| 真实漏报 | 0 | 1 | 1 |

**High 严重度漏报 0 条 → High 召回 100%**（全数据集 4 个 High 全部命中）。
漏报**全部**集中在 Low（47/61）与 Medium（14/61）。

**对产品决策的含义**：

- 该配置在"能被检出的问题"上表现良好：Precision 94%、High 召回 100%
- 但**当前数据集无法支撑"Recall 是否达标"的判断** —— 必须先修标注
- ⚠️ **修标注之前不要为提高 Recall 去放宽判定提示词**：那会让模型开始对
  "已被修复的旧问题"报 YES，推高误报而无真实收益

**由此暴露的评测集效力风险**：`preflight()` 只校验
「文件存在 + 行号在范围内」，**不校验「该行是否仍有标注所描述的问题」**。
48% 的漏报落在后者 —— 这是当前评测体系最大的方法论缺口，
比"三份矛盾读数"更根本。已记录为下一步最高优先级。

### 一之十一、2026-10-13 召回率提升：先建立可计量的基础

用户决定"优先提升召回率"。为此必须先解决**分母不可信**的问题——
否则任何"召回提升"都可能只是在对已修复的旧问题报错。本轮补三件事：

#### 1. 标注有效性叠加层（`tests/eval/validity.py`）

把逐条归因结果变成机器可读的有效性映射，并给出两个口径的召回率：

| 口径 | 召回率 | 分母 |
|---|---|---|
| 原始 | 43.5% | 108（全部真实问题标注） |
| **有效** | **59.5%** | 79（排除 29 条语义失效） |

有效分母 = VALID（49）+ CRITERION_DISPUTED（30）。
STALE（29）**不计入**：它们的缺陷在当前代码里已不存在，任何提示词都无法召回。
未召回的 32 条 = 30 条口径分歧（可由提示词拿回）+ 2 条真实漏报。

CLI：`python -m tests.eval.validity reports/<replay>.json`

#### 2. 判据变体与 A/B 对比（`tests/eval/judge.py`、`scripts/judge_ab.py`）

此前 `JUDGE_PROMPT` 与 `CONTEXT_LINES=15` **硬编码**，想试另一个判据就得改源码，
无法 A/B、无法复现"哪一版更好"。现在：

- 判据与窗口集中到 `tests/eval/judge.py`，两版并存：
  `strict`（历史口径，严格）/ `lenient`（把客观可验证的低优先级缺陷纳入 YES）
- 窗口可按变体或 `JUDGE_CONTEXT_LINES` 覆盖，用于**隔离**"提示词"与"窗口"的影响
- `scripts/judge_ab.py` 输出对照表，并**强制同时报告误报变化** ——
  只看召回率会得出错误结论
- 判据版本与窗口落盘到产物 `meta`：否则两次读数不同却无法解释差异

顺带修掉一个归因盲区：判分 `judge()` 此前只返回布尔值，产物里 `answer` 恒为
`"NO"`，"模型为什么答 NO"完全无法查证。现在返回 `(判定, 模型原话)`。

#### 3. 标注锚点指纹（`tests/eval/anchors.py`）

防止**未来**再次出现"48% 标注失效却无人发现"。为每条标注记录
标注行与上下文的**规范化文本哈希**，代码变化后机械判定：

| 状态 | 含义 | 处理 |
|---|---|---|
| OK | 内容未变 | 标注仍有效 |
| RELOCATED | 内容仍在、行号漂移 | 给出新行号，可自动修正 |
| CHANGED | 该行内容已变 | **需人工复核**（缺陷可能已修复） |
| MISSING | 文件/上下文找不到 | 需重新定位 |

归一化宽容到"缩进/格式化/注释改写不报警"，敏感到"语句被改写就报 CHANGED"。
已为两份数据集生成指纹（`tests/eval/anchors_*.json`），CI 加入自评集校验。

**必须说明的边界**：锚点只能防止**未来**失效 —— 它记录的是"构建锚点那一刻"
的内容。已有的 29 条语义失效是靠一次性逐条读源码归因发现的，**无法自动化**。

#### 4. 未做的事（诚实记录）

- **没有修改被评测项目** `llm-agent-qa-system`。那 2 条"真实漏报"是它的代码缺陷，
  修改它会把可评测样本变成失效样本，等于自己降低评测难度。
- 全量 124 条的 A/B 重跑未做（约 2.5 小时 + 重复 token）；先用前 40 条做受控对比。
- STALE 标注未删除也未改写，只标记为不计入分母：原始数据保持可追溯。

### 一之十二、2026-10-13 召回率实验 #1：**"放宽判据"假设被证伪**

结论先行：**放宽判定判据 + 放大上下文窗口不仅没提升召回率，还丢掉 8 个原本
正确的检出，零真实收益。** 完整报告见
[`reports/recall_experiment_01.md`](../../reports/recall_experiment_01.md)。

先建立计量基础（否则"提升"可能是假的）：

| 口径 | 召回率 | 分母 |
|---|---|---|
| 原始 | 43.5% | 108（全部真实问题标注） |
| **有效** | **59.5%** | 79（排除 29 条语义失效） |

受控对比（同 40 条样本，strict@±15 基线**从既有产物免费提取**）：

| 指标 | strict @±15 | lenient @±40 | 变化 |
|---|---|---|---|
| Precision | 93.8% | 100.0% | +6.2% |
| **Recall** | **38.5%** | **23.1%** | **−15.4%** |
| TP | 15 | 9 | **−6** |

逐样本翻转：**FN→TP 2 条（全部是 STALE 标注，属假改善）**、
**TP→FN 8 条（基线已检出）**。**可信改善 = 0。**

**为什么变差**：对 22 条两轮都判 NO 的持久性漏报做理由归因 ——
**13 条（59%）模型明说"代码正确/无问题"**，且"认为无关紧要/风格"与
"证据不足"均为 **0 条**。模型没有敷衍，它明确说这些代码是对的。

这与独立人工归因**逐条吻合**（例：`react_agent.py:97` 模型说"correctly preserves
a supplied memory instance"，人工归因说"在 `__init__` 体内每实例新建，不存在共享"）。
**两个独立来源同一结论 → 这些位置是标注失效，不是模型推理错误。**

**由此得到的判断**：判据措辞不是杠杆。真正的阻塞是
**28% 的标注（29/108）描述的是当前代码里已不存在的缺陷**。
在这部分清理完之前，任何判据调整都会产生"看起来的提升"，
且很容易来自 STALE 标注上的假改善。

**下一步（按证据排序）**：

1. 先修标注（29 条 STALE 逐条重定位或标为不计入分母；锚点指纹已可防未来失效）
2. 修完重跑基线，拿到可信 Recall，再谈目标
3. 若届时 Recall 仍低，换杠杆到**提问框架**（如开放式枚举），
   而不是继续调判据措辞。⚠️ 把标注 category 作为提示输入属标签泄漏，不建议

**本轮留下的可复用能力**：

| 工具 | 作用 |
|---|---|
| `tests/eval/validity.py` | 有效性叠加，算出调整后召回率与分母 |
| `tests/eval/judge.py` | `strict`/`lenient` 判据变体 + 可配窗口（受控实验前提） |
| `scripts/judge_ab.py` | A/B 运行器，强制同时报告误报变化 |
| `scripts/judge_compare.py` | 净变化 + 逐样本翻转 + 按有效性分层 |
| `scripts/fn_reasons.py` | 用模型理由做归因（离线、零 API 成本） |
| `scripts/ab_from_log.py` | 从日志恢复逐样本判定（兼容 PowerShell 的 UTF-16 输出） |
| `tests/eval/anchors.py` | 锚点指纹，防止标注**未来**失效 |

### 一之十三、2026-10-13 标签修正（v1.1.0）与召回率提升实验 #2

#### 1. 标签修正：29 条 STALE 的处置

逐条读源码复核后（并行 agent 提方案 + 我逐条核验影响召回声明的 3 条）：

| 处置 | 条数 | 含义 |
|---|---|---|
| RELOCATE | 2 | 缺陷仍在，只是行号漂移（`prompt.py` 75→76、85→106） |
| RELABEL_NEGATIVE | 27 | 该位置代码现在正确 → 翻成负例（FP 陷阱），描述加【已修复】前缀 |

**完备性**：总数仍 **124 条**，正例 108→81、负例 16→43。
不删除样本 —— 否则"召回率提升"会来自分母变小而非能力提升。

未被改动的部分：27 条"判据口径分歧"保持原样（缺陷客观存在，只是优先级低），
以及 2 条真实漏报（属被评测项目的代码缺陷，**故意不去修改它**，
否则可评测样本会变成失效样本）。

#### 2. 零 API 成本拿到修正后的真实读数

标签变了而模型回答没变 → 同一批产物可重算：

| 指标 | v1.0.0（旧标签） | v1.1.0（修正后） |
|---|---|---|
| Precision | 94.0% | 94.0% |
| **Recall** | 43.5% | **58.0%** |
| F1 | 0.59 | **0.72** |
| TP / FP / FN / TN | 47 / 3 / 61 / 13 | 47 / 3 / 34 / 40 |

**Recall 提升的 14.5 个百分点不是模型变好，是分母变得诚实了。**
跨版本读数不可比较，引用必须带 dataset version。

#### 3. 顺带发现并修复的两个工具缺陷

**① 同一位置的重复标注会让工具静默丢样本**

`tools/calculator.py:105` 有两条标注（SECURITY「eval 指数爆炸 DoS」+
BUG「eval 返回 inf/NaN 未处理」）。两条都是真实问题，因此**保留**；
但所有用 `{(file, line): 值}` 建索引的脚本都会**静默保留最后一条**。

修复：分析工具改用 `(file, line, 第几次出现)` 作为键；`preflight()` 新增
重复位置告警（warning，不是 error —— 重复本身不是错），
这样"该位置权重为 2、一次检出计入 2 次"变得可见。

**② `re.sub` 把标注描述里的 `\1` 当成组引用**

`react_agent.py:366` 的标注描述含 `\1`（正则反向引用），
用字符串做替换时 `re.sub` 报 `invalid group reference`。改用 lambda 替换。

#### 4. 召回率提升实验 #2：换提问形状（`enumerate` 变体）

**假设**：判据放宽已被证伪（实验 #1），但二值提问"这一行有 bug 吗？"
本身可能诱导答 NO。改为**开放式枚举**：
模型输出 `NONE` 或若干 `DEFECT|<类别>|<一句话>` 行。

设计上**只改一个变量**：提示词内容仍是 strict 的判据清单，窗口仍为 ±15，
只有"提问形状 + 输出格式 + 解析规则"变了。

关键实现：`judge.parse_verdict(variant, response)` 把解析规则与提示词放在一起
——否则"换了提示词却忘了换解析"会把读数**静默**算错，那类比模型判错更难发现。

**结果（部分完成，5/40 条）**：

| 分组 | 已完成 | 结果 |
|---|---|---|
| 口径分歧（待召回） | 3/30 | **全部仍是 FN**（模型回 `NONE`） |
| TP 对照（查回归） | 2/10 | 全部仍是 TP（**无回归**） |

三条未召回的样本与独立归因**逐条吻合**（`react_agent.py:242` 未抽公共方法、
`react_agent.py:281` thinking 流式取舍、`prompt.py:52` XML 协议约定）——
两个独立来源一致说明：**不是模型没看出来，是模型认为这不算缺陷**。

**结论（与实验 #1 合并后）**：

> 剩余召回缺口不是提示词问题，而是"什么算问题"的**产品口径分歧**；
> 放宽判据会连带丢掉真实检出，换提问形状也无效。

要改变这个数只能做产品决策：要么把这些维护性问题作为**独立低严重度类别**
明确要求报出，要么接受不报并把 Recall 目标定在 ~58-62% 且写进口径。
完整报告见 [`reports/recall_experiment_02.md`](../../reports/recall_experiment_02.md)。

**成本现实**：枚举变体在 `reasoning_effort=high` 下单样本 4-5 分钟
（约 strict 的 4-8 倍），全量 124 条估算 3.6 小时 → **不适合作为常规评测配置**，
只适合小样本定位实验。

**⑥ 由此暴露的数据集遗留问题（待跟进）**

枚举变体在 `react_agent.py:222`（我翻成负例的样本）报出了
`DEFECT|ROBUSTNESS|The broad except Exception ...`。
原标注称"无异常处理"确实不成立（有 try/except），但**过宽的 except 是另一个
真实缺陷**（本项目自身整改也把宽泛 except 当缺陷）。

→ 说明：**翻成负例的 27 条里，可能有一部分仍含其它真实缺陷**。
把它们当"应当沉默"的负例会让模型报出真问题时被计为误报，**低估精确率**。
处置：用 `--keys-file reports/_keys_flipped.json` 跑一遍枚举变体，
把报出具体可验证缺陷的样本重新评估为 REPLACE。本次因成本未执行。

### 一之十四、2026-10-13 翻转负例复核（v1.2.0）：3 条改回正例

上一条的遗留问题已用**离线方式**解决（27 条逐条读源码 + 并行 agent 提证 +
我逐条核验，未调用 API）。完整报告见
[`reports/flipped_review_v120.md`](../../reports/flipped_review_v120.md)。

| 结论 | 条数 |
|---|---|
| 确认干净（保持负例） | 24 |
| **仍有其它真实缺陷（改回正例并替换描述）** | **3** |

3 条缺陷（均已自己打开源码核验）：

| 位置 | 缺陷 |
|---|---|
| `react_agent.py:222` | 超限强制总结的响应（223 行）未经 `_extract_final_answer` 解析，原始 `<thinking>` 标签直接返回给用户；同函数 167 行正常路径走的是解析后分支 |
| `rag/retriever.py:78` | `retrieve_and_format` 中 `source` 为死存储（98 行赋值后从未读取），且该方法全仓无引用（死代码） |
| `config/settings.py:65` | `current_api_key`/`current_base_url` 全仓无引用，与 `create_llm_adapter` 的 provider 分发重复 |

**结果：正例 81 → 84，Recall 58.0% → 56.0%（下降）。**
这是刻意的 —— 修正标签不以"让读数变好"为目标。若继续把这 3 条当负例，
模型在那些位置报出真问题时会被计为误报，那是**已知的读数错误**。

**一个值得记录的 agent 失效模式**：复核中有 agent 在 evidence 里声称
"仓库的 `scripts/verify_flipped_defects.py` 已列出三条缺陷结论（81→84）"——
**纯属臆测**，该脚本只打印源码片段、不含任何结论。它读到脚本结构后
把它当成了结论文件。实质判断（NONE）有它自己引用的代码支撑，不受影响；
但这提示：**并行 agent 的"旁证式引用"必须自己确认**，否则会把幻觉当佐证。

### 一之十五、2026-10-13 决策：接受 56.0% 作为权威基线

产品决策：**不再为提高召回率调整判据**，接受当前读数并把它固化为权威基线。
完整口径见 [`docs/eval-baseline.md`](../eval-baseline.md)，
机器可读记录见 [`tests/eval/replay_baseline.json`](../tests/eval/replay_baseline.json)。

**基线**：`gpt-6-sol` / high 思考度 / strict 判据 / ±15 窗口 / v1.2.0 数据集
→ **P=94.0%、R=56.0%、F1=0.70**（TP=47、FP=3、FN=37、TN=37，124/124 判分成功）

**为什么接受**：37 条漏报中 30 条是"模型认为不该报、标注标准认为该报"的
口径分歧，只有 7 条属"模型该报而没报"。两条提示词路径均已实验证伪
（放宽判据丢 8 个正确检出、枚举提问无法召回）。

**为了让"接受"不等于"放任"，把它做成可执行的**：

| 工具 | 作用 |
|---|---|
| `scripts/replay_baseline.py record` | 从产物生成基线记录（含量出处与漏报构成） |
| `scripts/replay_baseline.py check` | 新产物对比基线，**回归 exit 1、不可比 exit 3** |
| `scripts/replay_baseline.py show` | 打印基线与漏报构成 |

**关键设计：不可比的运行必须拒绝比较**，而不是给出一个看似合理的差值。
数据集版本 / 模型 / 判据 / 窗口 / 判分方式任一不同即 exit 3 ——
历史上正是"拿不可比读数相减"产出了三份互相矛盾且无法调和的读数。

**顺带修复**：`recompute_metrics.py` 的产物此前保留**原始运行时的旧版本号**，
任何下游消费者（含可比性判断）都会读到错版本。现在重算时改写
`meta["dataset_version"]` 为标签当时的版本。

新增 `tests/test_replay_baseline.py`（19 项）锁定门禁行为：可比性拒绝、
推断字段容差、容差内不报回归、方向判定、以及"漏报构成之和必须等于 FN"的自洽断言。

### 二、企业级落地

| 缺口 | 现象 | 处理 | 证据 |
|---|---|---|---|
| **限流实际未生效** | `Limiter(default_limits=...)` 只挂 `app.state`，**没有挂 `SlowAPIMiddleware`** → `/analyze`（分钟级 + 真实 token）、`/findings/search`、`/dashboard` 完全不受限流 | 挂上中间件；限流键改为"优先用户名、回退 IP"（反代后不再共用一个桶） | `src/api/server.py`、`tests/test_api_rate_limit.py` |
| **密钥打进镜像** | 无 `.dockerignore` 且 `COPY . .` → `.env`（API key/JWT 密钥/管理员口令）、`data/code_review.db`、`users.json`、`logs/` 全部进镜像层 | 新增 `.dockerignore` 作为安全边界；Dockerfile 改多阶段 + 非 root + HEALTHCHECK + 去掉构建链；compose 加 healthcheck 与资源限额 | `.dockerignore`、`Dockerfile`、`docker-compose.yml`、`scripts/check_container_config.py` |
| 配置拼错静默回退 | `CODE_REVIEW_ALLOWED_ROOTS`（复数）不会报错，悄悄用默认根目录 | 启动期按变量名做未知配置项检测并告警 | `src/api/server.py::_warn_config_typos` |
| CI 缺口 | 无覆盖率下限、无脚本 lint、单 Python 版本、无交付面校验 | 覆盖率达到 56% → 设 50% 地板；lint 纳入 `scripts/`；3.9/3.11 矩阵；新增容器配置作业 | `.github/workflows/ci.yml` |
| 迁移脚本误导 | 文档命令必然 `ModuleNotFoundError`；裸 except 掩盖失败 | 改为 `python -m src.storage.migrate`，复用 `Database` 的迁移；只忽略"列已存在" | `src/storage/migrate.py` |

**仍未处理**（下一步优先级）：审计日志缺 actor/request_id（回答不了"谁在何时改了哪个文件"）；
`/analyze` 同步阻塞、无 202/job/取消/全链路 deadline；无 schema 版本表与备份/保留策略；
API 无 `DELETE /sessions/{id}`（合规缺口）；模型失败被降级成 HTTP 200 且回显上游错误体；
吊销列表为进程内内存（重启/多 worker 失效）。

### 三、产品化

| 缺口 | 现象 | 处理 |
|---|---|---|
| 指标树没有数据源 | `05_指标体系与实验方案.md` 定义了完整指标树，但没有任何文档说明**今天哪些算得出来** | 新增 `docs/product-manager/08_指标可达性与数据管道.md`：逐项标注 ✅已落地 / 🟡半落地 / 🔴仅规划，并写清口径边界 |
| 门禁建议未落地 | "离线故障集无关键回归才发布"只是建议 | 05 文档新增 §8.1，写明已自动化的三项判定与仍未自动化的三类及其阻塞原因 |
| 口径混用风险 | 用户价值指标（采纳率、有帮助率）被与工程指标并列，读者会误以为有数据 | 08 文档独立成章标注 🔴 无数据源，并指出这是商业化的关键缺口 |

## 本轮已完成（2026-08-18）

## 本轮已完成（2026-08-18）

本轮针对上表遗留项做了端到端收口，每一项都有对应回归测试：

- **所有者隔离收口（P0#4 遗留）**：`Database.get_messages` 增加 owner 校验；
  `VectorStore` 新增 `document_owners` 映射表与 `delete_by_session()`；
  会话删除同时清理全文索引，杜绝"会话已删仍可搜到"的悬空数据。
  新增 `tests/test_owner_isolation.py`（DAO+向量库）与
  `tests/test_api_user_isolation.py`（端点级跨用户访问，8 项）。
- **命令执行 fail-closed（P0#13）**：`AgentHarness` 在未配置沙箱时直接拒绝
  `run_command`，不再回退到 `tool.fn(**args)`（原实现等于以宿主权限执行任意命令）；
  `Sandbox` 建不出项目内隔离目录时报错，不再静默回退 `%TEMP%`。
- **子进程密钥泄露（新增发现）**：`git_tools` 不再把 `os.environ` 全量传给 git/命令
  子进程（此前会透传 `ANTHROPIC_AUTH_TOKEN` / `JWT_SECRET_KEY` / `ADMIN_PASSWORD`），
  改为白名单 + 显式代理；`run_command` 支持 `cwd` 并默认使用最小环境。
- **HITL 默认放行（P0#3）**：`HumanInTheLoop` 在无审批回调时拒绝写操作
  （原实现 `approved = risk == MODERATE` 等于无人值守自动批准）；
  前端单 Agent 分支同样受 `auto_fix` 开关约束，默认仅审查。
- **默认口令（P0#7）**：登录页与 README 中失效的 `admin/admin123` 提示全部移除，
  并在 README 说明首启由 `ADMIN_PASSWORD` 提供、缺失即 fail-fast。
- **全量类型检查（P1#9）**：`mypy src/` 从 35 error 降到 0；规则固化到
  `pyproject.toml`，CI 由"5 个模块"扩大到整个 `src/`。
- **裸 except（P2#15）**：`pyproject.toml` 的 ruff 忽略项不再包含 `E722`，
  CI 也不再覆盖规则集；修复了 `git_tools`、`database`、`migrate` 中的裸 `except: pass`。
- **向量库可观测性与并发（P1#11）**：`add_finding` 失败改为记日志（不再静默返回空串）；
  `search()` 不再丢弃 `category/severity` 参数；`count()` 与搜索口径对齐（按 owner）；
  `_like_search` 降级路径不再抛异常；连接开启 `check_same_thread=False`（修复
  lifespan 线程建连、请求线程访问必然报错的问题）。
- **持久化可观测性（P1#10）**：Streamlit 的静默 `except: pass` 全部改为
  `format_frontend_error` + 用户可见提示 + 日志。
- **消息幂等（P2#10）**：`messages` 增加 `client_msg_id` 唯一索引；前端不再用
  "内容是否已存在"去重（该逻辑会把用户重复提问的第二条丢弃）。
  `"pending"` 外键导致的"助手回复从未落库"问题一并修复。
- **FINDING 解析（新增发现）**：抽出 `src/multi_agent/finding_parser.py` 作为唯一实现，
  API 与 LangGraph 编排共用；修复描述含 `|` 时 CN 描述与 fix 建议错位/丢失。
  `tests/test_finding_parser.py` 用反例锁定旧实现的错位行为。
- **CLI 能力不一致（P1#12）**：`cli_multi` 不再注册任何节点都不会绑定的
  `run_command`；旧编排提示中"用 run_command 跑测试"的误导文案删除。
- **配置漂移（P1#8/#14）**：`requirements.txt`/pyproject/README/CI 的依赖与检查命令
  统一到 `pyproject.toml`；补上缺失的 `python-dotenv` 依赖声明；
  `docker-compose.yml` 模型默认值对齐（`deepseek-v4-flash`）并移除无任何引用的 chromadb 服务；
  `migrate.py` 改为 `python -m src.storage.migrate`（原命令因导入根错误必然失败）。
- **测试样本语义（P2#16）**：`tests/bug_injection_sample.py` 还原为未被修复的纯基线，
  期望发现落盘为 `tests/fixtures/bug_injection_expected.json`；
  新增 `tests/test_bug_injection_baseline.py` 防止后续 E2E 再次就地改写基线。
- **文档漂移（P1#8）**：README 徽章、测试数量、评估命令、`/metrics` 鉴权说明全部校正；
  `docs/industrial-gaps.md` 的"当前状态"与"改进进度"不再自相矛盾。

## 本轮已完成（2026-08-17）

- **修复落盘证据**：LangGraph 只认可当前 `run()` 产生的写入 receipt（目标路径、写前/写后 SHA-256、备份路径、时间）。历史 `.bak` 不再能把未写入的结果误判为成功。
- **状态与行为验证**：修复先标为 `APPLIED`，只有项目根目录内、受限 `pytest` 命令通过后才标为 `VERIFIED`。没有目标测试、命令被拒绝或测试失败时均不会升级状态。
- **入口统一**：CLI、API、Streamlit 的多代理调用都经由 `create_langgraph_orchestrator` 创建同一个 LangGraph 编排器。CLI 新增默认关闭的 `--auto-fix`；API 维持只读工具集与默认仅审查。
- **回归测试**：新增 receipt 假阳性、行为验证、命令白名单、入口统一与报告完整性的测试，不触发真实模型网络请求。

## P0：立即整改

### 1. 三个入口使用不同的执行编排

**状态：已完成（2026-08-17）**。三个正式入口已统一到 LangGraph 工厂；旧 `MultiAgentOrchestrator` 仅保留为未迁移外部调用的兼容实现。

**现状**：Streamlit 使用包含 `plan -> execute_one -> review -> fix_one -> verify_fix` 的 `LangGraphOrchestrator`；CLI 和 API 仍使用只含 Planner、Executor、Reviewer 的旧 `MultiAgentOrchestrator`。

**风险**：同一请求从不同入口进入会得到不同能力与安全行为，CLI/API 不会真正走 Fixer 和修复验证。

**整改**：抽取统一的审查服务接口，让 CLI、API、前端共享同一编排；保留旧编排前必须明确标注只读兼容模式。增加三入口一致性集成测试。

**涉及文件**：`src/app/cli_multi.py`、`src/api/server.py`、`src/app/streamlit_app.py`、`src/multi_agent/langgraph_orchestrator.py`。

### 2. `write_file` 缺少项目根目录限制

**现状**：写入工具接受任意绝对路径。

**风险**：模型或恶意输入可能修改项目范围外的文件。

**整改**：在任务创建时保存并校验允许写入的项目根目录；对目标路径执行规范化并拒绝根目录外、符号链接逃逸和不在已批准文件集合内的写入。写入前展示 diff。

**涉及文件**：`src/tools/git_tools.py`、`src/harness/agent.py`、`src/multi_agent/langgraph_orchestrator.py`。

### 3. 自动批准会导致“只审查”也修改文件

**状态：已完成（2026-08-18）**。`HumanInTheLoop` 在无审批回调时拒绝写操作；
前端单/多 Agent 统一受 `auto_fix` 开关约束，默认仅审查。

**现状**：前端创建 `HumanInTheLoop(auto_approve_safe=True)`，`write_file` 被划为 MODERATE，默认可能自动批准。

**风险**：用户仅请求代码审查时，流程仍可能写文件。

**整改**：引入明确的 `review-only` 与 `auto-fix` 模式；默认只审查。自动修复应在任务开始时获得一次明确授权，并在每次实际写入前记录 diff、目标路径和审计事件。

**涉及文件**：`src/harness/auth.py`、`src/app/streamlit_app.py`、`src/multi_agent/agents.py`。

### 4. Streamlit 绕过用户所有者隔离

**状态：已完成（2026-08-18）**。会话、消息、日志、finding 与向量索引全部按
`owner_username` 过滤/写入；历史空归属数据 fail-closed（不授权任何登录用户）；
新增 DAO 级与 API 端点级跨用户隔离测试。历史数据的批量回填脚本仍未提供
（空归属会话只能由管理侧显式回填 `owner_username`）。

**现状**：API 为会话和 finding 传递 `owner_username`；Streamlit 读取会话和消息、创建会话、写入 findings 时未完整传递该字段。向量索引也没有所有者字段。

**风险**：不同用户可能看到、检索到或污染彼此数据。

**整改**：把当前登录用户贯穿 Session、Message、Finding 与向量索引的创建、读取、搜索和删除；为历史空所有者数据制定迁移和访问策略。

**涉及文件**：`src/app/streamlit_app.py`、`src/storage/database.py`、`src/memory/vector_store.py`、`src/api/server.py`。

### 5. 修复落盘验证可能产生假阳性

**状态：已完成（2026-08-17）**。验证节点只检查本次运行的 receipt 与当前文件哈希，不再以 `.bak` 是否存在判断写入成功。

**现状**：验证节点以 `.bak` 文件存在作为 `write_file` 本次成功的证据。旧备份也会满足条件。

**风险**：流程会将未实际写入的 finding 标为 `FIXED`。

**整改**：写入工具返回本次操作 ID、原内容哈希、新内容哈希、备份路径和写入时间；验证节点只接受与本次操作匹配的证据。明确备份版本化或覆盖策略。

**涉及文件**：`src/tools/git_tools.py`、`src/multi_agent/langgraph_orchestrator.py`。

### 6. 修复验证不足以证明业务正确性

**状态：已完成（2026-08-17）**。状态已区分 `APPLIED` 与 `VERIFIED`；只有受控的目标 `pytest` 测试通过才会标记为 `VERIFIED`。

**现状**：`verify_fix` 主要检查语法和文件完整性。

**风险**：例如把 `eval` 替换为 `ast.literal_eval` 虽更安全，但会改变对算术表达式的支持范围；仅能编译不能证明需求仍成立。

**整改**：区分 `APPLIED` 和 `VERIFIED`；Fixer 必须生成或选择目标行为测试，并在受控环境执行。测试不能运行时，状态保持 `APPLIED`，不能标为 `VERIFIED`。

**涉及文件**：`src/multi_agent/langgraph_orchestrator.py`、`src/multi_agent/agents.py`、`tests/`。

### 7. 公开文档与 UI 仍宣传失效默认管理员密码

**状态：已完成（2026-08-18）**。登录页与 README 中全部默认口令提示已删除，
改为说明首启由 `ADMIN_PASSWORD` 提供、缺失即 fail-fast。
⚠️ 仍需人工处理：**已部署实例中若曾使用弱口令，必须轮换该账号密码**，
代码侧无法替你修改已存在的 `data/users.json`。

**现状**：README 与前端登录页显示 `admin/admin123`，而当前启动逻辑已要求通过环境变量提供管理员密码。

**风险**：误导部署者并鼓励弱凭据。

**整改**：删除默认账号提示，说明首次启动所需的 `ADMIN_PASSWORD` 与 `JWT_SECRET_KEY`，并校验强度与缺失场景。

**涉及文件**：`README.md`、`src/app/streamlit_app.py`、`.env.example`（待新增）。

## P1：本迭代整改

### 8. 文档乱码、过期且缺少可复制配置示例

**现状**：现有 `docs/industrial-gaps.md` 已乱码；README 中部分架构和能力描述过期；仓库没有 `.env.example`。

**整改**：统一 UTF-8，重写启动、配置、三入口能力差异和修复安全策略；新增不包含真实密钥的 `.env.example`。

### 9. 全量静态类型检查未通过

**状态：已完成（2026-08-18）**。`mypy src/` 从 35 error（5 文件）降到 0 error（42 文件）；
检查参数固化在 `pyproject.toml [tool.mypy]`，CI 由局部模块扩大到整个 `src/`。
注：仍未开启 `--check-untyped-defs`，无类型标注的函数体不在检查范围内。

**现状**：`py -m mypy src --ignore-missing-imports --explicit-package-bases` 有 33 个错误，集中在 `llm_client.py`、`model_router.py`、`api/server.py`、`langgraph_orchestrator.py`、`streamlit_app.py`。

**整改**：逐模块补齐类型并消除重定义和可空值错误；CI 由局部检查升级为全 `src` 检查。

### 10. 前端和持久化失败被静默吞掉

**状态：部分完成（2026-08-18）**。Streamlit 中所有 `except: pass` 已改为
`format_frontend_error` + 用户可见提示；消息改为 `client_msg_id` 唯一索引的幂等写入，
不再按内容文本去重；`"pending"` 外键导致的回复丢失已修复。
待办：`Database`/`VectorStore` 的并发与事务边界仍以"每次操作一个短事务"为准，
未引入连接池或显式事务 API。

**现状**：Streamlit 等位置使用 `except Exception: pass`，消息、会话和索引失败不会通知用户。

**整改**：按异常类型记录日志，向用户显示可操作的失败信息；会话、消息和索引使用事务与幂等消息 ID，不能按内容文本去重。

**涉及文件**：`src/app/streamlit_app.py`、`src/storage/database.py`、`src/memory/vector_store.py`。

### 11. VectorStore 的并发与异常处理不足

**状态：已完成（2026-08-18）**。连接开启 WAL + `busy_timeout` +
`check_same_thread=False`（修复 lifespan 线程建连、请求线程访问必失败）；
写入失败改为记日志；`search()` 不再丢弃 `category/severity`；
`count()` 与检索口径一致（可按 owner）；`_like_search` 降级不抛异常；
新增 `delete_by_session()` 与 `document_owners` 表，检索按所有者过滤。

**现状**：对象长期持有单个 SQLite 连接，写入失败可能返回空字符串，检索未按所有者过滤。

**整改**：使用明确的连接生命周期和并发策略；让失败可观测；完成所有者字段迁移和 owner-aware 查询。

### 12. CLI 工具、安全提示和实际能力不一致

**状态：部分完成（2026-08-18）**。`cli_multi` 已移除任何节点都不会绑定的
`run_command`；旧编排中"用 run_command 跑测试"的误导提示删除；
命令执行本身改为 fail-closed（无沙箱即拒绝）。
待办：若将来要恢复命令执行，必须先接入 Sandbox + HITL 并把工具名加入节点工具列表，
同时实现白名单、超时与审计。

**现状**：CLI 暴露 `run_command`，旧编排提示 Executor 运行测试，但编排和工具绑定行为不完整，也未接入 Sandbox、HITL、ContextMemory 和完整 LangGraph。

**整改**：统一到新编排或移除误导接口。若保留命令执行，必须实现项目根约束、命令白名单、超时、审计和真正隔离。

### 13. Sandbox 不是生产级安全边界

**状态：部分完成（2026-08-18）**。已做到 fail-closed：无沙箱时 `run_command`
直接拒绝执行；沙箱建不出项目内隔离目录时报错而非回退 `%TEMP%`；
子进程环境改为白名单，宿主密钥不再透传。
待办（未完成）：`DockerSandbox` 仍未被任何入口实例化；
"生产模式强制容器隔离"，以及网络/FS/资源硬限制仍未落地。

**现状**：代码已说明当前 Sandbox 不是真正隔离，Docker 隔离也不是生产路径的硬前提。

**整改**：生产模式强制容器隔离；无法隔离时禁用 `run_command` 并 fail-closed。UI 和文档必须如实说明限制。

### 14. 依赖声明可能漂移

**状态：部分完成（2026-08-18）**。`pyproject.toml` 定为唯一来源：
ruff/mypy/pytest 规则与参数固化其中，CI 安装改为 `pip install -e .`，
不再用命令行 `--ignore` 覆盖；补齐了被使用却未声明的 `python-dotenv`；
`docker-compose.yml` 模型默认值与 `.env.example`/`config.py` 对齐并移除无引用的 chromadb。
待办：`requirements.txt` 仍与 pyproject 并存（Dockerfile 已改用 `pip install -e .`），
彻底消除需要删除该文件或改为从 pyproject 生成。

**现状**：`requirements.txt`、`pyproject.toml`、README 和 CI 分别维护安装说明。

**整改**：确定单一依赖来源，并让 CI、开发环境和文档从同一约束生成或校验。

## P2：维护性与测试债务

### 15. 清理高风险路径中的宽泛异常捕获

**状态：部分完成（2026-08-18）**。`streamlit_app.py`、`database.py`、
`migrate.py`、`vector_store.py`、`git_tools.py` 中的裸 `except` / `except: pass`
已全部替换为具体异常类型 + 日志或用户可见提示；
ruff 忽略项移除 `E722` 且 CI 不再覆盖规则集，新的裸 `except` 会被直接拦下。
待办：`sandbox.py` 内的兜底 `except Exception` 仍需逐个评估（部分属于
"杀进程/清理"路径，吞掉更安全，但应逐处注释原因）。

优先处理 `streamlit_app.py`、`sandbox.py`、`vector_store.py` 和 `database.py` 的裸 `except` 或吞异常逻辑；每处明确恢复策略和中文原因注释。

### 16. 明确测试样本的仓库语义

**状态：已完成（2026-08-18）**。`tests/bug_injection_sample.py` 已还原为
**未修复的漏洞注入基线**（撤销了历史提交中被 Fixer 就地改写的两处）；
期望发现清单落到 `tests/fixtures/bug_injection_expected.json`；
新增 `tests/test_bug_injection_baseline.py`，一旦后续 E2E 再次就地改写基线即失败。

`tests/bug_injection_sample.py` 已在真实 Fixer 流程中被修改，但尚未提交。应明确它是“漏洞注入基线”还是“修复后样本”；建议保留基线并创建独立的期望输出 fixture，避免测试输入被流程副作用改变。

### 17. 建立覆盖完整工作流的测试矩阵

**状态：部分完成（2026-08-18）**。已覆盖 receipt、行为验证、命令白名单、
三入口编排一致性、DAO 级所有者隔离、**API 端点级跨用户访问**（8 项）、
HITL fail-closed、漏洞基线不被改写。
待办：Streamlit 端到端仍只能做源码字符串断言——需要先把
`_load_sessions` / `_current_username` / 持久化同步逻辑抽到不 import streamlit
的模块，才可能做真正的单元测试（这也是覆盖率 52% → 80% 的主要障碍）。

**状态：部分完成（2026-08-17）**。已覆盖 receipt、行为验证、命令白名单及三入口编排一致性；所有者隔离、持久化和完整端到端模型调用仍待后续整改。


增加审查、审批、拒绝写入、路径越界、修复、行为验证、持久化、用户隔离、CLI/API/前端入口一致性的测试，并在 CI 显示对应结果。

## 推荐执行顺序与验收

1. 先完成 P0 的入口统一、写入边界、审批、所有者隔离和修复证据，阻断越权与误修复。
2. 再完成 P1 的文档配置、类型检查、错误可观测性、向量存储与 Sandbox。
3. 最后处理 P2 的异常清理、测试样本语义和工作流测试矩阵。

每项整改的验收条件是：相应自动化测试通过、中文注释与中文说明完整、未出现新的跨入口行为差异或用户数据越权。
