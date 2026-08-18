"""
全流程测试 / Full Pipeline Test

用 Multi-Agent 管线（LangGraph: plan → execute(并行) → review → fix）
分析 bug_injection_sample.py，验证各类型 bug 的检测能力。

运行:
    python scripts/run_full_pipeline_test.py
"""
import sys, json, io
from pathlib import Path

PROJ = Path(__file__).parent.parent
sys.path.insert(0, str(PROJ))
# Windows 控制台默认 GBK，无法输出 emoji/中文，强制 UTF-8（与 cli.py 相同处理）
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.llm_client import AnthropicClient
from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator
from src.tools.git_tools import read_file, list_files, grep_pattern, write_file
from src.harness.agent import ToolDefinition
from src.harness.auth import HumanInTheLoop
from src.harness.sandbox import Sandbox
from src.harness.memory import ContextMemory

# 取第一个非 "--" 参数作为 TARGET（--safe 等旗标不应被当作目标路径）
TARGET = next(
    (a for a in sys.argv[1:] if not a.startswith("--")),
    str(PROJ / "tests" / "bug_injection_sample.py"),
)

# --safe: 复制目标到临时目录再分析，防止 fix 修改原文件
if "--safe" in sys.argv:
    import shutil, tempfile
    _src = Path(TARGET)
    _tmp_dir = Path(tempfile.mkdtemp(prefix="pipeline_safe_"))
    _copy = _tmp_dir / _src.name
    shutil.copy2(_src, _copy)
    print(f"[safe] 已复制 {_src.name} 到 {_copy}（原文件不会被修改）")
    TARGET = str(_copy)

TOOLS = [
    ToolDefinition("list_files", "List files", {"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}, list_files),
    ToolDefinition("read_file", "Read file", {"type":"object","properties":{"file_path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["file_path"]}, read_file),
    ToolDefinition("grep_pattern", "Search regex", {"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string"}},"required":["pattern","path"]}, grep_pattern),
    ToolDefinition("write_file", "Write file", {"type":"object","properties":{"file_path":{"type":"string"},"content":{"type":"string"},"start_line":{"type":"integer"}},"required":["file_path","content"]}, write_file),
]


def main():
    client = AnthropicClient(temperature=0.1)
    orch = LangGraphOrchestrator(
        client, TOOLS,
        sandbox=Sandbox(),
        hitl=HumanInTheLoop(auto_approve_safe=True),
        memory=ContextMemory(strategy="hybrid", window_size=10),
        auto_fix=True,  # 全流程测试脚本：验证修复能力（--safe 模式已防止污染原文件）
    )

    print("=" * 70)
    print(f"  全流程测试 / Full Pipeline Test")
    print(f"  目标: {TARGET}")
    print("=" * 70)

    result = orch.run(f"Find ALL bugs in {TARGET}", str(Path(TARGET).parent))

    print("\n" + "=" * 70)
    print("  结果 / Results")
    print("=" * 70)
    findings = result["findings"]
    verdicts = result["verdicts"]
    fixes = result["fixes"]

    print(f"\n📋 Findings (Planner 发现): {len(findings)}")
    for f in findings:
        print(f"  [{f['severity']}] {f['category']} L{f['line']} — {f['description_en'][:60]}")

    print(f"\n🔍 Verdicts (Executor 验证): {len(verdicts)}")
    vmap = {v["finding_id"]: v for v in verdicts}
    confirmed = [v for v in verdicts if v["verdict"] == "CONFIRMED"]
    fp = [v for v in verdicts if v["verdict"] == "FALSE_POSITIVE"]
    unc = [v for v in verdicts if v["verdict"] == "UNCERTAIN"]
    print(f"  CONFIRMED: {len(confirmed)} | FALSE_POSITIVE: {len(fp)} | UNCERTAIN: {len(unc)}")

    print(f"\n🔧 Fixes (Fixer 修复): {len(fixes)}")
    for fx in fixes:
        print(f"  [{fx['status']}] #{fx['finding_id']} {fx['file_path']} — {fx['summary'][:60]}")

    print(f"\n⏱️ Node Stats (节点统计):")
    for node, s in result["node_stats"].items():
        print(f"  {node}: {s.get('turns')} turns | {s.get('tools')} tools | {s.get('tokens')} tok | {s.get('elapsed_ms')}ms")

    print(f"\n✅ Complete: {result['complete']}")

    # 汇总 JSON 保存
    out = {
        "target": TARGET,
        "findings": findings,
        "verdicts": verdicts,
        "fixes": fixes,
        "node_stats": result["node_stats"],
    }
    Path("data").mkdir(exist_ok=True)
    with open("data/pipeline_test_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n结果已保存: data/pipeline_test_result.json")


if __name__ == "__main__":
    main()
