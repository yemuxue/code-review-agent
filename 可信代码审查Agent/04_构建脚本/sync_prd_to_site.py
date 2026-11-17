#!/usr/bin/env python3
"""把仓库里的 PRD.md 同步成站点的 Markdown 页面。

单一来源：01_正式文档/可信代码审查Agent-PRD.md
目标：<site>/src/pages/docs/code-review-agent/prd.md

用法：
    python3 sync_prd_to_site.py [站点仓库路径]
默认站点路径取自 SITE_REPO 环境变量，其次 ../site（与本仓库同级）。
"""
import os
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "01_正式文档" / "可信代码审查Agent-PRD.md"
arg = sys.argv[1] if len(sys.argv) > 1 else None
SITE = Path(arg or os.environ.get("SITE_REPO") or "/Users/edy/Documents/aiXcoder/L/site")
DST = SITE / "src" / "pages" / "docs" / "code-review-agent" / "prd.md"

FRONTMATTER = """---
layout: ../../../layouts/DocLayout.astro
title: 可信代码审查 Agent · 产品需求文档
eyebrow: PRD · v1.0 · 2026.11
lede: 把「AI 生成评论」做成可核验、可处理、可回滚的审查单元。这份文档说明它解决什么问题、边界在哪、以及每个判断的依据。
meta:
  - { k: 范围, v: 'MVP · 只读审查 + 审批后修复' }
  - { k: 角色, v: '代码作者 / Reviewer / 平台管理员' }
  - { k: 状态, v: '原型已验证，待试点' }
---

"""


def main():
    if not SRC.exists():
        sys.exit(f"缺少源文件：{SRC}")
    text = SRC.read_text(encoding="utf-8")

    # 去掉文件开头的 H1、导语与元信息列表（站点由 frontmatter 渲染）
    body = re.sub(r"\A# .*?\n", "", text, count=1)
    body = re.sub(r"\A(?:>.*\n|\s*\n)+", "", body)
    body = re.sub(r"\A(?:- .*\n)+", "", body)

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(FRONTMATTER + body.lstrip(), encoding="utf-8")
    print(f"同步 → {DST}")


if __name__ == "__main__":
    main()
