# Agent 真实评测报告（replay）

- 本报告来自**真实模型调用**；样本量与数据集版本见下表，跨版本读数不可直接比较。

## 1. 运行元数据

| 项 | 值 |
|----|----|
| 数据集 | llm-agent-qa-system-labeled v1.0.0 |
| 模型 | gpt-6-sol |
| 温度 | 0.0 |
| 判分方式 | per_sample_llm_judgement |
| 质量声明 | 真实模型判分 |
| 运行时间 | 2026-10-07T22:00:44+08:00 |
| 目标项目 | `X:/VScode/llm-agent-qa-system` |
| 健康判分 | True（跳过 0、异常 0） |


## 2. Replay 结果

- 目标项目: `X:/VScode/llm-agent-qa-system`
- 已扫描样本: 124/124
- Precision: 94.0%
- Recall: 43.5%
- F1: 0.59
- 混淆矩阵: TP=47、FP=3、FN=61、TN=13
- 明细产物: `reports\eval_replay_sol_high_full_replay.json`
