# 可信代码审查 Agent · 作品集

> 一个把「AI 生成评论」做成可核验、可处理、可回滚的审查单元的项目。
> 本目录按 5.6 / 5.8 两个项目的成品规范组织：正式文档、核心图册、可编辑源文件、构建脚本、历史版本。

## 目录

```text
可信代码审查Agent/
├── 01_正式文档/            PRD、可用性测试报告
├── 02_核心图_PNG/          核心图册（编号 + 图名），00_界面截图 放原型截图
├── 03_可编辑源文件/        SVG 图源、原型 HTML、实验数据
├── 04_构建脚本/            图册生成（Python）与 SVG→PNG 导出（Node）
└── 99_历史版本/            旧原型、旧线框图
```

## 核心图册

共 13 张，编号沿用 5.6 的 `F / U / D / C / P` 体系；成品见 `01_正式文档/可信代码审查Agent-核心图表图册.docx`。

| 编号 | 图名 | 回答的问题 | 源文件 |
|---|---|---|---|
| F01 | 系统上下文图 | 谁用它、它读写哪些外部系统 | `PlantUML/F01_系统上下文图.puml` |
| F02 | 业务能力地图 | 六个入口怎么分组、谁主责 | `PlantUML/F02_业务能力地图.puml` |
| F03 | 角色与权限矩阵 | 谁能做什么、谁不能看什么 | `render_tables.py` |
| U01 | 代码作者用例 | 提交 → 处理发现 → 补丁 → 验证 | `PlantUML/U01_代码作者用例.puml` |
| U02 | Reviewer 用例 | 复核 → 放行 / 阻断 → 审计 | `PlantUML/U02_Reviewer用例.puml` |
| U03 | 平台管理员用例 | 规则、审计、质量，不看业务内容 | `PlantUML/U03_平台管理员用例.puml` |
| D01 | 主流程泳道图（竖版） | 六阶段 × 三角色怎么协作 | `PlantUML/D01_主流程泳道图.puml` |
| D02 | 任务状态机 | 四态怎么迁移、失败回到哪 | `PlantUML/D02_任务状态机.puml` |
| D03 | 页面流程图 | S1–S8 页面怎么跳 | `PlantUML/D03_页面流程图.puml` |
| D04 | 一次审查时序图 | GitHub → Agent → 模型 → CI → Reviewer | `PlantUML/D04_一次审查时序图.puml` |
| C01 | 三场景覆盖矩阵 | Web / 后端 / 数据管道 × 六阶段 | `render_tables.py` |
| C02 | 放行判定流程 | R1–R4 → 自动通过 / 人工放行 / 阻断 | `PlantUML/C02_放行判定流程.puml` |
| P01 | 部署与数据流 | GitHub App → Worker → 模型 → 存储 | `PlantUML/P01_部署与数据流.puml` |

每张图的规范：左上角编号 + 标题 + 一句话副标题；底部图例；右上角可放口径注释；
浅色配色，角色用颜色区分；PNG 200 dpi，输出到 `02_核心图_PNG/`。

## 构建

图册用 **PlantUML**（UML 2.x 语法）绘制，源文件在 `03_可编辑源文件/PlantUML/*.puml`，
渲染脚本复用 5.6 项目自带的 JRE + plantuml.jar，不依赖本机 Java/Graphviz：

```bash
python3 04_构建脚本/build_atlas.py            # 渲染全部 PlantUML 图
python3 04_构建脚本/render_tables.py          # 渲染矩阵类图（F03/C01）
python3 04_构建脚本/build_atlas_docx.py       # 组装图册 docx
```

- 组件图、状态图：`!pragma layout smetana`（本机无 Graphviz，走内置布局）
- 活动图（泳道）：PlantUML 内置布局，竖版，不需要 dot
- 输出：`02_核心图_PNG/`，200 dpi
- 如需换布局引擎，改 `build_atlas.py` 里的 JRE/JAR 环境变量即可

## 文档

- [PRD](01_正式文档/可信代码审查Agent-PRD.md)（唯一来源，站点由 `sync_prd_to_site.py` 同步）
- [核心图表图册](01_正式文档/可信代码审查Agent-核心图表图册.docx)
- [可用性测试报告](01_正式文档/可信代码审查Agent-可用性测试报告.md)

站点 PRD 页面来自本仓库，不要再在站点里单独改：

```bash
python3 04_构建脚本/sync_prd_to_site.py            # 同步到 ../site
python3 04_构建脚本/sync_prd_to_site.py /path/to/site
```

## 历史

`99_历史版本/` 保留 v1 原型与旧线框图，仅作对照，不参与正文引用。
