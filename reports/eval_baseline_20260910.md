# Agent 评测基线报告

- **生成时间**: 2026-09-10 22:16
- **数据来源**: `logs`（84 个运行日志）
- **评测体系版本**: v1（Phase 1 指标库；故障注入用例集为 Phase 2，尚未接入）
- **日志坏行率**: 0.00%（0 行）

> 本报告是**基线**：只统计现有运行日志能算出的指标。数据源缺失的指标明确标注
> n/a 并给出原因，不用 0 冒充——这也是 Phase 0 遥测补全要解决的问题。

## 1. 数据源覆盖度

| 采集能力 | 覆盖运行数 | 占比 |
|----------|-----------|------|
| 带角色归因（role） | 0 | 0.0% |
| tool_call_start（工具调用起点） | 0 | 0.0% |
| node_stats（节点统计） | 0 | 0.0% |
| parse_result（结构化解析埋点） | 0 | 0.0% |
| 多 Agent 完整运行 | 0 | 0.0% |
| 运行已收尾（有 session_end） | 7 | 8.3% |

未收尾运行 77 个（进程中断或 Phase 0 前多 Agent 未接 logger）。此项属**采集缺口**，不计入失败分类——否则会把日志问题伪装成模型失败。

## 2. 轨迹指标（JD 核心）

| 指标 | 数值 | 样本量 | 目标 | 状态 | 备注 |
|------|------|--------|------|------|------|
| 工具调用成功率 | 99.0%（495/500） | 500 | ≥ 98% | ✅ 达标 | 失败构成: legacy(无 failure_kind，归因见 §4)×5 |
| 参数合法率 | n/a（样本不足：需 P0 后新日志（tool_call_start.args_ok）） | — | 建立基线 | — | 需 P0 后新日志（tool_call_start.args_ok） |
| Plan 可执行率 | n/a（样本不足：需 P0 后新日志（parse_result 事件）） | — | ≥ 95% | — | 需 P0 后新日志（parse_result 事件） |
| VERDICT 解析率 | n/a（样本不足：需 P0 后新日志（parse_result 事件）） | — | ≥ 95% | — | 需 P0 后新日志（parse_result 事件） |
| fix 输出解析率 | n/a（样本不足：需 P0 后新日志（parse_result 事件）） | — | 建立基线 | — | 需 P0 后新日志（parse_result 事件） |
| 空转率 | 0.0%（0/135） | 135 | ≤ 10% | ✅ 达标 |  |
| 循环率 | n/a（样本不足：需 P0 后新日志（tool_call_start.args_fingerprint）） | — | ≤ 0% | — | 需 P0 后新日志（tool_call_start.args_fingerprint） |
| fix 成功率 | n/a（样本不足：需 auto_fix 运行） | — | ≥ 70% | — | 需 auto_fix 运行 |
| 回归引入率 | n/a（样本不足：需 auto_fix 运行） | — | 建立基线 | — | 需 auto_fix 运行 |
| 端到端成功率 | 8.3%（7/84） | 84 | ≥ 90% | ❌ 未达标 |  |

## 3. 效率与成本

无 node_stats 数据（Phase 0 前的运行未持久化节点统计）。

- **总 token**: 0（84 次运行，平均 0.0/次）
- **成本/任务**: 未启用（需 `src/eval/pricing.py` 单价表，Phase 5）

## 4. 失败分类（F-01..F-09）

| 代码 | 类型 | 次数 |
|------|------|------|
| F-04 | 工具执行异常 | 5 |
| F-05 | 模型API故障 | 4 |

### F-04 工具执行异常（5 次）
- `20260720_223211_wcst` tool=read_file, detail=Tool error: TypeError: read_file() got an unexpected keyword argument 'offset'
- `20260721_004831_b0q9` tool=read_file, detail=Tool error: TypeError: read_file() got an unexpected keyword argument 'offset'
- `20260721_004927_eng7` tool=read_file, detail=Tool error: TypeError: read_file() got an unexpected keyword argument 'offset'
- `20260721_004927_eng7` tool=read_file, detail=Tool error: TypeError: read_file() got an unexpected keyword argument 'offset'
- `20260721_004927_eng7` tool=read_file, detail=Tool error: TypeError: read_file() got an unexpected keyword argument 'offset'

### F-05 模型API故障（4 次）
- `20260818_214109_mx34` error_type=RuntimeError, detail=Traceback (most recent call last):   File "X:\VScode\code-review-agent\src\llm_client.py", line 130, in chat     with urllib.request.urlopen(req, timeout=120) a …（老日志在 500 字处截断，栈尾根因不可见）
- `20260819_123105_ze5d` error_type=RuntimeError, detail=Traceback (most recent call last):   File "C:\Python314\Lib\urllib\request.py", line 1321, in do_open     h.request(req.get_method(), req.selector, req.data, he …（老日志在 500 字处截断，栈尾根因不可见）
- `20260903_142537_jwl7` error_type=RuntimeError, detail=Traceback (most recent call last):   File "C:\Python314\Lib\urllib\request.py", line 1321, in do_open     h.request(req.get_method(), req.selector, req.data, he …（老日志在 500 字处截断，栈尾根因不可见）
- `20260903_143309_hpc7` error_type=RuntimeError, detail=Traceback (most recent call last):   File "X:\VScode\code-review-agent\src\llm_client.py", line 130, in chat     with urllib.request.urlopen(req, timeout=120) a …（老日志在 500 字处截断，栈尾根因不可见）

## 5. 稳定性

| 指标 | 数值 | 样本量 | 目标 | 状态 | 备注 |
|------|------|--------|------|------|------|
| pass@k | n/a（样本不足：需 --repeat k 采集） | — | 建立基线 | — | 需 --repeat k 采集 |
| flaky 率 | n/a（样本不足：需 --repeat k 采集） | — | 建立基线 | — | 需 --repeat k 采集 |

## 6. 结论与下一步

- 可算指标 3/10（数据源缺失：参数合法率、Plan 可执行率、VERDICT 解析率、fix 输出解析率、循环率、fix 成功率、回归引入率）。
- 未达标指标：端到端成功率 —— 需在 Phase 2 用故障注入用例验证指标是否真能抓到对应故障，再决定修复优先级。
- 失败集中在 F-04 工具执行异常（5 次），对应 Phase 2 故障注入用例优先覆盖该路径。
- **下一步（Phase 2）**: 为每个指标补一条故障注入用例，证明指标真能抓住故障。

---

**指标定义**: [docs/agent-evaluation-plan.md](../../docs/agent-evaluation-plan.md) §4 ｜ **失败分类**: 同上 §4.5
