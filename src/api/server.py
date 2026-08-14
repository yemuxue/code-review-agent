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
import sys
import os
import asyncio
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from jose import JWTError, ExpiredSignatureError
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

# Importing config applies documented .env values before authentication initializes.
import src.config  # noqa: F401
from src.llm_client import AnthropicClient
from src.harness.agent import AgentHarness, ToolDefinition
from src.harness.telemetry import AgentLogger
from src.harness.jwt_auth import (
    User,
    get_auth, get_user_store, get_revocation_list,
)
from src.multi_agent.orchestrator import MultiAgentOrchestrator
from src.tools.git_tools import list_files, read_file, grep_pattern
from src.storage.database import Database, Finding
from src.memory.vector_store import VectorStore, FindingDocument
from src.security.paths import resolve_under_root

# ─── 数据目录 / 延迟初始化 ─────────────────

DATA_DIR = Path(__file__).parent.parent.parent / "data"

# db / vector_store 改为在应用启动（lifespan）时初始化，
# 避免模块导入时产生数据库/文件副作用（可测试性、可移植性）。
db: Optional[Database] = None
vector_store: Optional[VectorStore] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库与向量库，关闭时可清理资源。"""
    global db, vector_store
    db = Database(str(DATA_DIR / "code_review.db"))
    vector_store = VectorStore(str(DATA_DIR / "search.db"))
    yield


# ─── Init ───────────────────────────────────────

app = FastAPI(
    title="Code Review Agent API",
    description="Multi-Agent Code Analysis System / 多 Agent 代码分析系统",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS：仅允许可信来源（通过环境变量 CORS_ALLOWED_ORIGINS 配置），
# 不再使用通配符 "*" —— 否则任何网站都能用窃取的令牌调用接口。
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8501",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics（不自动暴露 /metrics，改为受 JWT 保护的端点）
from prometheus_fastapi_instrumentator import Instrumentator
instrumentator = Instrumentator().instrument(app)

# Rate limiting
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ─── JWT Auth 初始化 ──────────────────────────

jwt_auth = get_auth()
user_store = get_user_store()
revocation_list = get_revocation_list()
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """
    JWT 认证依赖注入 —— 三层验证：
    ① jose.jwt.decode 验证 HMAC-SHA256 签名（防篡改）
    ② 检查 exp 过期时间（自动由 python-jose 处理）
    ③ 检查 jti 是否在吊销列表中（支持服务端主动吊销）

    面试话术："每个请求到达时，FastAPI 依赖注入系统自动调用
    get_current_user → jwt.decode 验证签名+过期 → 吊销列表 O(1) 检查。
    任何一步失败都返回 401，攻击面极小。"
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = credentials.credentials

    try:
        payload = jwt_auth.verify_token(token, token_type="access")
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    # 检查吊销列表
    jti = payload.get("jti", "")
    if jti and revocation_list.is_revoked(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked")

    # 签名有效但缺少 sub 声明时返回 401，而不是 500（KeyError）
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing 'sub' claim")

    return User(
        username=sub,
        role=payload.get("role", "user"),
        email=payload.get("email", ""),
    )


# 受保护的 /metrics 端点：防止公开泄露请求量与错误率
@app.get("/metrics", include_in_schema=False)
async def metrics(current_user: User = Depends(get_current_user)):
    """Prometheus 指标（需登录）"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _metrics_text() -> str:
    """生成本地 Prometheus 指标文本（供仪表盘解析，无需内部 HTTP 调用）"""
    try:
        return generate_latest().decode("utf-8")
    except Exception:
        return ""


def _parse_metric_value(line: str) -> float:
    """解析 Prometheus 指标行末的数值（支持整数与浮点），失败返回 0.0"""
    try:
        return float(line.split()[-1])
    except (ValueError, IndexError):
        return 0.0


def _allowed_root() -> Path:
    configured = os.environ.get("CODE_REVIEW_ALLOWED_ROOT")
    fallback = Path(__file__).parent.parent.parent
    root = Path(configured) if configured else fallback
    return root.resolve(strict=True)


def build_api_tools(project_root: Path) -> list[ToolDefinition]:
    """Create read-only tools constrained to one resolved project directory."""
    root = resolve_under_root(_allowed_root(), project_root)

    def bound_list_files(path: str = "", pattern: str = "*") -> str:
        target = resolve_under_root(root, path or root)
        return list_files(str(target), pattern)

    def bound_read_file(file_path: str, start_line: int = 1,
                        end_line: int | None = None) -> str:
        target = resolve_under_root(root, file_path)
        return read_file(str(target), start_line, end_line)

    def bound_grep_pattern(pattern: str, path: str = "", file_glob: str = "*.py",
                           context_lines: int = 2, max_results: int = 30) -> str:
        target = resolve_under_root(root, path or root)
        return grep_pattern(pattern, str(target), file_glob, context_lines, max_results)

    return [
        ToolDefinition("list_files", "List files recursively under the project root",
                       {"type": "object", "properties": {"path": {"type": "string"},
                       "pattern": {"type": "string"}}}, bound_list_files),
        ToolDefinition("read_file", "Read a file under the project root",
                       {"type": "object", "properties": {"file_path": {"type": "string"},
                       "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
                       "required": ["file_path"]}, bound_read_file),
        ToolDefinition("grep_pattern", "Search code under the project root",
                       {"type": "object", "properties": {"pattern": {"type": "string"},
                       "path": {"type": "string"}, "file_glob": {"type": "string"}},
                       "required": ["pattern"]}, bound_grep_pattern),
    ]


TOOLS = build_api_tools(_allowed_root())

# ─── Models ─────────────────────────────────────


def _default_project_path() -> str:
    """默认项目路径：优先取环境变量，否则基于当前仓库位置（可移植）"""
    return os.environ.get(
        "CODE_REVIEW_PROJECT_PATH",
        str(Path(__file__).parent.parent.parent / "src"),
    )


class AnalyzeRequest(BaseModel):
    query: str = Field(..., description="分析任务描述 / Analysis task description")
    project_path: str = Field(default_factory=_default_project_path, description="项目路径")
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


# ─── Auth Models ────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64, description="用户名")
    password: str = Field(..., min_length=1, max_length=128, description="密码")

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict
    expires_in: int = 900  # 15 min in seconds

class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token from /auth/login")

# ─── Dashboard ──────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(current_user: User = Depends(get_current_user)):
    """实时监控仪表盘 / Live Dashboard（需登录）"""
    metrics_text = _metrics_text()

    total_requests = 0
    error_count = 0
    for line in metrics_text.split("\n"):
        if "http_requests_total" in line and not line.startswith("#"):
            total_requests += _parse_metric_value(line)
        elif "http_requests_created" not in line and "http_requests" in line and "500" in line:
            error_count += _parse_metric_value(line)

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


# ─── Auth Routes ────────────────────────────────

@app.post("/auth/login", response_model=TokenResponse)
@limiter.limit("10/minute")  # 登录接口严格限流：防止暴力破解
async def login(req: LoginRequest, request: Request):
    """
    用户登录 → 返回 JWT access + refresh token 对。

    面试话术："登录接口单独限流 10次/分钟——暴力破解攻击者
    最多尝试 10 个密码就被封堵 1 分钟，同时配合 bcrypt 的
    慢哈希（~100ms/次）让离线破解也不划算。"
    """
    user = user_store.verify_password(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tokens = jwt_auth.create_tokens(user)

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        user=user.to_dict(),
        expires_in=900,  # 15 min
    )


@app.post("/auth/refresh", response_model=TokenResponse)
@limiter.limit("30/minute")
async def refresh_token(req: RefreshRequest, request: Request):
    """
    用 refresh token 换取新的 access token（轮换机制）。

    面试话术："refresh token 只能用于此端点——不能访问业务 API。
    每次刷新都签发新 token 对（token rotation），旧的 refresh token
    可选择性作废——如果检测到已作废 token 被重用，说明泄露，
    立即吊销该用户所有 token。"
    """
    try:
        tokens = jwt_auth.refresh_access_token(req.refresh_token)
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired, please re-login")
    except (JWTError, ValueError) as e:
        raise HTTPException(status_code=401, detail=f"Invalid refresh token: {e}")

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        user={},  # refresh 时不返回 user 信息
        expires_in=900,
    )


@app.get("/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """
    获取当前登录用户信息（需 Bearer token）。

    面试话术："前端 SPA 刷新页面后，用存储的 access token 调 /auth/me
    恢复用户会话——不依赖 cookie/session，完全无状态。"
    """
    return {"user": current_user.to_dict()}


@app.post("/auth/logout")
async def logout(current_user: User = Depends(get_current_user),
                 credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    登出：将当前 access token 的 jti 加入吊销列表。

    面试话术："JWT 本身是无状态的——签发的 token 在过期前无法'取消'。
    解决方案：维护一个 Redis/内存吊销列表（基于 jti），
    登出时将 token ID 加入黑名单，验证时 O(1) 查询。
    生产环境用 Redis Set + TTL 对齐 token 过期时间。"
    """
    try:
        payload = jwt_auth.verify_token(credentials.credentials, token_type="access")
        jti = payload.get("jti", "")
        if jti:
            revocation_list.revoke(jti)
        return {"status": "logged_out", "revoked_tokens": revocation_list.count()}
    except Exception:
        # Token already invalid — still count as logged out
        return {"status": "logged_out", "revoked_tokens": revocation_list.count()}

@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, current_user: User = Depends(get_current_user)):
    """运行代码分析"""
    try:
        project_path = resolve_under_root(_allowed_root(), req.project_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger = AgentLogger("./logs")
    client = AnthropicClient(temperature=0.1)
    tools = build_api_tools(project_path)
    sid = db.create_session(name=req.query[:60], mode=req.mode,
                            project_path=str(project_path), owner_username=current_user.username)

    # 构建 query
    target = req.query
    if req.file_paths:
        target += "\n\nFiles:\n" + "\n".join(f"- {p}" for p in req.file_paths)

    is_multi = req.mode == "multi"

    if is_multi:
        orch = MultiAgentOrchestrator(client, tools, logger=logger)
        result = await asyncio.to_thread(
            orch.run, task=target, project_path=str(project_path)
        )
        result_text = result["final_report"]
        ms = result.get("stats", {})
        stats = {
            "planner": ms.get("planner", {}),
            "executor": ms.get("executor", {}),
            "reviewer": ms.get("reviewer", {}),
        }
    else:
        SYSTEM_PROMPT = "You are a code analysis agent. Find bugs, security, performance, style issues."
        agent = AgentHarness(model=client, tools=tools, system_prompt=SYSTEM_PROMPT,
                             max_turns=req.max_turns, logger=logger)
        result_text = await asyncio.to_thread(agent.run, target)
        stats = agent.get_stats()

    # 保存消息
    db.add_message(sid, "user", req.query)
    db.add_message(sid, "assistant", result_text, stats)

    # 解析发现并存入向量库
    _index_findings(sid, result_text, current_user.username)

    return AnalyzeResponse(
        session_id=sid, mode=req.mode, result=result_text,
        stats=stats, log_path=logger.log_path,
    )

@app.get("/sessions")
async def list_sessions(limit: int = 20, offset: int = 0,
                         current_user: User = Depends(get_current_user)):
    """会话列表，支持分页"""
    all_sessions = db.list_sessions(limit + offset, owner_username=current_user.username)
    return all_sessions[offset:offset + limit]


@app.get("/findings/page")
async def list_findings_paginated(
    offset: int = 0,
    limit: int = 20,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    current_user: User = Depends(get_current_user),
):
    """发现列表，支持分页 + 筛选"""
    all_findings = db.get_findings(category=category, severity=severity, limit=limit + offset,
                                   owner_username=current_user.username)
    return {
        "offset": offset,
        "limit": limit,
        "total": len(all_findings),
        "items": all_findings[offset:offset + limit],
    }

@app.get("/sessions/{sid}")
async def get_session(sid: str, current_user: User = Depends(get_current_user)):
    session = db.get_session(sid, owner_username=current_user.username)
    if not session:
        raise HTTPException(404, "Session not found")
    messages = db.get_messages(sid)
    findings = db.get_findings(session_id=sid, owner_username=current_user.username)
    return {"session": session, "messages": messages, "findings": findings}

@app.get("/findings")
async def list_findings(
    session_id: Optional[str] = None,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    verdict: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
):
    return db.get_findings(session_id, category, severity, verdict, limit,
                           owner_username=current_user.username)

@app.post("/findings/search")
async def search_findings(req: SearchRequest, current_user: User = Depends(get_current_user)):
    """向量语义搜索 + 关键词搜索"""
    vector_results = [result for result in vector_store.search(
        req.query, req.n_results, req.category, req.severity
    ) if db.get_session(result.get("session_id", ""), owner_username=current_user.username)]
    keyword_results = db.search_findings(req.query, req.n_results,
                                         owner_username=current_user.username)
    return {"vector": vector_results, "keyword": keyword_results}

@app.get("/findings/keyword")
async def keyword_search(q: str, limit: int = 20,
                         current_user: User = Depends(get_current_user)):
    return db.search_findings(q, limit, owner_username=current_user.username)

@app.get("/stats")
async def system_stats(current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator role required")
    return {
        "database": db.get_stats(),
        "vector_store": {"document_count": vector_store.count()},
    }

# ─── Helpers ────────────────────────────────────

def _index_findings(sid: str, text: str, owner_username: str):
    """从输出文本中解析 FINDING 行并存入向量库"""
    docs = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("FINDING|"):
            parts = line.split("|")
            # description 字段可能包含字面量 "|"，不能简单按固定下标取值。
            # 结构化字段固定在前 5 个（FINDING, file, line, category, severity），
            # 剩余自由文本从右侧解析，多余 "|" 并入 description_en，避免错位/数据损坏。
            if len(parts) >= 6:
                try:
                    file_path = parts[1].strip()
                    line_no = int(parts[2].strip()) if parts[2].strip().isdigit() else 0
                    category = parts[3].strip()
                    severity = parts[4].strip()
                    rest = parts[5:]
                    if len(rest) >= 3:
                        description_en = "|".join(rest[:-2]).strip()
                        description_cn = rest[-2].strip()
                        suggestion = rest[-1].strip()
                    elif len(rest) == 2:
                        description_en = rest[0].strip()
                        description_cn = rest[1].strip()
                        suggestion = ""
                    else:
                        description_en = rest[0].strip()
                        description_cn = ""
                        suggestion = ""
                    doc = FindingDocument(
                        file_path=file_path,
                        line=line_no,
                        category=category,
                        severity=severity,
                        description_en=description_en,
                        description_cn=description_cn,
                        suggestion=suggestion,
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
                        owner_username=owner_username,
                    ))
                except (ValueError, IndexError):
                    continue
    if docs:
        vector_store.add_batch(docs)

# ─── Main ───────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
