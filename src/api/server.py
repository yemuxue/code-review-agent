"""
FastAPI REST Server — 生产级 API

运行:
    uvicorn src.api.server:app --host 0.0.0.0 --port 8000 --reload

端点:
    POST /analyze         — 代码分析（单/多 Agent）
    GET  /sessions        — 会话列表
    GET  /sessions/{id}   — 会话详情
    GET  /findings        — 发现列表（支持筛选）
    POST /findings/search — 向量语义搜索
    GET  /stats           — 系统统计
    GET  /health          — 健康检查
"""

from __future__ import annotations
import sys, os, json, uuid, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from src.llm_client import AnthropicClient
from src.harness.agent import AgentHarness, ToolDefinition
from src.harness.telemetry import AgentLogger
from src.multi_agent.orchestrator import MultiAgentOrchestrator
from src.tools.git_tools import list_files, read_file, grep_pattern, run_command
from src.storage.database import Database, Finding
from src.memory.vector_store import VectorStore, FindingDocument

# ─── Init ───────────────────────────────────────

app = FastAPI(
    title="Code Review Agent API",
    description="Multi-Agent Code Analysis System / 多 Agent 代码分析系统",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)

# Rate limiting
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# API key auth
import secrets
API_KEYS = {os.getenv("API_KEY", "dev-key-change-me"): "admin"}
security = HTTPBearer(auto_error=False)

def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials and credentials.credentials in API_KEYS:
        return credentials.credentials
    # Allow localhost without auth
    return "localhost"

db = Database(str(Path(__file__).parent.parent.parent / "data" / "code_review.db"))
vector_store = VectorStore(str(Path(__file__).parent.parent.parent / "data" / "search.db"))

TOOLS = [
    ToolDefinition("list_files","List files recursively",{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}, list_files),
    ToolDefinition("read_file","Read file content",{"type":"object","properties":{"file_path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["file_path"]}, read_file),
    ToolDefinition("grep_pattern","Search regex in code",{"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string"},"file_glob":{"type":"string"}},"required":["pattern","path"]}, grep_pattern),
    ToolDefinition("run_command","Run shell command",{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}, run_command),
]

# ─── Models ─────────────────────────────────────

class AnalyzeRequest(BaseModel):
    query: str = Field(..., description="分析任务描述 / Analysis task description")
    project_path: str = Field("X:/VScode/code-review-agent/src", description="项目路径")
    mode: str = Field("single", description="single 或 multi")
    max_turns: int = Field(8, ge=1, le=30)
    file_paths: list[str] = Field(default_factory=list, description="要分析的文件路径")

class AnalyzeResponse(BaseModel):
    session_id: str
    mode: str
    result: str
    stats: dict
    log_path: str

class FindingResponse(BaseModel):
    id: str
    file_path: str
    line: int
    category: str
    severity: str
    description_en: str
    description_cn: str
    suggestion: str
    verdict: str

class SearchRequest(BaseModel):
    query: str
    n_results: int = 5
    category: Optional[str] = None
    severity: Optional[str] = None

# ─── Dashboard ──────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """实时监控仪表盘 / Live Dashboard"""
    import urllib.request
    try:
        metrics_text = urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=2).read().decode()
    except Exception:
        metrics_text = ""

    total_requests = 0
    error_count = 0
    for line in metrics_text.split("\n"):
        if "http_requests_total" in line and not line.startswith("#"):
            total_requests += float(line.split()[-1])
        elif "http_requests_created" not in line and "http_requests" in line and "500" in line:
            error_count += float(line.split()[-1]) if line.split()[-1].isdigit() else 0

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="10">
    <title>Code Review Agent - Dashboard</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 2rem; }}
        h1 {{ color: #e6edf3; margin-bottom: 1.5rem; font-size: 1.5rem; }}
        h1 span {{ color: #58a6ff; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.2rem; text-align: center; }}
        .card .value {{ font-size: 2rem; font-weight: 700; color: #58a6ff; }}
        .card .label {{ font-size: 0.75rem; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 0.25rem; }}
        .status-ok {{ color: #3fb950; }} .status-warn {{ color: #d29922; }} .status-err {{ color: #f85149; }}
        pre {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; font-size: 0.7rem; max-height: 400px; overflow-y: auto; white-space: pre-wrap; color: #8b949e; font-family: 'Cascadia Code', monospace; }}
        .bar {{ height: 4px; background: #21262d; border-radius: 2px; margin-top: 0.5rem; overflow: hidden; }}
        .bar-fill {{ height: 100%; background: #58a6ff; border-radius: 2px; transition: width 0.5s; }}
    </style>
</head>
<body>
    <h1>⚙️ Code Review Agent <span>Dashboard</span></h1>

    <div class="grid">
        <div class="card">
            <div class="value">{total_requests:.0f}</div>
            <div class="label">Total Requests / 总请求</div>
            <div class="bar"><div class="bar-fill" style="width:{min(total_requests/10*100,100)}%"></div></div>
        </div>
        <div class="card">
            <div class="value">{error_count:.0f}</div>
            <div class="label">Errors / 错误</div>
            <div class="bar"><div class="bar-fill" style="width:{min(error_count*20,100)}%;background:#f85149"></div></div>
        </div>
        <div class="card">
            <div class="value status-ok">● UP</div>
            <div class="label">FastAPI :8000</div>
        </div>
        <div class="card">
            <div class="value status-ok">● UP</div>
            <div class="label">Streamlit :8501</div>
        </div>
    </div>

    <h3 style="color:#e6edf3;margin-bottom:0.5rem;">📊 Raw Metrics / 原始指标</h3>
    <pre>{metrics_text[:5000]}</pre>
    <p style="color:#484f58;font-size:0.7rem;margin-top:0.5rem;">Auto-refresh every 10s / 每10秒自动刷新</p>
</body>
</html>"""


# ─── Routes ─────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "2.0.0",
        "vector_store_count": vector_store.count(),
        "stats": db.get_stats(),
    }

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    """运行代码分析"""
    logger = AgentLogger("./logs")
    client = AnthropicClient(temperature=0.1)
    sid = db.create_session(name=req.query[:60], mode=req.mode, project_path=req.project_path)

    # 构建 query
    target = req.query
    if req.file_paths:
        target += "\n\nFiles:\n" + "\n".join(f"- {p}" for p in req.file_paths)

    is_multi = req.mode == "multi"

    if is_multi:
        orch = MultiAgentOrchestrator(client, TOOLS, logger=logger)
        result = orch.run(task=target, project_path=req.project_path)
        result_text = result["final_report"]
        ms = result.get("stats", {})
        stats = {
            "planner": ms.get("planner", {}),
            "executor": ms.get("executor", {}),
            "reviewer": ms.get("reviewer", {}),
        }
    else:
        SYSTEM_PROMPT = "You are a code analysis agent. Find bugs, security, performance, style issues."
        agent = AgentHarness(model=client, tools=TOOLS, system_prompt=SYSTEM_PROMPT,
                             max_turns=req.max_turns, logger=logger)
        result_text = agent.run(target)
        stats = agent.get_stats()

    # 保存消息
    db.add_message(sid, "user", req.query)
    db.add_message(sid, "assistant", result_text, stats)

    # 解析发现并存入向量库
    _index_findings(sid, result_text)

    return AnalyzeResponse(
        session_id=sid, mode=req.mode, result=result_text,
        stats=stats, log_path=logger.log_path,
    )

@app.get("/sessions")
async def list_sessions(limit: int = 20, offset: int = 0):
    """会话列表，支持分页"""
    all_sessions = db.list_sessions(limit + offset)
    return all_sessions[offset:offset + limit]


@app.get("/findings/page")
async def list_findings_paginated(
    offset: int = 0,
    limit: int = 20,
    category: Optional[str] = None,
    severity: Optional[str] = None,
):
    """发现列表，支持分页 + 筛选"""
    all_findings = db.get_findings(category=category, severity=severity, limit=limit + offset)
    return {
        "offset": offset,
        "limit": limit,
        "total": len(all_findings),
        "items": all_findings[offset:offset + limit],
    }

@app.get("/sessions/{sid}")
async def get_session(sid: str):
    session = db.get_session(sid)
    if not session:
        raise HTTPException(404, "Session not found")
    messages = db.get_messages(sid)
    findings = db.get_findings(session_id=sid)
    return {"session": session, "messages": messages, "findings": findings}

@app.get("/findings")
async def list_findings(
    session_id: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    verdict: Optional[str] = None,
    limit: int = 50,
):
    return db.get_findings(session_id, category, severity, verdict, limit)

@app.post("/findings/search")
async def search_findings(req: SearchRequest):
    """向量语义搜索 + 关键词搜索"""
    vector_results = vector_store.search(req.query, req.n_results, req.category, req.severity)
    keyword_results = db.search_findings(req.query, req.n_results)
    return {"vector": vector_results, "keyword": keyword_results}

@app.get("/findings/keyword")
async def keyword_search(q: str, limit: int = 20):
    return db.search_findings(q, limit)

@app.get("/stats")
async def system_stats():
    return {
        "database": db.get_stats(),
        "vector_store": {"document_count": vector_store.count()},
    }

# ─── Helpers ────────────────────────────────────

def _index_findings(sid: str, text: str):
    """从输出文本中解析 FINDING 行并存入向量库"""
    docs = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("FINDING|"):
            parts = line.split("|")
            if len(parts) >= 7:
                try:
                    doc = FindingDocument(
                        file_path=parts[1].strip(),
                        line=int(parts[2].strip()) if parts[2].strip().isdigit() else 0,
                        category=parts[3].strip(),
                        severity=parts[4].strip(),
                        description_en=parts[5].strip(),
                        description_cn=parts[6].strip() if len(parts) > 6 else "",
                        suggestion=parts[7].strip() if len(parts) > 7 else "",
                        session_id=sid,
                        verified=False,
                    )
                    docs.append(doc)
                    # 同时存入 SQLite
                    db.add_finding(sid, Finding(
                        id=doc.id, session_id=sid, file_path=doc.file_path,
                        line=doc.line, category=doc.category, severity=doc.severity,
                        description_en=doc.description_en, description_cn=doc.description_cn,
                        suggestion=doc.suggestion, verdict="PENDING", embedding_id=doc.id,
                    ))
                except (ValueError, IndexError):
                    continue
    if docs:
        vector_store.add_batch(docs)

# ─── Main ───────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
