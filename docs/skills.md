# Agent Skills 使用说明

## 目标

Agent Skills 为 Multi-Agent 编排增加可版本控制、按需注入的只读工作规则。它们用于细化
Planner、Executor、Reviewer 和 Fixer 的审查或修复方法，不替换基础 system prompt，也不改变
`FINDING`、`VERDICT`、`FIXED` 等机器可读状态行格式。

当前实现只应用于 LangGraph Multi-Agent 流程。Streamlit 的 Multi-Agent 模式、`/analyze`
接口的 `mode=multi`，以及 `python -m src.app.cli_multi` 共享同一套加载和选择逻辑；单 Agent
模式不会加载或注入这些技能。

## 目录结构

每个技能是仓库根目录 `skills/` 下的一个目录，入口文件固定为 `SKILL.md`：

```text
skills/
  security-review-rules/
    SKILL.md
  fix-encoding-safety/
    SKILL.md
```

默认加载目录是 `<repo>/skills`。可通过 `SKILLS_DIR` 环境变量覆盖全部正式入口的目录；CLI
还可用优先级更高的 `--skills-dir DIR` 覆盖：

```powershell
$env:SKILLS_DIR = "X:\skills"
python -m src.app.cli_multi analyze X:\target

python -m src.app.cli_multi analyze X:\target --skills-dir X:\skills
```

## SKILL.md 格式

文件必须以 YAML 风格 frontmatter 开始，`name` 与 `description` 为必填字段：

```markdown
---
name: security-review-rules
description: 安全审查时使用的本地规则
roles: [executor, reviewer, fixer]
triggers: [security, secret, token, 注入]
---

- 只读规则正文写在这里。
- 规则应当具体、局部，不要求模型扩大改动范围。
```

字段含义：

| 字段 | 说明 |
|---|---|
| `name` | 必填，唯一技能名；重名时按目录名排序，首个生效。 |
| `description` | 必填，显示在适用角色的可用技能索引中。 |
| `roles` | 可选，允许 `planner`、`executor`、`reviewer`、`fixer`；省略或为空时所有角色可用。 |
| `triggers` | 可选，任务文本的大小写无关关键词；省略或为空时不受关键词限制。 |

格式损坏、缺少必填字段、含未知角色、越过 skills 根目录的符号链接，都会被安全跳过并记录警告。

## 选择与注入规则

每一轮分析开始时，系统根据原始任务文本重新计算匹配，不依赖上一个会话的结果。对一个角色，
只有同时满足以下条件的技能才会注入完整正文：

1. `roles` 为空，或包含该角色。
2. `triggers` 为空，或任一关键词命中任务文本。

适用于角色但未命中关键词的技能仅保留名称和描述索引，不会注入规则正文。没有任何适用技能的
角色完全不追加内容，以保持旧行为不变。

例如任务包含 `security token injection` 与 `utf-8 编码` 时，当前内置 skills 的效果为：

| 角色 | 注入的完整 skill 正文 |
|---|---|
| `planner` | 无 |
| `executor` | `security-review-rules` |
| `reviewer` | `security-review-rules` |
| `fixer` | `security-review-rules`、`fix-encoding-safety` |

## 验证

新增或修改技能后，运行对应测试：

```powershell
python -m pytest tests/test_skills_loader.py tests/test_skills_selector.py tests/test_skills_orchestrator.py -q
```

完整项目测试命令为：

```powershell
python -m pytest -q
```
