"""复刻前端 Multi-Agent 完整运行（与 streamlit_app.py:475-484 完全相同的代码路径）

验证管线加固：write_file 截断守卫 + verify 完整性检查 + 自动回滚
"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm_client import AnthropicClient
from src.harness.agent import ToolDefinition
from src.harness.auth import HumanInTheLoop
from src.harness.sandbox import Sandbox
from src.harness.memory import ContextMemory
from src.multi_agent.langgraph_orchestrator import LangGraphOrchestrator as Orchestrator
from src.tools.git_tools import list_files, read_file, grep_pattern, run_command, write_file

# ═══ 与前端 TOOLS 完全一致 ═══
TOOLS = [
    ToolDefinition("list_files", "List files recursively / 递归列出文件",
        {"type": "object", "properties": {"path": {"type": "string", "description": "Directory path / 目录路径"}, "pattern": {"type": "string"}}, "required": ["path"]}, list_files),
    ToolDefinition("read_file", "Read file content / 读取文件. Use file_path (absolute), start_line, end_line. Do NOT use 'offset'.",
        {"type": "object", "properties": {"file_path": {"type": "string", "description": "Absolute path"}, "start_line": {"type": "integer", "description": "Starting line number (1-indexed)"}, "end_line": {"type": "integer", "description": "Ending line number"}}, "required": ["file_path"]}, read_file),
    ToolDefinition("grep_pattern", "Search regex pattern / 正则搜索代码",
        {"type": "object", "properties": {"pattern": {"type": "string", "description": "Regex"}, "path": {"type": "string"}, "file_glob": {"type": "string"}}, "required": ["pattern", "path"]}, grep_pattern),
    ToolDefinition("run_command", "Run shell command / 运行命令",
        {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}, run_command),
    ToolDefinition("write_file", "Write/repair file / 写入或修复文件（写前自动备份 .bak）",
        {"type": "object", "properties": {"file_path": {"type": "string", "description": "Absolute path"}, "content": {"type": "string", "description": "New content"}, "start_line": {"type": "integer", "description": "Replace from this line (default 1 = whole file)"}}, "required": ["file_path", "content"]}, write_file),
]

# ═══ 与上次前端运行相同的命令：4 个文件 + CRITICAL 约束 ═══
prompt = "分析这些文件的代码质量，找出所有 BUG、安全、性能和风格问题"
files = ["src/harness/jwt_auth.py", "src/api/server.py", "src/config.py", "src/llm_client.py"]
extra = ("\n\nCRITICAL: ONLY analyze these specific files, NOT the whole project:\n"
         + "\n".join(f"- {f}" for f in files)
         + "\nDo NOT read or list other files. Focus exclusively on the files listed above.")
target = prompt + extra

client = AnthropicClient()
hitl_guard = HumanInTheLoop(auto_approve_safe=True)
orch = Orchestrator(client, TOOLS, sandbox=Sandbox(), hitl=hitl_guard,
                    memory=ContextMemory(strategy="hybrid", window_size=10))

print(f"[RUN] {time.strftime('%Y%m%d_%H%M%S')}")
print(f"[TASK] {target}\n")
t0 = time.time()
lang_result = orch.run(task=target, project_path="X:/VScode/code-review-agent/src")
print(f"[DONE] {time.time() - t0:.0f}s")

findings = lang_result.get("findings", [])
fixes = lang_result.get("fixes", [])
print(f"\n[SUMMARY] Findings {len(findings)} | Fixes {len(fixes)}")
n_fixed = sum(1 for f in fixes if f.get("status") == "FIXED")
n_failed = sum(1 for f in fixes if f.get("status") == "FAILED")
n_rolled = sum(1 for f in fixes if f.get("status") == "ROLLED_BACK")
print(f"[SUMMARY] Fixed {n_fixed} | Failed {n_failed} | Rolled back {n_rolled}")

print("\n=== FINDINGS ===")
for f in findings:
    print(f"  #{f['id']} [{f['category']}] {f['severity']} -- {f['file']}:{f['line']} | {f['description_en'][:70]}")

print("\n=== FIXES ===")
for f in fixes:
    print(f"  {f['status']} | {f.get('file_path', '')} | {f.get('summary', '')[:80]}")

print("\n=== VERIFY / FIXER 报告消息 ===")
for m in lang_result.get("messages", []):
    content = m if isinstance(m, str) else str(m)
    if "Fix Verification" in content or "Fix Results" in content:
        print("\n---\n" + content[:1500])
