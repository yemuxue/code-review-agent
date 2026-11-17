#!/usr/bin/env python3
"""渲染矩阵类图（F03 权限矩阵、C01 覆盖矩阵），版式对齐 5.6 的 D4 覆盖矩阵：
深蓝表头 + 色块单元格 + 圆点符号 + 底部图例与结论。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "02_核心图_PNG"

FONT_PATH = "/System/Library/Fonts/PingFang.ttc"
NAVY = (31, 42, 68)
INK = (23, 32, 51)
MUTED = (90, 102, 120)
LINE = (222, 229, 240)

GREEN_BG = (234, 245, 230)
GREEN_FG = (20, 121, 109)
YELLOW_BG = (253, 243, 208)
YELLOW_FG = (138, 109, 31)
GRAY_BG = (244, 246, 249)
GRAY_FG = (107, 119, 137)

STATUS = {
    "full": (GREEN_BG, GREEN_FG, "●"),
    "partial": (YELLOW_BG, YELLOW_FG, "○"),
    "none": (GRAY_BG, GRAY_FG, "—"),
}


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONT_PATH, size, index=1 if bold else 0)
    except Exception:
        return ImageFont.load_default()


def draw_matrix(path, num, title, subtitle, headers, head_colors, rows,
                col_w, status_of, legend, conclusion, row_h=84):
    title_f, sub_f = font(46, True), font(24)
    head_f, cell_f, note_f = font(26, True), font(22), font(22)
    table_w = sum(col_w)
    w = max(table_w + 160, 1500)
    left = (w - table_w) // 2
    h = 300 + row_h * (len(rows) + 1) + 170

    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)

    d.text((w // 2, 56), f"{num}  {title}", font=title_f, fill=NAVY, anchor="ma")
    d.text((w // 2, 128), subtitle, font=sub_f, fill=MUTED, anchor="ma")

    x0, y0 = left, 210
    d.rectangle([x0, y0, x0 + table_w, y0 + row_h], fill=head_colors[0])
    x = x0
    for i, head in enumerate(headers):
        if i:
            d.rectangle([x, y0, x + col_w[i], y0 + row_h], fill=head_colors[i] if i < len(head_colors) else NAVY)
        d.text((x + col_w[i] / 2, y0 + row_h / 2), head, font=head_f, fill="white", anchor="mm")
        x += col_w[i]

    for r, row in enumerate(rows):
        y = y0 + row_h * (r + 1)
        d.rectangle([x0, y, x0 + table_w, y + row_h], fill=(255, 255, 255))
        x = x0
        for i, cell in enumerate(row):
            cx, cy = x + col_w[i] / 2, y + row_h / 2
            if i == 0:
                d.text((x + 22, cy), cell, font=cell_f, fill=INK, anchor="lm")
            else:
                bg, fg, sym = STATUS[status_of(i, cell)]
                d.rectangle([x, y + 1, x + col_w[i], y + row_h - 1], fill=bg)
                if len(cell) > 3:
                    d.text((cx, cy), cell, font=cell_f, fill=fg, anchor="mm")
                else:
                    d.text((cx, cy), sym, font=font(30), fill=fg, anchor="mm")
            x += col_w[i]
        d.line([(x0, y + row_h), (x0 + table_w, y + row_h)], fill=LINE, width=2)

    x = x0
    d.line([(x0, y0), (x0, y0 + row_h * (len(rows) + 1))], fill="white", width=3)
    for cw in col_w[:-1]:
        x += cw
        d.line([(x, y0), (x, y0 + row_h * (len(rows) + 1))], fill=LINE, width=2)

    ly = y0 + row_h * (len(rows) + 1) + 46
    d.text((w // 2, ly), legend, font=note_f, fill=MUTED, anchor="ma")
    d.text((w // 2, ly + 40), conclusion, font=note_f, fill=INK, anchor="ma")

    img.save(path, optimize=True)
    print("写出", path.relative_to(BASE), img.size)


def main():
    def perm_status(col, cell):
        if cell.startswith("√"):
            return "full"
        if "仅" in cell:
            return "partial"
        return "none"

    draw_matrix(
        OUT / "03_F03_角色与权限矩阵.png", "F03", "角色与权限矩阵", "谁能做什么、谁不能看什么",
        ["动作", "代码作者", "Reviewer", "平台管理员"],
        [NAVY, (47, 85, 151), (20, 121, 109), (138, 92, 31)],
        [
            ["提交变更 / 查看自己任务的证据", "√", "√", "—"],
            ["确认 / 忽略发现", "√ 仅自己的任务", "√ 全部任务", "—"],
            ["生成补丁 / 运行验证", "√", "—", "—"],
            ["放行 / 阻断", "—", "√", "—"],
            ["查看业务内容（代码 / 发现）", "√", "√", "—"],
            ["修改规则集", "—", "—", "√"],
            ["查看审计日志", "—", "—", "√"],
        ],
        [560, 320, 320, 300], perm_status,
        "● 允许　○ 仅限自己的任务　— 不允许",
        "结论：放行是 Reviewer 专属；管理员只落在规则与审计。",
        row_h=78,
    )

    def cover_status(col, cell):
        if cell == "√":
            return "full"
        if cell == "部分":
            return "partial"
        return "none"

    draw_matrix(
        OUT / "11_C01_三场景覆盖矩阵.png", "C01", "三场景覆盖矩阵", "Web / 后端 / 数据管道 × 六阶段",
        ["阶段", "Web 前端", "后端服务", "数据管道"],
        [NAVY, (47, 85, 151), (20, 121, 109), (138, 92, 31)],
        [
            ["01 接入", "√", "√", "√"],
            ["02 分析", "√", "√", "部分"],
            ["03 发现", "√", "√", "√"],
            ["04 修复", "√", "√", "部分"],
            ["05 验证", "部分", "√", "√"],
            ["06 结论", "√", "√", "√"],
        ],
        [420, 300, 300, 300], cover_status,
        "● 直接覆盖　○ 部分覆盖　— 未覆盖；「部分」是当前原型未验证的边界，不是失败。",
        "结论：接入、发现、结论三阶段三场景全覆盖；分析与修复对数据管道、验证对前端只覆盖一部分。",
        row_h=76,
    )


if __name__ == "__main__":
    main()
