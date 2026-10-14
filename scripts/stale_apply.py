"""把 STALE 处置写入评测集（锚定元组整块替换）。

为什么不用"重新生成整个 EVAL_SAMPLES 列表"：该文件里有按源文件分区的注释
（如 `# ═══ react_agent.py (12 samples) ═══`），重新生成会丢失这些文档。
因此按 (file, line) 定位元组、整块替换，并**断言每次恰好匹配 1 处**。

处置规则（见 reports/_stale_originals.json 的 action 字段）：
    RELOCATE         保持 is_bug=True，只改行号（缺陷仍在，只是漂移了）
    RELABEL_NEGATIVE is_bug=False，描述加【已修复】前缀
                     —— 该位置代码现在正确，模型报出即为误报

⚠️ 本脚本不发明缺陷：RELABEL_NEGATIVE 只翻转标签，不改写成"新缺陷"。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DATASET = Path("tests/eval_llm_agent_qa.py")
ORIGINALS = Path("reports/_stale_originals.json")

FIXED_PREFIX_CN = "【已修复】"
FIXED_SUFFIX_CN = " —— 当前代码正确，报出即为误报"
FIXED_PREFIX_EN = "[FIXED] "
FIXED_SUFFIX_EN = " — current code is correct; reporting it is a false positive"


def _tuple_pattern(file_name: str, line: int) -> re.Pattern:
    """匹配以 (\"file\", line, ... 开头、以 `),` 结尾的整个元组。

    元组内部是单行字符串，不会出现 `),\\n`，因此非贪婪匹配到第一个 `),\\n`
    就是该元组的结尾。
    """
    return re.compile(
        r'\(\s*"' + re.escape(file_name) + r'",\s*' + str(line) + r',.*?\),\n',
        re.DOTALL)


def _render(file_name: str, line: int, category: str, severity: str,
            desc_en: str, desc_cn: str, is_bug: bool) -> str:
    return (f'    ("{file_name}", {line}, "{category}", "{severity}",\n'
            f'     "{desc_en}",\n'
            f'     "{desc_cn}", {is_bug}),\n')


def main(argv: list[str] | None = None) -> int:
    changes = json.loads(ORIGINALS.read_text(encoding="utf-8"))
    text = DATASET.read_text(encoding="utf-8")
    applied = 0

    for change in changes:
        pattern = _tuple_pattern(change["file"], change["line"])
        matches = pattern.findall(text)
        if len(matches) != 1:
            print(f"  ✗ {change['file']}:{change['line']} 匹配 {len(matches)} 处，跳过",
                  file=sys.stderr)
            return 1

        if change["action"] == "RELOCATE":
            new_line = change["new_line"]
            desc_en, desc_cn, is_bug = change["desc_en"], change["desc_cn"], True
        else:
            new_line = change["line"]
            desc_en = f"{FIXED_PREFIX_EN}{change['desc_en']}{FIXED_SUFFIX_EN}"
            desc_cn = f"{FIXED_PREFIX_CN}{change['desc_cn']}{FIXED_SUFFIX_CN}"
            is_bug = False
            # RELABEL_NEGATIVE 断言原标签是 True：把正例翻成负例才是本意，
            # 若原本就是 False 说明处置对象有误。
            assert change["is_bug"] is True, f"id{change['stale_id']} 原标签不是正例"

        replacement = _render(change["file"], new_line, change["category"],
                              change["severity"], desc_en, desc_cn, is_bug)
        # 必须用 lambda 而不是字符串替换：某些标注描述本身含 `\1`
        # （例如"反向引用在字符类中失效"），re.sub 会把它当成组引用而报错。
        text = pattern.sub(lambda _match: replacement, text, count=1)
        applied += 1

    DATASET.write_text(text, encoding="utf-8", newline="\n")
    print(f"[stale-apply] 已应用 {applied}/{len(changes)} 条处置 -> {DATASET}")
    print(f"  RELOCATE: {sum(1 for c in changes if c['action'] == 'RELOCATE')} 条")
    print(f"  RELABEL_NEGATIVE: {sum(1 for c in changes if c['action'] != 'RELOCATE')} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
