"""FINDING 行解析 —— 各入口唯一的实现。

机器可读格式（提示词约定，见 multi_agent/agents.py）：
    FINDING|file_path|line_num|CATEGORY|severity|EN: description|CN: 描述|suggested fix

为什么单独抽一个模块：API 与 LangGraph 编排原本各写了一份解析，
其中一份按固定下标取值，遇到描述文本里出现字面量 `|` 就会整体错位
（CN 描述取到 fix 片段、fix 建议被截断丢弃）。解析规则必须只有一处，
否则两边会持续漂移。

解析优先级（从最可靠到最宽松）：
    ① 正好 8 段 → 按位置取值（提示词的标准形态，最精确）
    ② >8 段且能找到 `CN:` 标记 → 以标记为锚：标记前全部并入英文描述，
       标记后最后一段是 fix 建议，其余并入中文描述
    ③ 找不到锚点 → 从右侧兜底（最后两段为 CN 描述与 fix 建议）
"""
from __future__ import annotations

# FINDING 行的结构化字段数量：FINDING / file / line / category / severity
_STRUCTURED_FIELDS = 5

_CN_MARKER = "cn:"
_EN_PREFIXES = ("EN:", "en:")
_CN_PREFIXES = ("CN:", "cn:")


def _strip_language_prefix(value: str, prefixes: tuple[str, ...]) -> str:
    """剥离 `EN:` / `CN:` 前缀，保留其余内容。"""
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _split_tail(rest: list[str]) -> tuple[str, str, str]:
    """把结构化字段之后的自由文本切成 (英文描述, 中文描述, 修复建议)。

    第 ② 步是关键：`CN:` 是提示词约定的字段标记，用它做锚点可以同时
    容忍"英文描述含 `|`"和"修复建议含 `|`"——而单纯从右侧切分会把
    fix 片段误当成 CN 描述。
    """
    if len(rest) == 1:
        return rest[0].strip(), "", ""
    if len(rest) == 2:
        return rest[0].strip(), rest[1].strip(), ""

    # ① 提示词的标准形态：FINDING|file|line|CAT|sev|EN:..|CN:..|fix
    #    正好 8 段时按位置取值最精确（EN/CN 均已剥离前缀，fix 原样保留）。
    cn_index = len(rest) - 2 if len(rest) == 3 else -1
    if cn_index >= 0 and rest[cn_index].lstrip().lower().startswith(_CN_MARKER):
        return rest[0].strip(), rest[cn_index].strip(), rest[cn_index + 1].strip()

    # ② 以 `CN:` 标记为锚（取最后一个）。用最后一个：英文描述可能含多个
    #    `|`，其中某段恰以 "cn:" 开头的概率极低，取最后一个更贴近真实字段。
    anchor = -1
    for i in range(len(rest) - 1, 0, -1):
        if rest[i].lstrip().lower().startswith(_CN_MARKER):
            anchor = i
            break
    if anchor > 0:
        tail = rest[anchor:]
        description_en = "|".join(rest[:anchor]).strip()
        if len(tail) == 1:
            return description_en, tail[0].strip(), ""
        if len(tail) == 2:
            return description_en, tail[0].strip(), tail[1].strip()
        # 已知边界：锚点之后仍有 `|` 时，无法区分"CN 描述续行"与
        # "fix 建议前半段"（本格式未对自由文本做转义）。此处选择保真
        # CN 描述、fix 只保留最后一段，避免把 fix 片段混进中文描述。
        return description_en, tail[0].strip(), tail[-1].strip()

    # ③ 兜底：没有锚点（模型未遵循提示词）时按右侧两段切分
    return "|".join(rest[:-2]).strip(), rest[-2].strip(), rest[-1].strip()


def parse_finding_line(raw_line: str, index: int = 0) -> dict | None:
    """解析单行 FINDING，解析失败返回 None。"""
    line = raw_line.strip()
    if not line.startswith("FINDING|"):
        return None
    parts = line.split("|")
    if len(parts) < _STRUCTURED_FIELDS + 1:
        # 连结构化字段都不全（file/line/category/severity 后至少要有一个描述段）
        return None

    file_path = parts[1].strip()
    line_field = parts[2].strip()
    line_no = int(line_field) if line_field.isdigit() else 0
    category = parts[3].strip()
    severity = parts[4].strip()

    description_en, description_cn, suggestion = _split_tail(parts[_STRUCTURED_FIELDS:])

    return {
        "id": index + 1,
        "file_path": file_path,
        "file": file_path,
        "line": line_no,
        "line_text": line_field,
        "category": category,
        "severity": severity,
        "description_en": _strip_language_prefix(description_en, _EN_PREFIXES),
        "description_cn": _strip_language_prefix(description_cn, _CN_PREFIXES),
        "suggestion": suggestion,
    }


def parse_finding_lines(text: str) -> list[dict]:
    """解析文本中所有 FINDING 行，保持出现顺序并连续编号。"""
    findings: list[dict] = []
    for raw_line in (text or "").split("\n"):
        parsed = parse_finding_line(raw_line, index=len(findings))
        if parsed is not None:
            findings.append(parsed)
    return findings
