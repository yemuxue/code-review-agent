"""Git 工具集 — clone_repo, get_diff, read_file, search_code"""
from __future__ import annotations
import os, re, subprocess
from pathlib import Path

# 代理配置（用于 git clone / fetch 等网络操作）
PROXY_ENV = {"http_proxy": "http://127.0.0.1:7897",
             "https_proxy": "http://127.0.0.1:7897"}
GIT_ENV = {**os.environ, **PROXY_ENV}

def clone_repo(repo_url: str, branch: str = "main") -> str:
    clean_url = repo_url.rstrip("/").removesuffix(".git")
    if "github.com" not in clean_url:
        return f"Error: Only GitHub repos supported."
    try:
        result = subprocess.run(
            ["git","clone","--depth","1","--branch",branch,clean_url+".git","."],
            capture_output=True, text=True, timeout=120, env=GIT_ENV)
        if result.returncode != 0:
            return f"Clone failed (exit {result.returncode}):\nSTDERR: {result.stderr[:1000]}\nSTDOUT: {result.stdout[:500]}"
        file_count = sum(1 for _ in Path(".").rglob("*") if _.is_file())
        return f"Cloned {clean_url} (branch: {branch}), ~{file_count} files."
    except subprocess.TimeoutExpired:
        return "Clone timed out (120s)."
    except Exception as e:
        return f"Clone error: {type(e).__name__}: {e}"

def get_diff(base_branch: str = "main") -> str:
    try:
        subprocess.run(["git","fetch","origin",base_branch,"--depth=1"],
                       capture_output=True, text=True, timeout=30, env=GIT_ENV)
        r1 = subprocess.run(["git","diff",f"origin/{base_branch}..HEAD","--stat"],
                            capture_output=True, text=True, timeout=30, env=GIT_ENV)
        r2 = subprocess.run(["git","diff",f"origin/{base_branch}..HEAD"],
                            capture_output=True, text=True, timeout=30, env=GIT_ENV)
        stat, diff = r1.stdout, r2.stdout
        if not diff: return "No changes detected (diff is empty). PR may already be merged."
        max_chars = 8000
        if len(diff) > max_chars: diff = diff[:max_chars] + f"\n\n[Truncated: {len(diff)} -> {max_chars} chars]"
        return f"=== Changed files ===\n{stat}\n\n=== Full diff ===\n{diff}"
    except Exception as e:
        return f"Diff error: {type(e).__name__}: {e}"

def read_file(file_path: str = "", start_line: int = 1, end_line: int | None = None,
              offset: int | None = None) -> str:
    # offset 是 LLM 常见幻觉参数，映射到 start_line
    if offset is not None:
        start_line = offset
    if not file_path:
        return ("ERROR: You MUST provide file_path. "
                "Use read_file(file_path='/absolute/path/to/file.py', start_line=1). "
                "NOTE: use 'start_line' not 'offset'.")
    path = Path(file_path)
    if not path.exists(): return f"File not found: {file_path}. Please check the path is correct and absolute."
    if path.suffix in {".exe",".dll",".so",".bin",".zip",".gz",".png",".jpg"}: return f"Cannot read binary: {file_path}"
    if path.stat().st_size > 2*1024*1024: return f"File too large."
    try:
        with open(path,"r",encoding="utf-8",errors="replace") as f: lines = f.readlines()
        total = len(lines)
        end = min(end_line or total, total)
        start = max(1, start_line) - 1
        result = "".join(f"{i:4d}| {line}" for i, line in enumerate(lines[start:end], start=start+1))
        if end < total: result += f"\n\n[Lines {start+1}-{end} of {total}]"
        if len(result) > 5000: result = result[:5000] + f"\n\n[Truncated]"
        return result
    except Exception as e:
        return f"Read error: {e}"

def search_code(pattern: str, path: str = ".", file_glob: str = "*.py") -> str:
    root = Path(path)
    if not root.exists(): return f"Path not found."
    try: regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e: return f"Invalid regex: {e}"
    results = []
    for fp in root.rglob(file_glob):
        if fp.name.startswith(".") or "node_modules" in str(fp) or "__pycache__" in str(fp): continue
        try:
            with open(fp,"r",encoding="utf-8",errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if regex.search(line):
                        results.append(f"{fp}:{i}: {line.rstrip()}")
                        if len(results) >= 50: break
            if len(results) >= 50: break
        except: continue
    if not results: return f"No matches for: {pattern}"
    out = "\n".join(results)
    if len(results) >= 50: out += "\n\n[Truncated at 50]"
    return out

def fetch_pr(pr_number: int) -> str:
    """Fetch a GitHub PR ref and checkout the branch. Call AFTER clone_repo."""
    try:
        # Fetch the PR ref
        r = subprocess.run(
            ["git","fetch","origin",f"pull/{pr_number}/head:pr-{pr_number}"],
            capture_output=True, text=True, timeout=60, env=GIT_ENV)
        if r.returncode != 0:
            return f"Fetch PR failed (exit {r.returncode}):\n{r.stderr[:800]}"
        # Checkout the PR branch
        r2 = subprocess.run(
            ["git","checkout",f"pr-{pr_number}"],
            capture_output=True, text=True, timeout=30, env=GIT_ENV)
        if r2.returncode != 0:
            return f"Checkout PR failed (exit {r2.returncode}):\n{r2.stderr[:800]}"
        # Show what changed
        r3 = subprocess.run(
            ["git","diff","origin/main","--stat"],
            capture_output=True, text=True, timeout=30, env=GIT_ENV)
        return f"Fetched PR #{pr_number} branch. Files changed:\n{r3.stdout}"
    except Exception as e:
        return f"Fetch PR error: {type(e).__name__}: {e}"

# ─── Multi-Agent 工具 ─────────────────────────────────

def list_files(path: str = ".", pattern: str = "*") -> str:
    """List all files in a directory recursively (excludes __pycache__, .git, node_modules)."""
    import fnmatch
    root = Path(path)
    if not root.exists(): return f"Path not found: {path}"
    results = []
    for fp in root.rglob(pattern):
        if any(skip in str(fp) for skip in ["__pycache__", ".git", "node_modules", ".pytest_cache"]):
            continue
        if fp.is_file():
            results.append(str(fp))
    if not results: return f"No files matched '{pattern}' in {path}"
    out = "\n".join(sorted(results)[:100])
    if len(results) > 100: out += f"\n\n[Showing 100 of {len(results)} files]"
    return out

def grep_pattern(pattern: str, path: str = ".", file_glob: str = "*.py",
                 context_lines: int = 2, max_results: int = 30) -> str:
    """Search for a regex pattern in files, with surrounding context lines."""
    root = Path(path)
    if not root.exists(): return f"Path not found: {path}"
    try: regex = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    except re.error as e: return f"Invalid regex: {e}"
    results = []
    for fp in root.rglob(file_glob):
        if any(skip in str(fp) for skip in ["__pycache__", ".git", "node_modules"]):
            continue
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").split("\n")
            for i, line in enumerate(lines):
                if regex.search(line):
                    ctx_start = max(0, i - context_lines)
                    ctx_end = min(len(lines), i + context_lines + 1)
                    snippet = "\n".join(f"  {j+1:4d}| {l}" for j, l in enumerate(lines[ctx_start:ctx_end], start=ctx_start))
                    results.append(f"--- {fp}:{i+1} ---\n{snippet}")
                    if len(results) >= max_results: break
            if len(results) >= max_results: break
        except: continue
    if not results: return f"No matches for: {pattern}"
    return "\n".join(results)

def write_file(file_path: str = "", content: str = "", start_line: int = 1) -> str:
    """写入/修改文件。修复 Agent 专用：替换指定行范围的内容。

    ⚠️ 写前自动备份：修改前先复制一份 <file>.bak，防止修复破坏代码后无法回滚。

    Args:
        file_path: 目标文件的绝对路径
        content: 新内容（替换 start_line 起的部分）
        start_line: 从哪一行开始替换（默认 1 = 覆盖整个文件）
    """
    if not file_path:
        return ("ERROR: You MUST provide file_path. "
                "Use write_file(file_path='/absolute/path/to/file.py', content='...').")
    path = Path(file_path)
    if path.suffix in {".exe", ".dll", ".so", ".bin", ".zip", ".gz", ".png", ".jpg"}:
        return f"Cannot write binary: {file_path}"
    try:
        # 读取原文件
        if path.exists():
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                original = f.read()
        else:
            original = ""
        # ── 写前备份（只在首次修改时备份）──
        bak_path = path.with_suffix(path.suffix + ".bak")
        if path.exists() and not bak_path.exists():
            import shutil
            shutil.copy2(path, bak_path)
        # 替换：start_line 之后的内容用新 content 替换
        if path.exists() and start_line > 1:
            lines = original.split("\n")
            keep = lines[: start_line - 1]
            new_text = "\n".join(keep + content.split("\n"))
        else:
            new_text = content
        # 原子写入（先写临时文件再替换，防止写一半崩溃）
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
        tmp.replace(path)
        bak_info = f" (backup: {bak_path.name})" if bak_path.exists() else ""
        return f"OK: wrote {len(new_text)} chars to {file_path} (was {len(original)}){bak_info}"
    except OSError as e:
        return f"ERROR: write failed: {type(e).__name__}: {e}"


def run_command(command: str) -> str:
    """Run a shell command and return its output (timeout: 60s). Use for pytest, mypy, etc."""
    import shlex
    try:
        cmd = shlex.split(command) if isinstance(command, str) else command
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           env=GIT_ENV if "git" in cmd[0] else None)
        out = f"[EXIT:{r.returncode}]\n"
        if r.stdout: out += f"STDOUT:\n{r.stdout[:3000]}"
        if r.stderr: out += f"STDERR:\n{r.stderr[:1000]}"
        return out
    except subprocess.TimeoutExpired:
        return "Command timed out (60s)."
    except Exception as e:
        return f"Command error: {type(e).__name__}: {e}"
