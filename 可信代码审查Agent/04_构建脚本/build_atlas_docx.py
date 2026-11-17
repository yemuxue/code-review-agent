#!/usr/bin/env python3
"""组装《核心图表图册》docx。

结构对齐 5.6：封面 → 使用说明与图目录 → 按分类分组 → 每图「回答的问题 / 怎么读 / 源文件」。
"""
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BASE = Path(__file__).resolve().parent.parent
PNG = BASE / "02_核心图_PNG"
OUT = BASE / "01_正式文档" / "可信代码审查Agent-核心图表图册.docx"

NAVY = RGBColor(0x1F, 0x2A, 0x44)
BLUE = RGBColor(0x2E, 0x6B, 0xFF)
MUTED = RGBColor(0x5A, 0x66, 0x78)
INK = RGBColor(0x17, 0x20, 0x33)
LIGHT = "F5F8FF"
LINE = "DCE5F2"

# 分组：(标题, 说明, [图])
GROUPS = [
    ("一、业务与产品", "先说清边界：谁在用、有哪些能力、谁不能看什么。", [
        dict(no="F01", file="01_F01_系统上下文.png", title="系统上下文图",
             q="谁使用它、它读写哪些外部系统？",
             read=["三个角色（作者 / Reviewer / 管理员）分别进入不同能力面，管理员不读业务内容。",
                   "外部只依赖四类系统：代码托管、模型服务、CI、审计存储。",
                   "箭头颜色区角色：蓝=作者、绿=Reviewer、琥珀=管理员。"],
             src="F01_系统上下文图.puml", w=15.5),
        dict(no="F02", file="02_F02_业务能力地图.png", title="业务能力地图",
             q="六个入口怎么分组、谁主责？",
             read=["工作 / 质量 / 治理三组，共六个入口。",
                   "作者与 Reviewer 落在「工作」，管理员只落在「治理」。",
                   "质量报告是三方共用的读面。"],
             src="F02_业务能力地图.puml", w=16),
        dict(no="F03", file="03_F03_角色与权限矩阵.png", title="角色与权限矩阵",
             q="谁能做什么、谁不能看什么？",
             read=["越界的两条：作者不能放行；管理员不能看代码与发现详情。",
                   "「确认 / 忽略发现」作者只对自己任务生效，Reviewer 对全部任务生效。",
                   "放行与阻断是 Reviewer 专属，且必须先过前置阶段。"],
             src="（由 render_tables.py 生成）", w=15.5),
    ]),
    ("二、分角色用例", "每个角色只画自己的主路径，避免一张图塞下所有人。", [
        dict(no="U01", file="04_U01_代码作者用例.png", title="代码作者用例",
             q="作者从提交到验证要经过哪些步骤？",
             read=["主路径：提交变更 → 看发现 → 确认 → 生成补丁 → 运行验证。",
                   "补丁只读预览，验证失败保留证据、不标记修复成功。"],
             src="U01_代码作者用例.puml", w=14.5),
        dict(no="U02", file="05_U02_Reviewer用例.png", title="Reviewer 用例",
             q="Reviewer 在什么条件下介入？",
             read=["默认不阻断：未命中 R1–R3 时结论自动通过，Reviewer 不必介入。",
                   "命中规则才进入待放行，放行与阻断都会写审计。"],
             src="U02_Reviewer用例.puml", w=15.5),
        dict(no="U03", file="06_U03_平台管理员用例.png", title="平台管理员用例",
             q="管理员能看什么、不能看什么？",
             read=["只看规则、质量、权限与审计；代码与发现详情对管理员隐藏。",
                   "规则变更与放行记录都进审计，形成可回放的治理链。"],
             src="U03_平台管理员用例.puml", w=15.5),
    ]),
    ("三、系统总览", "把「一次审查怎么走完」讲清楚：流程、状态、页面、时序。", [
        dict(no="D01", file="07_D01_主流程泳道图.png", title="主流程泳道图（竖版）",
             q="六阶段里，谁在哪一步做什么？",
             read=["三条泳道：作者 / 系统 / Reviewer；六个阶段从接入到结论。",
                   "验证失败回到修复，阻断回退到修复，两条回退都在图里画出来。",
                   "结论阶段才出现 Reviewer，前面阶段系统与作者闭环。"],
             src="D01_主流程泳道图.puml", w=11.5),
        dict(no="D02", file="08_D02_任务状态机.png", title="任务状态机",
             q="四态怎么迁移、失败回到哪？",
             read=["每个阶段只有四态：未开始 / 进行中 / 已完成 / 需修改。",
                   "失败不生成新版本，也不标记修复成功；修完可重跑。",
                   "任务级结果是三选一：进行中、已放行、已阻断。"],
             src="D02_任务状态机.puml", w=13),
        dict(no="D03", file="09_D03_页面流程图.png", title="页面流程图",
             q="S1–S8 页面之间怎么跳？",
             read=["入口是工作台，主路径是任务列表 → 阶段视图 → 发现 → 补丁 → 验证 → 结论。",
                   "放行与阻断是两条不同去向，阻断回到补丁页。",
                   "深链接可直接定位到某个任务的某个阶段。"],
             src="D03_页面流程图.puml", w=11),
        dict(no="D04", file="10_D04_一次审查时序图.png", title="一次审查的时序",
             q="系统之间按什么顺序调用？",
             read=["GitHub 只提供 diff 与上下文，不克隆整仓。",
                   "模型逐块审查后由 Agent 组装证据，CI 负责验证。",
                   "写回检查项与写审计是两个独立动作。"],
             src="D04_一次审查时序图.puml", w=14),
    ]),
    ("四、案例与规则", "用矩阵看覆盖，用流程看放行判定。", [
        dict(no="C01", file="11_C01_三场景覆盖矩阵.png", title="三场景覆盖矩阵",
             q="这套能力在 Web / 后端 / 数据管道上覆盖到哪？",
             read=["接入、发现、结论三类在三场景上全覆盖。",
                   "分析与修复对数据管道只覆盖一部分；验证对前端只覆盖一部分。",
                   "「部分」不是失败，是当前原型未验证的边界。"],
             src="（由 render_tables.py 生成）", w=14),
        dict(no="C02", file="12_C02_放行判定流程.png", title="放行判定流程",
             q="什么情况下需要人放行，什么情况下自动通过？",
             read=["R1 高危、R2 大变更、R3 首次接入，命中任一才进入待放行。",
                   "未命中自动通过，但同样写审计。",
                   "R4 只记录不阻断，不进放行判定。"],
             src="C02_放行判定流程.puml", w=12),
    ]),
    ("五、生产实现", "从原型到部署：一次请求经过哪些节点。", [
        dict(no="P01", file="13_P01_部署与数据流.png", title="部署与数据流",
             q="服务怎么部署、数据落在哪？",
             read=["Worker 无状态，可水平扩展；不落业务代码，只保留 diff 片段与证据。",
                   "审计与结果库存放放行记录与报告，页面只读。",
                   "写回代码托管是唯一的外部写动作，受审批约束。"],
             src="P01_部署与数据流.puml", w=13),
    ]),
]


def shade(cell, color):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), color)
    tcPr.append(shd)


def add_table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        p = c.paragraphs[0]
        r = p.add_run(h)
        r.bold = True
        r.font.size = Pt(9.5)
        r.font.color.rgb = NAVY
        shade(c, LIGHT)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(v))
            r.font.size = Pt(9)
            r.font.color.rgb = INK
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Cm(w)
    return t


def main():
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.2)
    sec.top_margin = sec.bottom_margin = Cm(2.0)

    style = doc.styles["Normal"]
    style.font.name = "PingFang SC"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")

    # 封面
    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("可信代码审查 Agent")
    r.font.size = Pt(30)
    r.bold = True
    r.font.color.rgb = NAVY

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("核心图表图册")
    r.font.size = Pt(22)
    r.font.color.rgb = BLUE

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("业务与产品 · 分角色用例 · 系统总览 · 案例与规则 · 生产实现")
    r.font.size = Pt(11)
    r.font.color.rgb = MUTED

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("配套《可信代码审查 Agent PRD》使用 · 内部评审稿")
    r.font.size = Pt(10)
    r.font.color.rgb = MUTED

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # 使用说明与图目录
    h = doc.add_heading("使用说明与图目录", level=1)
    for run in h.runs:
        run.font.color.rgb = NAVY

    doc.add_paragraph(
        "本图册选 13 张核心设计图：3 张业务与产品、3 张分角色用例、4 张系统总览、2 张案例与规则、1 张生产实现。"
        "每张图配「回答的问题 / 怎么读 / 源文件」，图下标注源文件；"
        "F/D/U/C/P 编号与 PRD 章节一一对应。"
    )
    doc.add_paragraph(
        "绘图方式：UML 2.x（PlantUML），组件图与状态图用 Graphviz dot 布局，"
        "活动图（泳道）用 PlantUML 内置布局；矩阵类图由脚本渲染。"
    )

    add_table(doc, ["项", "内容"], [
        ["图数", "13 张"],
        ["编号体系", "F 业务与产品 / U 分角色用例 / D 系统总览 / C 案例与规则 / P 生产实现"],
        ["源文件", "03_可编辑源文件/PlantUML/*.puml（矩阵类为 04_构建脚本/render_tables.py）"],
        ["渲染", "python3 04_构建脚本/build_atlas.py（200 dpi）"],
        ["口径", "全部为原型演示数据，数字不代表真实评测结果"],
    ], widths=[4, 12])

    doc.add_paragraph()
    rows = []
    for _, _, items in GROUPS:
        for it in items:
            rows.append([it["no"], it["title"], it["q"]])
    add_table(doc, ["编号", "图名", "回答的问题"], rows, widths=[2.2, 5.5, 9])

    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # 正文：按分组
    for gtitle, gdesc, items in GROUPS:
        h = doc.add_heading(gtitle, level=1)
        for run in h.runs:
            run.font.color.rgb = NAVY
        p = doc.add_paragraph(gdesc)
        p.runs[0].font.color.rgb = MUTED

        for it in items:
            p = doc.add_paragraph()
            r = p.add_run(f"图 {it['no']}  {it['title']}")
            r.bold = True
            r.font.size = Pt(13)
            r.font.color.rgb = INK

            img = PNG / it["file"]
            if img.exists():
                doc.add_picture(str(img), width=Cm(it["w"]))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            else:
                doc.add_paragraph(f"（缺少图片：{it['file']}）")

            p = doc.add_paragraph()
            r = p.add_run("回答的问题：")
            r.bold = True
            r.font.size = Pt(9.5)
            r.font.color.rgb = BLUE
            r2 = p.add_run(it["q"])
            r2.font.size = Pt(9.5)
            r2.font.color.rgb = MUTED

            p = doc.add_paragraph()
            r = p.add_run("怎么读")
            r.bold = True
            r.font.size = Pt(9.5)
            r.font.color.rgb = BLUE
            for line in it["read"]:
                bp = doc.add_paragraph(style="List Bullet")
                br = bp.add_run(line)
                br.font.size = Pt(9.5)

            p = doc.add_paragraph()
            r = p.add_run("📎 源文件：" + it["src"])
            r.font.size = Pt(8.5)
            r.font.color.rgb = MUTED

            doc.add_paragraph()

        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    # 结尾
    h = doc.add_heading("口径说明", level=1)
    for run in h.runs:
        run.font.color.rgb = NAVY
    for line in [
        "图中所有任务、仓库、数字均为演示数据，不代表真实评测结果。",
        "评测口径以 docs/eval-baseline.md 为准：v1.4.0，精确率 82.0%、召回 51.2%、F1 0.63，数据集 124 条全部审计。",
        "历史版本读数（v1.2.0 94.0%、v1.3.0 82.0%）不参与跨版本相减。",
        "图册随原型更新，源文件与渲染脚本见 03_可编辑源文件 / 04_构建脚本。",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print("写出", OUT.relative_to(BASE), OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
