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
import json
import asyncio
import datetime
import logging
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
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
from src.multi_agent.factory import create_langgraph_orchestrator, langgraph_final_report
from src.multi_agent.finding_parser import parse_finding_lines
from src.tools.git_tools import list_files, read_file, grep_pattern
from src.storage.database import Database, Finding
from src.memory.vector_store import VectorStore, FindingDocument
from src.security.paths import resolve_under_root
from src.api.jobs import Job, JobRegistry, JobStatus
from src.harness.request_context import (
    get_context, new_request_id, set_context as _set_context,
    reset_context as _reset_context, update_context as _update_context,
)

_LOGGER = logging.getLogger(__name__)


# 统一错误码：把散落的自由文本 detail 归类，供调用方按类别处理与告警。
ERROR_CODES = {
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    422: "INVALID_REQUEST",
    429: "RATE_LIMITED",
    503: "SERVICE_NOT_READY",
}


# ─── 结构化日志 / Structured logging ───────────────
# 此前全仓没有 basicConfig，日志走 logging lastResort 明文 stderr：
# 没有时间戳、级别、请求号，无法被采集系统（Loki/ELK）直接消费。
# 这里输出单行 JSON 到 stdout，并在每条记录里带上请求号与操作者。

class _JsonLogFormatter(logging.Formatter):
    """把日志记录序列化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        context = get_context()
        payload = {
            "ts": datetime.datetime.fromtimestamp(
                record.created, tz=datetime.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if context.request_id:
            payload["request_id"] = context.request_id
        if context.actor:
            payload["actor"] = context.actor
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _init_structured_logging() -> None:
    """配置一次结构化日志（幂等：重复调用不会叠加 handler）。"""
    root = logging.getLogger("src")
    if any(getattr(h, "_dsh_json", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonLogFormatter())
    handler._dsh_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

# ─── 数据目录 / 延迟初始化 ─────────────────

DATA_DIR = Path(__file__).parent.parent.parent / "data"

# 本服务认得的配置项。用于把"拼错变量名 → 静默回退默认值"变成显式告警：
# 之前把 CODE_REVIEW_ALLOWED_ROOT 写成复数会悄悄退回仓库根目录，
# 部署者以为收紧了范围，实际用的是默认值。
KNOWN_CONFIG_VARS = {
    "CODE_REVIEW_ALLOWED_ROOT", "CODE_REVIEW_PROJECT_PATH",
    "CORS_ALLOWED_ORIGINS", "SKILLS_DIR", "LLM_API_PROTOCOL",
    "JWT_SECRET_KEY", "JWT_SECRET_FILE", "ADMIN_PASSWORD",
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
}

# 命名前缀：只有这些前缀下的未知变量才值得告警，避免对无关环境变量刷警告。
_CONFIG_PREFIXES = ("CODE_REVIEW_", "CORS_ALLOWED_")

# 已知的易错写法 → 正确名（给出更明确的提示）
_CONFIG_TYPOS = {
    "CODE_REVIEW_ALLOWED_ROOTS": "CODE_REVIEW_ALLOWED_ROOT",
    "CORS_ALLOWED_ORIGIN": "CORS_ALLOWED_ORIGINS",
    "CODE_REVIEW_PROJECT_PATHS": "CODE_REVIEW_PROJECT_PATH",
}

# db / vector_store 改为在应用启动（lifespan）时初始化，
# 避免模块导入时产生数据库/文件副作用（可测试性、可移植性）。
db: Optional[Database] = None
vector_store: Optional[VectorStore] = None


def get_db() -> Database:
    """获取已初始化的数据库。

    端点内直接用模块级 Optional 变量会同时带来两处问题：
    ① 类型检查报 union-attr；② 万一 lifespan 未执行（例如被当作
    裸 ASGI 应用挂载），访问会抛 AttributeError 并变成 500。
    这里集中转成明确的 503，让"尚未就绪"可被调用方识别。
    """
    if db is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return db


def get_vector_store() -> VectorStore:
    """获取已初始化的向量库（未就绪返回 503，而不是 500）。"""
    if vector_store is None:
        raise HTTPException(status_code=503, detail="Vector store not initialized")
    return vector_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库与向量库，关闭时释放连接。"""
    global db, vector_store
    db = Database(str(DATA_DIR / "code_review.db"))
    vector_store = VectorStore(str(DATA_DIR / "search.db"))
    try:
        yield
    finally:
        # 显式关闭长期持有的 SQLite 连接，避免进程退出时报
        # "unclosed database" 资源警告（reload/测试下会反复出现）。
        if vector_store is not None:
            vector_store.close()
        db = None
        vector_store = None


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

# ─── Rate limiting ────────────────────────────
#
# ⚠️ 必须同时挂 SlowAPIMiddleware：slowapi 的 `default_limits` 只在中间件
# 路径上生效。仅设置 app.state.limiter 时，未被 @limiter.limit 装饰的路由
# （/analyze、/findings/search、/dashboard 等）完全不受限流保护——
# 而 /analyze 是分钟级、消耗真实 token 的最贵端点。
# 限流键优先取已认证用户名，取不到再回退远端 IP：
# 反代场景下单独用 IP 会让所有用户共用一个桶。


def _rate_limit_key(request: Request) -> str:
    """限流键：优先已认证用户名，其次远端 IP。

    认证解析失败（未登录/令牌无效）时回退 IP，保证登录接口仍有防爆破能力。
    注意 jwt_auth 在本模块后面初始化——该函数只在请求到达时调用，此时已就绪。
    """
    try:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            payload = jwt_auth.verify_token(header.split(" ", 1)[1].strip(),
                                            token_type="access")
            subject = payload.get("sub")
            if subject:
                return f"user:{subject}"
    except (JWTError, ValueError):
        # 令牌无效/过期不应让限流键计算失败
        pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_limit_key, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

_init_structured_logging()


def _audit(event: str, *, actor: str = "", session_id: str = "", outcome: str = "",
           file_path: str = "", message: str = "", error: bool = False) -> None:
    """写一条审计记录（登录、分析、写操作、拒绝）。

    审计回答的是"谁在什么时候对哪个文件做了什么"：
    主体是 actor，线索是 request_id（自动取自上下文），对象是 file_path。
    写失败不能影响主流程，但必须留日志——审计静默失败比没有审计更危险。
    """
    try:
        database = get_db()
    except HTTPException:
        # 数据库未就绪（启动早期）时只记本地日志
        _LOGGER.warning("audit event dropped (db not ready): %s", event)
        return
    context = get_context()
    try:
        database.add_log(
            session_id or context.request_id or "-",
            event,
            error=error,
            message=message,
            owner_username=actor or context.actor,
            actor=actor or context.actor,
            request_id=context.request_id,
            file_path=file_path,
            outcome=outcome,
        )
    except Exception:
        _LOGGER.exception("failed to persist audit event %s", event)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """统一错误响应：保留可读 detail，同时给出稳定的 error_code。

    旧实现只有自由文本 detail，调用方无法按类别处理（只能匹配字符串）。
    """
    code = ERROR_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_code": code, "detail": exc.detail,
                 "request_id": getattr(request.state, "request_id", "")},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底：未预期异常也必须返回带 request_id 的结构化响应。

    没有这一层时，starlette 会返回 HTML/纯文本的 500，
    调用方拿不到 request_id，运维无法把报错对应到具体请求。
    """
    _LOGGER.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error_code": "INTERNAL_ERROR",
                 "detail": "Internal server error",
                 "request_id": getattr(request.state, "request_id", "")},
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """为每个请求建立审计上下文、兜底未预期异常、回写请求号。

    请求号是"把一次用户操作串起来"的唯一线索：没有它，
    日志、遥测 JSONL、数据库审计行三处无法关联到同一次请求。

    ⚠️ 未预期异常在这里兜底，而不是只依赖 `@app.exception_handler(Exception)`：
    starlette 的 ServerErrorMiddleware 位于最外层，会先于 FastAPI 的异常中间件
    处理 500，导致注册的处理器在部分路径上不生效（响应格式不稳定）。
    在自建中间件里直接捕获，可以保证**任何**未预期异常都返回同样的结构。
    """
    incoming = request.headers.get("x-request-id", "").strip()
    request_id = incoming[:64] if incoming else new_request_id()
    actor = ""
    try:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            payload = jwt_auth.verify_token(header.split(" ", 1)[1].strip(),
                                            token_type="access")
            actor = str(payload.get("sub") or "")
    except (JWTError, ValueError):
        actor = ""
    token = _set_context(request_id=request_id, actor=actor)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        _LOGGER.exception("unhandled error on %s %s",
                          request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error_code": "INTERNAL_ERROR",
                     "detail": "Internal server error",
                     "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )
    finally:
        _reset_context(token)
    response.headers["X-Request-ID"] = request_id
    return response


async def rate_limit_handler(request: Request, exc: Exception) -> Response:
    """slowapi 默认处理器的显式包装。

    直接注册 `_rate_limit_exceeded_handler` 时，其 `(Request, RateLimitExceeded)`
    签名与 Starlette 期望的 `(Request, Exception)` 不匹配（类型检查报错）。
    这里统一成 Exception，并把非目标异常原样抛出，行为与 slowapi 默认一致。
    """
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)


app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


def _warn_config_typos() -> list[str]:
    """检测未知 / 错拼的本服务配置项，返回告警文本（不抛异常，只提示）。

    按**变量名**判定而不是按"正确名是否存在"：`.env` 会被加载进 os.environ，
    如果只在"正确名缺失"时告警，一旦 .env 里恰好有正确名，错拼项就被静默忽略。
    """
    warnings: list[str] = []
    for name in sorted(os.environ):
        if name in KNOWN_CONFIG_VARS:
            continue
        if not name.startswith(_CONFIG_PREFIXES):
            continue
        suggestion = _CONFIG_TYPOS.get(name)
        hint = f"，是否想设置 '{suggestion}'？" if suggestion else "（该变量不会被读取）"
        warnings.append(f"环境变量 '{name}' 不是可识别的配置项{hint}")
        _LOGGER.warning("Unrecognized config variable %s%s", name, hint)
    return warnings


# 启动期配置拼写检查：把"静默回退默认值"变成显式告警
_config_warnings = _warn_config_typos()

# ─── 异步任务注册表 ────────────────────────────
# 并发上限与单任务超时可配置：分钟级任务若没有上限，几十个并发就会把
# asyncio 线程池占满并让所有请求排队（且无任何反馈）。
job_registry = JobRegistry(
    max_concurrent=int(os.environ.get("ANALYZE_MAX_CONCURRENT", "4")),
    default_timeout_s=float(os.environ.get("ANALYZE_TIMEOUT_SECONDS", "900")),
)
if os.environ.get("UVICORN_WORKERS", "1") not in {"", "1"}:
    _LOGGER.warning(
        "任务注册表为进程内存储，多 worker 下状态不共享（worker=%s）；"
        "生产多 worker 部署需外置到 Redis/DB",
        os.environ.get("UVICORN_WORKERS"))

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

    设计说明："每个请求到达时，FastAPI 依赖注入系统自动调用
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


def _build_llm_client() -> AnthropicClient:
    """构造 LLM 客户端的唯一入口（依赖注入缝）。

    为什么单独抽一个函数：`/analyze` 此前在函数体内直接 `AnthropicClient(...)`，
    而真实客户端会发起网络请求，导致这条最长、最贵的端点**无法被任何测试触发**。
    测试通过替换本函数即可让整条链路离线运行：

        monkeypatch.setattr(server, "_build_llm_client", lambda: ScriptedLLM([...]))

    契约：返回值只需提供 `chat(messages, tools=...)`；流式路径额外需要 `stream()`。
    """
    return AnthropicClient(temperature=0.1)


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
    # ── 降级标记 ──
    # 模型故障 / turn 预算耗尽 / 空回答都会产出"有文本但不可信"的结果。
    # 旧实现一律返回 HTTP 200 且只有人类可读文本，调用方无法程序化区分
    # "分析成功"与"模型 429"。新增字段而不是改状态码，避免破坏现有消费者。
    degraded: bool = Field(False, description="结果是否已降级（不可当作成功结论）")
    error_code: str = Field("", description="降级原因码，例如 model_error:RuntimeError")
    # 异步提交时返回任务号，便于轮询/取消
    job_id: str = Field("", description="异步任务号（异步提交时返回）")


class JobSubmitResponse(BaseModel):
    """异步提交的即时响应（HTTP 202）。"""

    job_id: str
    status: str
    session_id: str = ""
    queue_ms: int = 0
    idempotent_replay: bool = Field(
        False, description="是否为幂等键命中的重复提交（返回既有任务）")


class JobStatusResponse(BaseModel):
    """任务状态查询响应。"""

    job_id: str
    status: str
    mode: str
    session_id: str
    project_path: str
    progress: str = ""
    error_code: str = ""
    degraded: bool = False
    elapsed_ms: int = 0
    queue_ms: int = 0
    terminal: bool = False
    result: str = Field("", description="终态时的分析结果")
    stats: dict = Field(default_factory=dict)

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

    # 指标值是浮点（Prometheus 输出带小数），用 float 累加避免
    # "int += float" 的类型错误与截断。
    total_requests = 0.0
    error_count = 0.0
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
    """健康检查（无需认证）。

    ⚠️ 该端点公开可访问，因此只返回存活性与是否就绪，
    绝不返回 get_stats() 这类业务统计——否则匿名调用者就能拿到
    发现总数与类别/严重度分布（信息泄露）。
    """
    return {
        "status": "healthy",
        "version": "2.0.0",
        "database_ready": db is not None,
        "vector_store_ready": vector_store is not None,
    }


# ─── Auth Routes ────────────────────────────────

@app.post("/auth/login", response_model=TokenResponse)
@limiter.limit("10/minute")  # 登录接口严格限流：防止暴力破解
async def login(req: LoginRequest, request: Request):
    """
    用户登录 → 返回 JWT access + refresh token 对。

    设计说明："登录接口单独限流 10次/分钟——暴力破解攻击者
    最多尝试 10 个密码就被封堵 1 分钟，同时配合 bcrypt 的
    慢哈希（~100ms/次）让离线破解也不划算。"
    """
    user = user_store.verify_password(req.username, req.password)
    if not user:
        # 登录失败必须审计（此前零记录），否则无法发现撞库/爆破
        _audit("auth_login", actor=req.username, outcome="FAILED",
               message="invalid credentials", error=True)
        _LOGGER.warning("login failed for user=%s", req.username)
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tokens = jwt_auth.create_tokens(user)
    _audit("auth_login", actor=user.username, outcome="SUCCESS")
    _LOGGER.info("login succeeded user=%s", user.username)

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

    设计说明："refresh token 只能用于此端点——不能访问业务 API。
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

    设计说明："前端 SPA 刷新页面后，用存储的 access token 调 /auth/me
    恢复用户会话——不依赖 cookie/session，完全无状态。"
    """
    return {"user": current_user.to_dict()}


@app.post("/auth/logout")
async def logout(current_user: User = Depends(get_current_user),
                 credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    登出：将当前 access token 的 jti 加入吊销列表。

    设计说明："JWT 本身是无状态的——签发的 token 在过期前无法'取消'。
    解决方案：维护一个 Redis/内存吊销列表（基于 jti），
    登出时将 token ID 加入黑名单，验证时 O(1) 查询。
    生产环境用 Redis Set + TTL 对齐 token 过期时间。"
    """
    try:
        payload = jwt_auth.verify_token(credentials.credentials, token_type="access")
        jti = payload.get("jti", "")
        if jti:
            revocation_list.revoke(jti)
        _audit("auth_logout", actor=current_user.username, outcome="SUCCESS")
        return {"status": "logged_out", "revoked_tokens": revocation_list.count()}
    except Exception:
        # Token already invalid — still count as logged out
        _audit("auth_logout", actor=current_user.username, outcome="INVALID_TOKEN",
               message="token 已失效，仍按登出处理", error=True)
        return {"status": "logged_out", "revoked_tokens": revocation_list.count()}

async def _run_analysis(req: "AnalyzeRequest", *, project_path: Path, sid: str,
                        actor: str, logger: AgentLogger,
                        on_progress=None) -> dict:
    """执行一次分析并落库，返回结果字段。

    同步端点与异步任务共用同一实现，避免两条路径行为漂移
    （这与"三入口编排统一"是同一类问题：能力只能有一份实现）。

    调用方负责：先解析并校验 project_path、建好会话与 logger。
    """
    def _progress(text: str) -> None:
        if on_progress:
            on_progress(text)

    client = _build_llm_client()

    # 构建 query
    target = req.query
    if req.file_paths:
        target += "\n\nFiles:\n" + "\n".join(f"- {p}" for p in req.file_paths)

    is_multi = req.mode == "multi"
    degraded = False
    error_code = ""
    stats: dict = {}

    try:
        # 工具与编排的构造放在 try 内：它们同样可能抛异常（例如受限根目录
        # 在启动后被删除）。放在 try 外会让异常穿透到 starlette 的兜底
        # 中间件，返回不带 request_id 的裸 500，同时也漏掉审计记录。
        tools = build_api_tools(project_path)
        if is_multi:
            _progress("orchestrating")
            orch = create_langgraph_orchestrator(client, tools, logger=logger)
            langgraph_result = await asyncio.to_thread(
                orch.run, task=target, project_path=str(project_path)
            )
            result_text = langgraph_final_report(langgraph_result)
            ms = langgraph_result.get("node_stats", {})
            stats = {
                "plan": ms.get("plan", {}),
                "review": ms.get("review", {}),
                "node_stats": ms,
            }
            # 编排层自己判定降级（未到 review 节点 / 某节点用满 turn 预算）
            if langgraph_result.get("degraded"):
                degraded = True
                error_code = str(langgraph_result.get("degraded_reason") or "orchestrator_degraded")
        else:
            _progress("analyzing")
            SYSTEM_PROMPT = ("You are a code analysis agent. "
                             "Find bugs, security, performance, style issues.")
            agent = AgentHarness(model=client, tools=tools, system_prompt=SYSTEM_PROMPT,
                                 max_turns=req.max_turns, logger=logger)
            result_text = await asyncio.to_thread(agent.run, target)
            stats = agent.get_stats()
            degraded = bool(agent.degraded)
            error_code = agent.degraded_reason
    except Exception as exc:
        # 未预期的异常不能变成裸 500：会话已经建好，必须留下可解释的终态，
        # 否则会出现"有 session 但没有任何消息"的孤儿记录。
        #
        # 但**不把异常文本回显给调用方**：上游错误体可能包含网关地址、
        # 内部路径甚至密钥片段（llm_client 会把上游响应体拼进异常消息）。
        # 完整细节进日志与审计，调用方只拿到错误类别 + 请求号用于对账。
        request_id = get_context().request_id
        _LOGGER.exception("analyze failed for session %s", sid)
        error_code = f"analyze_failed:{type(exc).__name__}"
        result_text = (f"Analysis failed ({type(exc).__name__}). "
                       f"Reference: {request_id or 'n/a'}")
        degraded = True
        stats = {"degraded": True, "degraded_reason": error_code}
    else:
        _progress("indexing")
        # 解析发现并存入向量库（带上所有者，检索时才能按用户隔离）
        _index_findings(sid, result_text, actor)

    # 保存消息（成功与失败都落库，保证"有 session 必有内容"）
    get_db().add_message(sid, "user", req.query)
    get_db().add_message(sid, "assistant", result_text, stats)
    # 事件名保持 `analyze_complete` 不变（降级用 outcome 区分）：
    # 事件名一旦随状态变化，基于事件名的审计查询就会漏记录。
    _audit("analyze_complete", actor=actor, session_id=sid,
           outcome="FAILED" if error_code.startswith("analyze_failed")
           else ("DEGRADED" if degraded else "SUCCESS"),
           file_path=str(project_path), message=error_code,
           error=error_code.startswith("analyze_failed"))
    _progress("done")
    return {"session_id": sid, "mode": req.mode, "result": result_text,
            "stats": stats, "log_path": logger.log_path,
            "degraded": degraded, "error_code": error_code}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, current_user: User = Depends(get_current_user)):
    """同步运行代码分析（保留兼容）。

    长任务（分钟级）请改用 `POST /analyze/async` + `GET /jobs/{id}`：
    同步端点会让请求一直挂到分析结束。

    降级语义：模型故障 / turn 预算耗尽 / 空回答都仍有 HTTP 200 + 可读文本，
    但同时把 `degraded=True` 与 `error_code` 写进响应体，让调用方能程序化判断。
    """
    project_path, sid, logger = _prepare_analysis(req, current_user.username)
    payload = await _run_analysis(req, project_path=project_path, sid=sid,
                                  actor=current_user.username, logger=logger)
    return AnalyzeResponse(**payload)


@app.post("/analyze/async", response_model=JobSubmitResponse, status_code=202)
async def analyze_async(req: AnalyzeRequest, request: Request,
                        current_user: User = Depends(get_current_user)):
    """异步提交分析：立即返回 job_id（HTTP 202），执行在后台进行。

    支持 `Idempotency-Key` 请求头：同一用户用同一 key 重复提交会返回
    同一个任务，而不是重复消耗 token。
    """
    idempotency_key = request.headers.get("idempotency-key", "").strip()[:128]
    existing = job_registry.find_idempotent(current_user.username, idempotency_key)
    if existing is not None:
        return JobSubmitResponse(job_id=existing.job_id, status=existing.status.value,
                                 session_id=existing.session_id,
                                 queue_ms=existing.queue_ms,
                                 idempotent_replay=True)

    project_path, sid, logger = _prepare_analysis(req, current_user.username)
    job = job_registry.create(actor=current_user.username, session_id=sid,
                              mode=req.mode, project_path=str(project_path),
                              idempotency_key=idempotency_key)

    async def runner(current: Job) -> None:
        # 后台任务的上下文不再来自请求，需要显式重建，
        # 否则遥测/审计拿不到 actor 与请求号。
        token = _set_context(request_id=f"job-{current.job_id}",
                             actor=current.actor,
                             project_path=current.project_path)
        try:
            payload = await _run_analysis(
                req, project_path=project_path, sid=sid, actor=current.actor,
                logger=logger, on_progress=lambda text: setattr(current, "progress", text))
            current.result = payload["result"]
            current.stats = payload["stats"]
            current.degraded = payload["degraded"]
            current.error_code = payload["error_code"]
            current.status = JobStatus.SUCCEEDED
        finally:
            _reset_context(token)

    task = asyncio.create_task(job_registry.start(job, runner))
    job_registry.set_task(job, task)

    _audit("analyze_submitted", actor=current_user.username, session_id=sid,
           outcome="QUEUED", file_path=str(project_path), message=job.job_id)
    return JobSubmitResponse(job_id=job.job_id, status=job.status.value,
                             session_id=sid, queue_ms=job.queue_ms,
                             idempotent_replay=False)


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, current_user: User = Depends(get_current_user)):
    """查询任务状态与结果（仅任务所有者可见）。"""
    job = job_registry.get_owned(job_id, current_user.username)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusResponse(
        job_id=job.job_id, status=job.status.value, mode=job.mode,
        session_id=job.session_id, project_path=job.project_path,
        progress=job.progress, error_code=job.error_code, degraded=job.degraded,
        elapsed_ms=job.elapsed_ms, queue_ms=job.queue_ms,
        terminal=job.status.terminal,
        result=job.result if job.status.terminal else "",
        stats=job.stats if job.status.terminal else {},
    )


@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str, current_user: User = Depends(get_current_user)):
    """请求取消任务。已进入终态的任务返回 409。"""
    job = job_registry.get_owned(job_id, current_user.username)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job_registry.cancel(job):
        raise HTTPException(status_code=409,
                            detail=f"Job already finished ({job.status.value})")
    _audit("analyze_cancelled", actor=current_user.username, session_id=job.session_id,
           outcome="CANCELLED", file_path=job.project_path, message=job_id)
    return {"job_id": job_id, "status": job.status.value}


def _prepare_analysis(req: AnalyzeRequest, actor: str) -> tuple[Path, str, AgentLogger]:
    """同步/异步两个入口共用的准备步骤：校验路径、建会话、建 logger。"""
    if req.mode not in {"single", "multi"}:
        # 之前 mode 是自由字符串，写错只会静默走 single 分支
        raise HTTPException(status_code=422, detail="mode must be 'single' or 'multi'")
    try:
        project_path = resolve_under_root(_allowed_root(), req.project_path)
    except (FileNotFoundError, ValueError) as exc:
        # 越界访问必须审计：这是有人尝试读取范围外目录的信号
        _audit("analyze_rejected", actor=actor, outcome="FORBIDDEN",
               file_path=req.project_path, message=str(exc), error=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 把目标项目写进上下文：此后创建的任何 AgentLogger 都会带上它
    _update_context(project_path=str(project_path))
    logger = AgentLogger("./logs", actor=actor)
    sid = get_db().create_session(name=req.query[:60], mode=req.mode,
                                  project_path=str(project_path),
                                  owner_username=actor)
    _audit("analyze_start", actor=actor, session_id=sid,
           outcome="STARTED", file_path=str(project_path), message=req.query[:200])
    return project_path, sid, logger

@app.get("/sessions")
async def list_sessions(limit: int = 20, offset: int = 0,
                         current_user: User = Depends(get_current_user)):
    """会话列表，支持分页"""
    all_sessions = get_db().list_sessions(limit + offset, owner_username=current_user.username)
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
    all_findings = get_db().get_findings(category=category, severity=severity, limit=limit + offset,
                                         owner_username=current_user.username)
    return {
        "offset": offset,
        "limit": limit,
        "total": len(all_findings),
        "items": all_findings[offset:offset + limit],
    }

@app.get("/sessions/{sid}")
async def get_session(sid: str, current_user: User = Depends(get_current_user)):
    database = get_db()
    session = database.get_session(sid, owner_username=current_user.username)
    if not session:
        raise HTTPException(404, "Session not found")
    messages = database.get_messages(sid, owner_username=current_user.username)
    findings = database.get_findings(session_id=sid, owner_username=current_user.username)
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
    return get_db().get_findings(session_id, category, severity, verdict, limit,
                                 owner_username=current_user.username)

@app.post("/findings/search")
async def search_findings(req: SearchRequest, current_user: User = Depends(get_current_user)):
    """向量语义搜索 + 关键词搜索（两者都按当前用户隔离）"""
    database = get_db()
    vector_results = get_vector_store().search(
        req.query, req.n_results, req.category, req.severity,
        owner_username=current_user.username,
    )
    keyword_results = database.search_findings(req.query, req.n_results,
                                               owner_username=current_user.username)
    return {"vector": vector_results, "keyword": keyword_results}

@app.get("/findings/keyword")
async def keyword_search(q: str, limit: int = 20,
                         current_user: User = Depends(get_current_user)):
    return get_db().search_findings(q, limit, owner_username=current_user.username)

@app.get("/stats")
async def system_stats(current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator role required")
    return {
        "database": get_db().get_stats(),
        "vector_store": {"document_count": get_vector_store().count()},
    }

# ─── Helpers ────────────────────────────────────

def _index_findings(sid: str, text: str, owner_username: str):
    """从输出文本中解析 FINDING 行并存入 SQLite 与向量库。

    索引降级：分析本身已完成、消息也已落库，此时若向量库未就绪，
    不应该把整个请求判为失败（调用方会看到 503 但数据其实已写入）。
    因此向量写入失败只记日志并跳过，SQLite 记录仍然保留。
    """
    database = get_db()
    docs = []
    for parsed in parse_finding_lines(text):
        doc = FindingDocument(
            file_path=parsed["file_path"],
            line=parsed["line"],
            category=parsed["category"],
            severity=parsed["severity"],
            description_en=parsed["description_en"],
            description_cn=parsed["description_cn"],
            suggestion=parsed["suggestion"],
            session_id=sid,
            verified=False,
            owner_username=owner_username,
        )
        docs.append(doc)
        # 同时存入 SQLite
        database.add_finding(sid, Finding(
            id=doc.id, session_id=sid, file_path=doc.file_path,
            line=doc.line, category=doc.category, severity=doc.severity,
            description_en=doc.description_en, description_cn=doc.description_cn,
            suggestion=doc.suggestion, verdict="PENDING", embedding_id=doc.id,
            owner_username=owner_username,
        ))
    if not docs:
        return
    try:
        get_vector_store().add_batch(docs, owner_username=owner_username)
    except HTTPException:
        _LOGGER.warning(
            "Vector store unavailable; %d finding(s) stored without full-text index "
            "(session %s)", len(docs), sid, exc_info=True)

# ─── Main ───────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
