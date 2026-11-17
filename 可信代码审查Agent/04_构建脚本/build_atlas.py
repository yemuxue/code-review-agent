#!/usr/bin/env python3
"""渲染核心图册：PlantUML (.puml) → PNG。

依赖 5.6 项目自带的 JRE + plantuml.jar（无需本机装 Java/Graphviz）：
    vendor/jre 与 vendor/plantuml/plantuml.jar

用法：
    python3 build_atlas.py              # 渲染 FIGURES 里全部
    python3 build_atlas.py F01 D01      # 只渲染指定图

说明：本机没有 Graphviz，组件图/状态图统一加 `!pragma layout smetana`；
活动图（泳道）用 PlantUML 内置布局，不需要 dot。
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SRC = BASE / "03_可编辑源文件" / "PlantUML"
OUT = BASE / "02_核心图_PNG"

JRE = os.environ.get(
    "PLANTUML_JRE",
    "/Users/edy/Documents/aiXcoder/需求/5.6数据集管理/99_历史版本/build_5.6/vendor/jre/Contents/Home/bin/java",
)
JAR = os.environ.get(
    "PLANTUML_JAR",
    "/Users/edy/Documents/aiXcoder/需求/5.6数据集管理/99_历史版本/build_5.6/vendor/plantuml/plantuml.jar",
)

# 编号 → (puml 文件名, 输出 PNG 文件名)
FIGURES = {
    "F01": ("F01_系统上下文图.puml", "01_F01_系统上下文.png"),
    "D01": ("D01_主流程泳道图.puml", "02_D01_主流程泳道图.png"),
    "D02": ("D02_任务状态机.puml", "03_D02_任务状态机.png"),
}
DPI = "200"

# Graphviz 的 dot：优先用环境变量，其次 /tmp/gv（micromamba 安装）
GRAPHVIZ_DOT = os.environ.get("GRAPHVIZ_DOT") or ("/tmp/gv/bin/dot" if os.path.exists("/tmp/gv/bin/dot") else "")


def render(key):
    puml, png_name = FIGURES[key]
    src = SRC / puml
    if not src.exists():
        print("缺少源文件:", src)
        return False
    cmd = [JRE, "-jar", JAR, "-tpng", f"-Sdpi={DPI}", str(src)]
    env = dict(os.environ)
    if GRAPHVIZ_DOT:
        env["GRAPHVIZ_DOT"] = GRAPHVIZ_DOT
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        print(f"[{key}] 渲染失败:\n{res.stderr[:400]}")
        return False
    produced = src.with_suffix(".png")
    if not produced.exists():
        print(f"[{key}] 未生成 PNG")
        return False
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / png_name
    target.write_bytes(produced.read_bytes())
    print(f"✓ {key} → {target.relative_to(BASE)}")
    return True


def main():
    want = sys.argv[1:] or list(FIGURES)
    ok = sum(render(k) for k in want if k in FIGURES)
    print(f"\n完成 {ok}/{len(want)}")


if __name__ == "__main__":
    main()
