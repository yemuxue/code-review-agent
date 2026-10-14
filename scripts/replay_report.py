"""从已有 replay 产物重建 Markdown 报告（不重新调用 API）。

用途：真实评测跑一次成本高（124 条 ≈ 25 分钟 + token 费用），
报告格式变更时不应该要求重跑。产物 JSON 已包含全部判分结果与元数据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.eval.runner import _append_replay_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 replay 产物重建 Markdown 报告")
    parser.add_argument("artifact", help="replay 产物 JSON 路径")
    parser.add_argument("--out", required=True, help="输出 Markdown 路径")
    parser.add_argument("--target-dir", default="", help="覆盖产物里的目标项目路径")
    args = parser.parse_args(argv)

    source = Path(args.artifact)
    if not source.is_file():
        print(f"产物不存在: {source}", file=sys.stderr)
        return 1
    result = json.loads(source.read_text(encoding="utf-8"))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        # 允许重复重建：先清空，避免历史内容叠加
        out.unlink()
    meta = result.get("meta") or {}
    target = args.target_dir or str(meta.get("target_dir") or "?")
    _append_replay_report(out, result, source, target)
    print(f"[replay-report] {out}（模型 {meta.get('model')}，"
          f"P={result.get('precision')} R={result.get('recall')} "
          f"healthy={meta.get('healthy')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
