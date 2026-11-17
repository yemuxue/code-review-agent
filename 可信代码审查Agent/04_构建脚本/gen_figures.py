#!/usr/bin/env python3
"""核心图册生成器：把每张图输出为可编辑 SVG（1600×900，16:9）。

用法：
    python3 gen_figures.py            # 输出全部
    python3 gen_figures.py F01 D01    # 只输出指定图

约定（与 5.6/5.8 图册一致）：
    - 左上角编号 + 标题 + 一句话副标题
    - 图底部图例；右上角可放口径注释
    - 白底、细线、圆角、单一强调色
"""
import sys
from pathlib import Path

W, H = 1600, 900
INK = "#172033"
MUTED = "#5a6678"
LINE = "#c9d2e0"
SOFT = "#eef2f8"
BLUE = "#2e6bff"
TEAL = "#0f766e"
AMBER = "#9a5b06"
RED = "#b4432c"
GREEN = "#14796d"

FONT = "-apple-system,'PingFang SC','Microsoft YaHei',sans-serif"
MONO = "Consolas,Menlo,monospace"

OUT = Path(__file__).resolve().parent.parent / "03_可编辑源文件" / "SVG"


def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def text(x, y, s, size=16, fill=INK, weight=400, anchor="start", mono=False, opacity=1):
    fam = MONO if mono else FONT
    return (f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}" opacity="{opacity}">{esc(s)}</text>')


def rect(x, y, w, h, fill="#fff", stroke=LINE, rx=10, sw=1.2, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def box(x, y, w, h, title, sub="", fill="#fff", stroke=LINE, tsize=17, ssize=12, accent=None):
    out = [rect(x, y, w, h, fill, stroke)]
    if accent:
        out.append(rect(x, y, 4, h, accent, accent, rx=2, sw=0))
    ty = y + h / 2 + (0 if not sub else -8)
    out.append(text(x + 16, ty, title, tsize, INK, 600))
    if sub:
        out.append(text(x + 16, ty + 20, sub, ssize, MUTED))
    return "".join(out)


def arrow(x1, y1, x2, y2, color=INK, dash=None, sw=2, label=None, lx=None, ly=None, curve=0):
    d = f"M {x1} {y1} L {x2} {y2}"
    if curve:
        mx, my = (x1 + x2) / 2 + curve, (y1 + y2) / 2
        d = f"M {x1} {y1} Q {mx} {my} {x2} {y2}"
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    head = (f'<path d="M {x2} {y2} L {x2-9} {y2-5} L {x2-9} {y2+5} Z" fill="{color}"/>')
    out = f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}"{dash_attr}/>' + head
    if label:
        out += text(lx if lx is not None else (x1 + x2) / 2, ly if ly is not None else (y1 + y2) / 2 - 8,
                    label, 12, color, 500, "middle")
    return out


def header(num, title, subtitle):
    return (
        text(64, 62, num, 15, BLUE, 700, mono=True)
        + text(64, 104, title, 30, INK, 700)
        + text(64, 136, subtitle, 15, MUTED)
        + f'<line x1="64" y1="160" x2="{W-64}" y2="160" stroke="{LINE}" stroke-width="1"/>'
    )


def legend(items, y=H - 46):
    x = 64
    out = [f'<line x1="64" y1="{y-26}" x2="{W-64}" y2="{y-26}" stroke="{LINE}" stroke-width="1"/>']
    for label, color, dash in items:
        out.append(f'<line x1="{x}" y1="{y}" x2="{x+26}" y2="{y}" stroke="{color}" stroke-width="2.5"'
                   + (f' stroke-dasharray="{dash}"' if dash else "") + '/>')
        out.append(text(x + 34, y + 5, label, 13, MUTED))
        x += 34 + 13 * len(label) + 42
    return "".join(out)


def note(x, y, w, lines, color=AMBER, fill="#fff8e8", stroke="#f0d9a8"):
    h = 18 + 20 * len(lines)
    out = [rect(x, y, w, h, fill, stroke, rx=8)]
    for i, line in enumerate(lines):
        out.append(text(x + 12, y + 24 + i * 20, line, 12.5, color))
    return "".join(out)


def svg(body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            f'<rect width="{W}" height="{H}" fill="#ffffff"/>' + body + "</svg>")


# ==========================================================================
# F01 系统上下文图
# ==========================================================================
def fig_f01():
    b = [header("F01", "系统上下文图", "谁使用它、它读写哪些外部系统；角色用颜色区分")]

    # 角色
    actors = [
        ("代码作者", "提交变更 · 处理发现 · 生成补丁", 200, BLUE),
        ("Reviewer", "复核发现 · 放行或阻断", 430, TEAL),
        ("平台管理员", "维护规则 · 查看审计", 660, AMBER),
    ]
    for name, sub, y, color in actors:
        b.append(rect(72, y, 232, 84, "#fff", color, rx=12, sw=1.6))
        b.append(rect(72, y, 6, 84, color, color, rx=3, sw=0))
        b.append(text(96, y + 34, name, 18, INK, 700))
        b.append(text(96, y + 58, sub, 12, MUTED))

    # 系统边界
    b.append(rect(392, 196, 640, 560, "#f8faff", BLUE, rx=16, sw=1.6, dash="6 4"))
    b.append(text(412, 226, "可信代码审查 Agent（本次范围）", 15, BLUE, 700))

    modules = [
        ("接入变更集", "PR / commit range · diff", 300),
        ("分析", "静态规则 + 模型逐块审查", 380),
        ("发现与证据", "文件 · 行号 · 代码片段", 460),
        ("修复补丁", "只读预览 · 写前备份", 540),
        ("验证", "pytest / ruff / 回归", 620),
        ("结论与放行", "默认不阻断 · 命中规则才需人工", 700),
    ]
    for title, sub, y in modules:
        b.append(box(420, y, 580, 64, title, sub))

    # 外部系统
    externals = [
        ("GitHub / GitLab", "读取 PR 与 diff，写回检查项", 300),
        ("模型服务（LLM）", "逐块审查，返回结论与置信度", 420),
        ("CI 测试环境", "执行验证并回传证据", 540),
        ("审计与存储", "放行记录、规则变更、报告归档", 660),
    ]
    for title, sub, y in externals:
        b.append(box(1112, y, 424, 72, title, sub, fill="#fbfcfe", stroke=LINE))

    # 角色 → 系统
    b.append(arrow(304, 242, 416, 320, BLUE, label="提交", sw=2))
    b.append(arrow(304, 472, 416, 470, TEAL, label="复核 / 放行", sw=2))
    b.append(arrow(304, 702, 416, 720, AMBER, label="治理", lx=360, ly=696, sw=2, dash="5 4"))

    # 系统 → 外部
    b.append(arrow(1000, 330, 1112, 330, MUTED, sw=1.8, label="读写", ly=322))
    b.append(arrow(1000, 452, 1112, 452, MUTED, sw=1.8, label="调用", ly=444))
    b.append(arrow(1000, 572, 1112, 572, MUTED, sw=1.8, label="触发", ly=564))
    b.append(arrow(1000, 700, 1112, 700, MUTED, sw=1.8, label="写入", ly=692, dash="5 4"))
    b.append(arrow(1112, 358, 1000, 358, MUTED, sw=1.8, label="回传", lx=1056, ly=380, dash="5 4"))

    b.append(note(1112, 196, 424, ["管理员不读业务内容：只看元数据与放行依据，", "规则变更与放行都会写入审计。"]))
    b.append(legend([("代码作者", BLUE, None), ("Reviewer", TEAL, None),
                     ("平台管理员", AMBER, "5 4"), ("系统与外部集成", MUTED, None)]))
    return svg("".join(b))


# ==========================================================================
# D01 主流程泳道图
# ==========================================================================
def fig_d01():
    b = [header("D01", "主流程泳道图", "六阶段 × 三角色：谁在哪一步做什么，失败时回到哪")]

    lanes = [("代码作者", 250, 400, BLUE), ("系统", 400, 560, MUTED), ("Reviewer", 560, 700, TEAL)]
    for name, y1, y2, color in lanes:
        b.append(rect(64, y1, W - 128, y2 - y1, "#fcfdff", LINE, rx=0, sw=1))
        b.append(f'<line x1="64" y1="{y2}" x2="{W-64}" y2="{y2}" stroke="{LINE}" stroke-width="1"/>')
        b.append(text(84, y1 + 26, name, 14, color, 700))

    stages = ["接入", "分析", "发现", "修复", "验证", "结论"]
    xs = [320, 520, 720, 920, 1120, 1340]
    for i, (s, x) in enumerate(zip(stages, xs)):
        b.append(text(x, 214, f"0{i+1}", 13, BLUE, 700, "middle", mono=True))
        b.append(text(x, 236, s, 15, INK, 700, "middle"))

    def node(x, y, w, h, title, sub, color=INK, fill="#fff"):
        b.append(rect(x - w / 2, y, w, h, fill, color, rx=10, sw=1.4))
        b.append(text(x, y + h / 2 - 2, title, 14, INK, 600, "middle"))
        b.append(text(x, y + h / 2 + 17, sub, 11, MUTED, 400, "middle"))

    node(xs[0], 432, 160, 56, "选择变更集", "PR / commit")
    node(xs[1], 432, 160, 56, "运行分析", "静态 + 模型")
    node(xs[2], 432, 160, 56, "生成发现", "证据链")
    node(xs[2], 284, 160, 56, "逐条复核", "确认 / 忽略", TEAL)
    node(xs[3], 284, 160, 56, "生成补丁", "只读预览", TEAL)
    node(xs[3], 432, 160, 56, "应用补丁", "写前 .bak")
    node(xs[4], 432, 160, 56, "运行验证", "pytest / ruff")
    node(xs[5], 600, 180, 60, "放行 / 阻断", "命中规则才需人工", TEAL)

    b.append(arrow(xs[0] + 80, 460, xs[1] - 80, 460))
    b.append(arrow(xs[1] + 80, 460, xs[2] - 80, 460))
    b.append(arrow(xs[2], 432, xs[2], 340))
    b.append(arrow(xs[2] + 80, 312, xs[3] - 80, 312))
    b.append(arrow(xs[3], 340, xs[3], 432))
    b.append(arrow(xs[3] + 80, 460, xs[4] - 80, 460))
    b.append(arrow(xs[4] + 80, 466, xs[5] - 95, 612))
    b.append(arrow(xs[4] - 85, 500, xs[3] + 85, 500, RED, dash="5 4"))
    b.append(text((xs[3] + xs[4]) / 2, 526, "验证失败 → 回到修复", 11, RED, 500, "middle"))
    b.append(text(xs[5], 686, "阻断后回退到「修复」，需重新生成补丁并验证", 11, RED, 400, "middle"))

    b.append(note(64, 776, 660, ["默认不阻断：未命中 R1–R3 时结论自动通过；命中才进入待放行。"]))
    b.append(legend([("主链路", INK, None), ("角色协作", TEAL, None), ("失败 / 回退", RED, "5 4")]))
    return svg("".join(b))


# ==========================================================================
# D02 任务状态机
# ==========================================================================
def fig_d02():
    b = [header("D02", "任务状态机", "每个阶段只有四种状态；迁移由动作触发，失败会回到「需修改」")]

    # 四态图例卡
    states = [("未开始", "#f4f6f9", "#6b7789"), ("进行中", "#eff6ff", BLUE),
              ("已完成", "#ecf9f2", GREEN), ("需修改", "#fff4f0", RED)]
    for i, (name, fill, fg) in enumerate(states):
        x = 64 + i * 150
        b.append(rect(x, 196, 130, 40, fill, fg, rx=20, sw=1.2))
        b.append(text(x + 65, 222, name, 13, fg, 700, "middle"))

    # 阶段管道
    stages = ["接入", "分析", "发现", "修复", "验证", "结论"]
    xs = [170, 420, 670, 920, 1170, 1420]
    for i, (s, x) in enumerate(zip(stages, xs)):
        b.append(rect(x - 90, 330, 180, 74, "#fff", LINE, rx=12, sw=1.4))
        b.append(text(x, 360, s, 17, INK, 700, "middle"))
        b.append(text(x, 384, f"阶段 {i+1}", 11, MUTED, 400, "middle", mono=True))
        if i < len(stages) - 1:
            b.append(arrow(x + 90, 367, xs[i + 1] - 90, 367))

    # 阶段内状态迁移
    b.append(rect(300, 470, 1000, 190, "#fbfcfe", LINE, rx=14, sw=1.2))
    b.append(text(328, 502, "阶段内迁移", 14, INK, 700))

    n1 = (420, 540, 150, "未开始", "#f4f6f9", "#6b7789")
    n2 = (700, 540, 150, "进行中", "#eff6ff", BLUE)
    n3 = (980, 540, 150, "已完成", "#ecf9f2", GREEN)
    for x, y, w, label, fill, fg in (n1, n2, n3):
        b.append(rect(x, y, w, 52, fill, fg, rx=10, sw=1.4))
        b.append(text(x + w / 2, y + 32, label, 15, fg, 700, "middle"))

    b.append(rect(700, 620, 150, 40, "#fff4f0", RED, rx=10, sw=1.4))
    b.append(text(775, 646, "需修改", 14, RED, 700, "middle"))

    b.append(arrow(570, 566, 700, 566, INK, label="开始", ly=556))
    b.append(arrow(850, 566, 980, 566, INK, label="成功", ly=556))
    b.append(arrow(850, 592, 790, 620, RED, dash="5 4", label="失败", lx=852, ly=610))
    b.append(arrow(730, 620, 760, 592, RED, dash="5 4", label="重跑", lx=712, ly=606))

    # 任务级结果
    b.append(text(64, 726, "任务级结果", 14, INK, 700))
    results = [("进行中", "#eff6ff", BLUE, "任一阶段未完成"),
               ("已放行", "#ecf9f2", GREEN, "全部阶段完成 + 门禁通过"),
               ("已阻断", "#fff4f0", RED, "Reviewer 阻断，回退到修复")]
    for i, (name, fill, fg, sub) in enumerate(results):
        x = 64 + i * 420
        b.append(rect(x, 746, 380, 60, fill, fg, rx=10, sw=1.2))
        b.append(text(x + 16, 772, name, 15, fg, 700))
        b.append(text(x + 16, 792, sub, 11, MUTED))

    b.append(note(1112, 196, 424, ["状态由数据决定，不由按钮文案决定：", "前置未完成时，放行按钮保持禁用。"],
                  fill="#eef4ff", stroke="#c9dcff", color=BLUE))
    b.append(legend([("正常迁移", INK, None), ("失败 / 回退", RED, "5 4")]))
    return svg("".join(b))


FIGURES = {"F01": fig_f01, "D01": fig_d01, "D02": fig_d02}


def main():
    want = sys.argv[1:] or list(FIGURES)
    OUT.mkdir(parents=True, exist_ok=True)
    for key in want:
        fn = FIGURES.get(key)
        if not fn:
            print("未知图:", key)
            continue
        path = OUT / f"{key}.svg"
        path.write_text(fn(), encoding="utf-8")
        print("写出", path.relative_to(OUT.parent.parent))


if __name__ == "__main__":
    main()
