#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「可信代码审查 Agent」需求的原型占位线框图 (SVG)。

这些线框图是**占位素材**：本需求尚无界面设计稿，PRD「详细方案」的原型列
按规范需要可渲染的 img/iframe。素材补齐后请用真实截图替换同名文件
（或直接把 PRD 里的 <img> src 指向 ../原型截图/*.png）。

用法：
    py -3 scripts/gen_prd_wireframes.py
"""
from __future__ import annotations

import html
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "可信代码审查Agent" / "原型截图"

W, H = 1672, 941
# 版面常量
PAD = 48
TITLE_H = 74
BAR_H = 46
ROW_H = 46
FONT = "Microsoft YaHei, 微软雅黑, PingFang SC, Noto Sans CJK SC, sans-serif"
MONO = "Consolas, Menlo, monospace"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def svg_document(screen_id: str, title: str, rows: list[tuple[str, str]], note: str) -> str:
    """rows: [(左侧标签, 右侧说明)]；note: 顶部横幅提示。"""
    body: list[str] = []
    y = TITLE_H + 18

    # 顶部横幅（只读声明 / 状态）
    body.append(
        f'<rect x="{PAD}" y="{y}" width="{W - 2 * PAD}" height="{BAR_H}" rx="8" '
        f'fill="#eef2ff" stroke="#c7d2fe"/>'
    )
    body.append(
        f'<text x="{PAD + 18}" y="{y + 30}" font-family="{FONT}" font-size="19" '
        f'fill="#3730a3">{esc(note)}</text>'
    )
    y += BAR_H + 26

    # 内容行：每条一个浅底行，左标签 + 右说明
    for i, (label, desc) in enumerate(rows):
        fill = "#ffffff" if i % 2 == 0 else "#f8fafc"
        body.append(
            f'<rect x="{PAD}" y="{y}" width="{W - 2 * PAD}" height="{ROW_H}" '
            f'fill="{fill}" stroke="#e2e8f0"/>'
        )
        body.append(
            f'<text x="{PAD + 18}" y="{y + 30}" font-family="{FONT}" font-size="19" '
            f'fill="#0f172a">{esc(label)}</text>'
        )
        body.append(
            f'<text x="{PAD + 470}" y="{y + 30}" font-family="{MONO}" font-size="18" '
            f'fill="#475569">{esc(desc)}</text>'
        )
        y += ROW_H

    y += 22
    body.append(
        f'<text x="{PAD}" y="{y}" font-family="{FONT}" font-size="16" fill="#94a3b8">'
        f'占位线框图 · 非最终视觉设计 · 素材补齐后替换 ../原型截图/{esc(screen_id)}.png</text>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">\n'
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>\n'
        f'<rect x="0" y="0" width="{W}" height="{TITLE_H}" fill="#f1f5f9"/>\n'
        f'<text x="{PAD}" y="47" font-family="{FONT}" font-size="26" font-weight="700" '
        f'fill="#1e293b">{esc(screen_id)} · {esc(title)}</text>\n'
        f'<line x1="0" y1="{TITLE_H}" x2="{W}" y2="{TITLE_H}" stroke="#cbd5e1" stroke-width="2"/>\n'
        + "\n".join(body)
        + "\n</svg>\n"
    )


SCREENS: list[tuple[str, str, str, list[tuple[str, str]]]] = [
    (
        "S1", "任务列表页", "审查任务 · 筛选仓库 / 状态 / 时间范围",
        [
            ("顶部：标题「审查任务」+ 新建审查按钮", "[ 新建审查 ]"),
            ("筛选栏", "仓库 ▾ | 状态 ▾ | 时间范围 ▾ | 仅看失败 ☐"),
            ("任务行 #1042 api-server PR#88", "已完成 · 发现 7 · 首结果 1m52s · 查看报告"),
            ("任务行 #1041 web-app PR#87", "等待审批 · 发现 3 · 查看补丁"),
            ("任务行 #1039 pay-svc PR#85", "失败(权限) · 重试 / 详情"),
            ("空态 / 加载失败", "空态引导新建审查；失败展示重试，不显示假数据"),
        ],
    ),
    (
        "S2", "接入与范围确认页", "本次为只读审查，不会修改任何文件",
        [
            ("仓库", "[ 已授权仓库下拉 ▾ ] · 未授权显示「申请权限」"),
            ("变更集", "( ) Pull Request [ PR 编号 / 列表 ▾ ]"),
            ("变更集", "( ) Commit [ commit SHA 输入框 ]"),
            ("变更集摘要", "标题 · 作者 · 目标分支 · 变更文件数 · 增删行数"),
            ("上下文范围", "读取：变更文件全文 / 所在目录 / 被引用符号所在文件"),
            ("上下文范围", "不读取：其余仓库文件与历史提交全文"),
            ("上下文范围", "[ 展开查看将读取的文件清单（N 个）]"),
            ("权限与影响提示", "只读声明 · 预计消耗 · 预计耗时区间"),
            ("操作", "[ 启动审查 ]   [ 取消 ]"),
        ],
    ),
    (
        "S3", "任务进度页", "预计还需约 1 分钟 · 已用 32 秒 · 已消耗 18.4k tokens",
        [
            ("① 权限校验", "✅ 已完成"),
            ("② 读取上下文", "✅ 已完成 · 12 个文件 / 340 行"),
            ("③ 分析变更", "⏳ 进行中 · 已发现 5 条候选"),
            ("④ 验证证据", "○ 等待中"),
            ("⑤ 生成报告", "○ 等待中"),
            ("增量结果入口", "[ 查看已确认的发现 ] 出现第一条结果即可查看"),
            ("失败态", "就地展开失败原因与「重试」，不隐藏错误类别"),
            ("取消", "[ 取消 ] 停止后续阶段并保留已产生结果"),
        ],
    ),
    (
        "S4", "发现列表页", "发现 7 条：High 1 · Medium 4 · Low 2 | 已确认 5 · 误报 1 · 不确定 1",
        [
            ("排序与筛选", "排序：风险 ▾ | verdict ☐ 严重度 ☐ 类别 ☐ 文件 ▾ | 🔍"),
            ("卡片 1", "[High] 🔴 已确认 未校验输入拼接进 SQL 查询"),
            ("卡片 1 明细", "auth/login.py:42-47 · 证据 3 项 · 采纳 忽略 误报 · 详情 →"),
            ("卡片 2", "[Medium] 🟡 不确定 循环内重复查询导致 N+1"),
            ("卡片 2 明细", "order/service.py:118 · 证据 1 项 · 采纳 忽略 误报 · 详情 →"),
            ("卡片 3", "[Low] ⚪ 已确认为误报 变量命名不符合项目规范"),
            ("卡片 3 明细", "utils/fmt.py:9 · 证据 2 项 · 撤销标记 · 详情 →"),
            ("空态 / 筛选空", "说明覆盖与未覆盖范围；筛选空提示清除筛选"),
        ],
    ),
    (
        "S5", "发现详情 / 证据链页", "[High] 🔴 已确认 · 证据 3 项 · 严重度由规则校准",
        [
            ("问题描述", "auth/login.py:42-47 用户输入未校验即拼接进 SQL 查询"),
            ("影响", "攻击者可构造输入读取任意表数据"),
            ("证据 1", "代码片段：login.py:42-47（最小上下文 6 行）"),
            ("证据 2", "调用关系：views/auth.py:12 传入 request.args['user']"),
            ("证据 3", "验证动作：检索既有防护写法 / 确认无参数化"),
            ("验证结论", "CONFIRMED（证据 3 项）"),
            ("建议", "改用参数化查询；最小可行修复，不改变函数签名"),
            ("处理", "[ 采纳并生成补丁 ] [ 忽略（需填原因）] [ 标记误报（需填原因）]"),
        ],
    ),
    (
        "S6", "补丁预览与审批页", "变更文件 1 个 · +4 / −2 行 · 基于 commit a1b2c3d",
        [
            ("目标文件", "auth/login.py"),
            ("影响面", "仅本文件内函数 login() 的查询构造"),
            ("diff 删除行", '- query = "SELECT * FROM users WHERE name=\'" + user + "\'"'),
            ("diff 新增行", '+ query = "SELECT * FROM users WHERE name = ?"'),
            ("diff 新增行", "+ cursor.execute(query, (user,))"),
            ("确认项", "☑ 我已确认该改动符合本仓库约定"),
            ("操作", "[ 批准并要求写入 ]   [ 拒绝 ]   [ 复制 diff ]"),
            ("边界", "未勾选不可写入；文件已被他人修改则阻止并提示冲突"),
        ],
    ),
    (
        "S7", "Verify 结果页（通过 / 失败 / 未配置）", "三种结果状态必居其一，绝不把失败伪装为成功",
        [
            ("通过态", "✅ 验证通过 · 已写入 auth/login.py"),
            ("通过态明细", "执行命令：语法检查（允许列表）· 耗时 3.2s"),
            ("失败态", "❌ 验证失败 · 未应用改动，原文件已回滚"),
            ("失败态明细", "pytest tests/test_auth.py · 退出码 1 · 2 failed, 5 passed"),
            ("失败态操作", "[ 查看完整输出 ] [ 重新生成补丁 ] [ 手动处理 ]"),
            ("未配置态", "⚠️ 未配置验证 · 改动已写入，但未执行任何校验"),
            ("未配置态操作", "[ 配置验证命令 ] [ 仍标记为已验证（需填原因）]"),
            ("边界", "超时 / 回滚失败 / 重复批准均需明确反馈与幂等处理"),
        ],
    ),
    (
        "S8", "审查报告页", "任务 #1042 · api-server · PR#88 · 已完成",
        [
            ("报告摘要", "发现 7 条 · High 1 / Medium 4 / Low 2 · 已确认 5"),
            ("覆盖范围", "已读取 12 个文件 / 340 行；未覆盖其余仓库文件"),
            ("发现清单", "按风险排序，可跳转 diff 对应行"),
            ("反馈汇总", "采纳 4 · 忽略 2 · 误报 1"),
            ("修复汇总", "已应用 2 · 已验证 2 · 回滚 0"),
            ("操作", "[ 导出 ] [ 分享 ] [ 回写 PR 评论 ]"),
            ("边界", "无发现时说明覆盖范围，不用「代码很干净」这类过度承诺"),
        ],
    ),
    (
        "S9", "指标与成本面板", "时间范围：近 7 天 ▾ · 维度：任务数 ▾ · 所有比率标注样本量",
        [
            ("任务量", "任务 42 · 完成 39 · 失败 2 · 取消 1"),
            ("体验", "首结果 P50 1m10s · P95 4m20s · 缓存命中 12 / 42"),
            ("用户价值", "有帮助率 63% (N=31) · 误报率 13% · 采纳率 48%"),
            ("修复安全", "Verify 通过 9 / 12 · 回归引入 0 · 回滚 1"),
            ("成本", "总 18.7 美元 · 每 PR 0.45 美元 · Token 1.24M"),
            ("质量（离线口径）", "Precision 95.3% · Recall 38.0% · 非线上效果"),
            ("边界", "无数据展示空态；样本不足标注不可用于决策；成本缺失不填估算值"),
        ],
    ),
    (
        "S10", "PR 内评论与检查项", "✅ 可信代码审查 · 已完成 · 本次为只读审查，未修改任何文件",
        [
            ("检查项摘要", "发现 7 条（High 1 / Medium 4 / Low 2）· 查看完整报告 →"),
            ("评论（行内）", "[High] 未校验的用户输入直接拼接进 SQL 查询"),
            ("评论证据", "证据：第 42-47 行 + 调用方第 12 行 · 验证：已确认"),
            ("评论建议", "建议：改用参数化查询"),
            ("评论操作", "[ 打开证据链 ] [ 采纳 ] [ 忽略 ] [ 标记误报 ]"),
            ("边界", "未完成不产生部分评论；同一行多条合并；新 commit 不静默覆盖"),
        ],
    ),
]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for screen_id, title, note, rows in SCREENS:
        path = OUT_DIR / f"wireframe-{screen_id}.svg"
        path.write_text(svg_document(screen_id, title, rows, note), encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    print(f"\n共 {len(SCREENS)} 张占位线框图。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
