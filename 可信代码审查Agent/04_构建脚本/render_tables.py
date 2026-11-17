#!/usr/bin/env python3
"""渲染矩阵类图（F03 权限矩阵、C01 覆盖矩阵）。

PlantUML 的 salt 表格在无边框主题下不好看，这里用 PIL 直接画，
保证与 UML 图同一套浅色规范。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "02_核心图_PNG"
SRC = BASE / "03_可编辑源文件" / "PlantUML"

FONT_PATH = "/System/Library/Fonts/PingFang.ttc"
INK = (23, 32, 51)
MUTED = (90, 102, 120)
LINE = (201, 210, 224)
HEAD_BG = (246, 249, 255)
ZEBRA = (252, 253, 255)
GREEN = (20, 121, 109)
BLUE = (46, 107, 255)


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_PATH, size, index=1 if bold else 0)
    except Exception:
        return ImageFont.load_default()


def draw_table(path, num, title, subtitle, headers, rows, col_w, row_h=62, cell_pad=18):
    title_f, sub_f, head_f, cell_f = font(34, True), font(20), font(22, True), font(22)
    w = sum(col_w) + 2 * cell_pad
    h = 230 + row_h * (len(rows) + 1)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    d.text((64, 48), num, font=font(20, True), fill=BLUE)
    d.text((64, 84), title, font=title_f, fill=INK)
    d.text((64, 136), subtitle, font=sub_f, fill=MUTED)
    d.line([(64, 176), (w - 64, 176)], fill=LINE, width=2)

    x0, y0 = 64, 206
    # 表头
    d.rectangle([x0, y0, x0 + w - 128, y0 + row_h], fill=HEAD_BG)
    x = x0 + cell_pad
    for i, head in enumerate(headers):
        d.text((x, y0 + row_h / 2 - 13), head, font=head_f, fill=INK)
        x += col_w[i]
    d.line([(x0, y0 + row_h), (x0 + w - 128, y0 + row_h)], fill=LINE, width=2)

    # 数据行
    for r, row in enumerate(rows, start=1):
        y = y0 + row_h * r
        if r % 2 == 0:
            d.rectangle([x0, y, x0 + w - 128, y + row_h], fill=ZEBRA)
        x = x0 + cell_pad
        for i, cell in enumerate(row):
            color = INK if i == 0 else (GREEN if cell.startswith("√") else MUTED)
            d.text((x, y + row_h / 2 - 13), cell, font=cell_f, fill=color)
            x += col_w[i]
        d.line([(x0, y + row_h), (x0 + w - 128, y + row_h)], fill=LINE, width=1)

    # 竖线
    x = x0
    d.line([(x0, y0), (x0, y0 + row_h * (len(rows) + 1))], fill=LINE, width=2)
    for cw in col_w[:-1]:
        x += cw
        d.line([(x, y0), (x, y0 + row_h * (len(rows) + 1))], fill=LINE, width=1)
    d.line([(x0 + w - 128, y0), (x0 + w - 128, y0 + row_h * (len(rows) + 1))], fill=LINE, width=2)

    img.save(path, optimize=True)
    print("写出", path.relative_to(BASE), img.size)


def main():
    draw_table(
        OUT / "03_F03_角色与权限矩阵.png", "F03", "角色与权限矩阵", "谁能做什么、谁不能看什么",
        ["动作", "代码作者", "Reviewer", "平台管理员"],
        [
            ["提交变更 / 查看自己任务的证据", "√", "√", "—"],
            ["确认 / 忽略发现", "√ 仅自己的任务", "√ 全部任务", "—"],
            ["生成补丁 / 运行验证", "√", "—", "—"],
            ["放行 / 阻断", "—", "√", "—"],
            ["查看业务内容（代码 / 发现）", "√", "√", "—"],
            ["修改规则集", "—", "—", "√"],
            ["查看审计日志", "—", "—", "√"],
        ],
        col_w=[520, 300, 300, 260],
    )

    draw_table(
        OUT / "11_C01_三场景覆盖矩阵.png", "C01", "三场景覆盖矩阵", "Web / 后端 / 数据管道 × 六阶段",
        ["阶段", "Web 前端", "后端服务", "数据管道"],
        [
            ["01 接入", "√", "√", "√"],
            ["02 分析", "√", "√", "部分"],
            ["03 发现", "√", "√", "√"],
            ["04 修复", "√", "√", "部分"],
            ["05 验证", "部分", "√", "√"],
            ["06 结论", "√", "√", "√"],
        ],
        col_w=[320, 260, 260, 260],
        row_h=58,
    )


if __name__ == "__main__":
    main()
