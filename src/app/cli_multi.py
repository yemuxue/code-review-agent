"""Multi-Agent Code Analysis — CLI 入口"""
from __future__ import annotations
import os
import sys
import io
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.llm_client import AnthropicClient
from src.harness.agent import ToolDefinition
from src.multi_agent.orchestrator import MultiAgentOrchestrator
from src.tools.git_tools import (list_files, read_file, grep_pattern, run_command)
from src.config import get_api_key, get_base_url, get_model

# Multi-Agent 需要的所有工具
ALL_TOOLS = [
    ToolDefinition(name="list_files", description="List all files in a directory recursively",
        parameters={"type":"object","properties":{"path":{"type":"string","description":"Directory path"},"pattern":{"type":"string","description":"File pattern, e.g. *.py"}},"required":["path"]},
        fn=list_files),
    ToolDefinition(name="read_file", description="Read file content. Supports line ranges.",
        parameters={"type":"object","properties":{"file_path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["file_path"]},
        fn=read_file),
    ToolDefinition(name="grep_pattern", description="Search regex pattern in files with surrounding context",
        parameters={"type":"object","properties":{"pattern":{"type":"string","description":"Python regex"},"path":{"type":"string"},"file_glob":{"type":"string"},"context_lines":{"type":"integer"},"max_results":{"type":"integer"}},"required":["pattern","path"]},
        fn=grep_pattern),
    ToolDefinition(name="run_command", description="Run a shell command (pytest, mypy, etc). Timeout: 60s.",
        parameters={"type":"object","properties":{"command":{"type":"string","description":"Shell command to run"}},"required":["command"]},
        fn=run_command),
]

def main():
    parser = argparse.ArgumentParser(description="Multi-Agent Code Analysis")
    parser.add_argument("command", choices=["analyze"])
    parser.add_argument("path", help="Project path to analyze")
    parser.add_argument("--task", default=None, help="Specific analysis task (default: auto)")
    parser.add_argument("--model", default=None, help="Model name")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--base-url", default=None, help="API base URL")
    args = parser.parse_args()

    project_path = os.path.abspath(args.path)
    if not os.path.isdir(project_path):
        print(f"Not a directory: {project_path}")
        sys.exit(1)

    task = args.task or (
        f"Analyze the project at '{project_path}' for bugs, security issues, "
        f"performance problems, and code quality issues. Focus on the source code, not tests."
    )

    print("=" * 50)
    print("  Multi-Agent Code Analysis")
    print("=" * 50)
    print(f"  Project: {project_path}")
    print(f"  Task: {task[:80]}...")
    print(f"  Model: {args.model or get_model()}")
    print("=" * 50)

    client = AnthropicClient(
        api_key=args.api_key or get_api_key(),
        base_url=args.base_url or get_base_url(),
        model=args.model or get_model(),
        temperature=0.1,
    )

    orchestrator = MultiAgentOrchestrator(client, ALL_TOOLS)
    result = orchestrator.run(task=task, project_path=project_path)

    print("\n" + "=" * 50)
    print("  FINAL REPORT")
    print("=" * 50 + "\n")
    print(result["final_report"])

    # 自动保存报告和日志路径
    os.makedirs("reports", exist_ok=True)
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"reports/report_{ts}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Multi-Agent Analysis Report\n\n")
        f.write(f"**Project**: `{project_path}`\n")
        f.write(f"**Time**: {ts}\n\n")
        f.write(result["final_report"])
        f.write("\n\n---\n## Stats\n")
        for agent_name, stats in result.get("stats", {}).items():
            f.write(f"- **{agent_name}**: {stats['turns_taken']} turns, {stats['tools_called']} tools\n")
    print(f"\n[Report saved: {report_path}]")

    print("\n" + "=" * 50)
    print("  STATS")
    print("=" * 50)
    for agent_name, stats in result.get("stats", {}).items():
        print(f"  {agent_name}: {stats['turns_taken']} turns, "
              f"{stats['tools_called']} tools, "
              f"{stats['messages_count']} messages")

if __name__ == "__main__":
    main()
