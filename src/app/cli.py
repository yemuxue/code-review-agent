"""Code Review Agent -- CLI"""
from __future__ import annotations
import os, sys, io, re, asyncio, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.llm_client import AnthropicClient
from src.harness.agent import AgentHarness, ToolDefinition
from src.harness.sandbox import Sandbox
from src.tools.git_tools import clone_repo, get_diff, read_file, search_code, fetch_pr
from src.config import get_api_key, get_base_url, get_model

SYSTEM_PROMPT = """You are an expert Code Review Agent. Review GitHub Pull Requests.

## Process
1. clone_repo -- ONCE. If fails: report error, STOP.
2. get_diff -- ONCE. If empty: say "No changes", STOP.
3. read_file on each changed file
4. Find: Bugs, Security, Performance, Style issues

## CRITICAL Rules
- ALWAYS call fetch_pr(pr_number=N) after clone_repo to get the actual PR changes.
- NEVER call same tool more than TWICE. If stuck, explain and STOP.
- If diff empty after fetch_pr, PR has no changes. Report and STOP immediately.
- Max 5 findings per file. Be SPECIFIC: "line 42: variable could be None" not generalizations.

## Output Format
### Summary
- Files changed: N | Issues: N (High:N, Medium:N, Low:N)

### Findings
**1. [BUG] High -- path/file.py:42**
Description: ...
Suggestion: ..."""

TOOLS = [
    ToolDefinition(name="clone_repo", description="Clone a GitHub repo (depth=1)",
        parameters={"type":"object","properties":{"repo_url":{"type":"string"},"branch":{"type":"string"}},"required":["repo_url"]}, fn=clone_repo),
    ToolDefinition(name="get_diff", description="Get git diff vs base branch",
        parameters={"type":"object","properties":{"base_branch":{"type":"string"}},"required":[]}, fn=get_diff),
    ToolDefinition(name="read_file", description="Read file content. Supports line ranges.",
        parameters={"type":"object","properties":{"file_path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["file_path"]}, fn=read_file),
    ToolDefinition(name="search_code", description="Search regex pattern in code",
        parameters={"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string"},"file_glob":{"type":"string"}},"required":["pattern"]}, fn=search_code),
    ToolDefinition(name="fetch_pr", description="Fetch a GitHub PR ref (pull/N/head) and checkout the branch. Call after clone_repo.",
        parameters={"type":"object","properties":{"pr_number":{"type":"integer","description":"PR number"}},"required":["pr_number"]}, fn=fetch_pr),
]

def main():
    p = argparse.ArgumentParser(description="Code Review Agent")
    p.add_argument("command", choices=["review"])
    p.add_argument("pr_url", help="GitHub PR URL")
    p.add_argument("--no-stream", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--api-key", default=None)
    p.add_argument("--base-url", default=None)
    args = p.parse_args()

    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", args.pr_url)
    if not m: print(f"Invalid PR URL"); sys.exit(1)
    owner, repo, num = m.group(1), m.group(2).removesuffix(".git"), m.group(3)

    print("="*43); print("  Code Review Agent"); print("="*43)
    print(f"  PR: {owner}/{repo}#{num}")
    print(f"  Model: {args.model or get_model()}")
    print("="*43+"\n")

    query = (f"Review PR #{num}: {args.pr_url}\n"
             f"Steps:\n"
             f"1. clone_repo(repo_url='https://github.com/{owner}/{repo}')\n"
             f"2. fetch_pr(pr_number={num}) -- this gets the actual PR changes\n"
             f"3. get_diff -- now you'll see the real changes\n"
             f"4. read_file on changed files, then provide review.")

    client = AnthropicClient(api_key=args.api_key or get_api_key(),
                             base_url=args.base_url or get_base_url(),
                             model=args.model or get_model(), temperature=0.1)
    agent = AgentHarness(model=client, tools=TOOLS, system_prompt=SYSTEM_PROMPT, max_turns=10)

    # ALWAYS use sandbox to isolate git operations
    sandbox = Sandbox()
    with sandbox.isolate():
        if args.no_stream:
            print("Running (non-streaming)...\n")
            result = agent.run(query)
            print(result.encode("utf-8",errors="replace").decode("utf-8",errors="replace"))
            print(f"\n--- Stats: {agent.get_stats()} ---")
        else:
            print("Review in progress:\n")
            asyncio.run(_run_streaming(agent, query))

async def _run_streaming(agent, query):
    async for event in agent.run_streaming(query):
        if event["type"] == "text_chunk":
            print(event["text"], end="", flush=True)
        elif event["type"] == "tool_call_detected":
            print(f"\n[Tool] Calling: {event['name']}...")
        elif event["type"] == "tools_executed":
            print(f"   [OK] {event['count']} tool(s) completed")
        elif event["type"] == "finished":
            print("\n\n--- Review Complete ---")
            print(f"Stats: {agent.get_stats()}")

if __name__ == "__main__": main()
