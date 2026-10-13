# 多阶段构建：builder 装依赖，运行镜像不带编译链与构建缓存。
FROM python:3.11-slim AS builder

WORKDIR /build

# 依赖声明唯一来源是 pyproject.toml（requirements.txt 仅保留给旧流程，
# 两者不一致时以 pyproject 为准）。先只复制依赖声明，让依赖层可缓存。
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build \
    && pip wheel --no-cache-dir --wheel-dir /wheels .


FROM python:3.11-slim

# 运行期只需要 git（Agent 的 clone/diff 工具）与 curl（HEALTHCHECK）。
# build-essential 只在 builder 阶段存在，不进运行镜像。
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl \
    && rm -rf /var/lib/apt/lists/*

# 非 root 运行：容器被攻破时不再直接拥有 root 能力。
# 固定 UID/GID 便于挂载卷的权限对齐。
RUN groupadd --gid 1001 app && useradd --uid 1001 --gid 1001 --create-home app

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl \
    && rm -rf /wheels

# 应用代码（.dockerignore 已排除 .env / data / logs / tests 等）
COPY --chown=app:app . .

# 数据目录必须归属运行用户，否则非 root 无法写入
RUN mkdir -p /app/data /app/logs /app/reports /app/sessions \
    && chown -R app:app /app/data /app/logs /app/reports /app/sessions

USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000 8501

# 与 /health 端点对齐：它就是用于存活/就绪探测的公开端点
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# 默认：FastAPI 服务。--timeout-graceful-shutdown 给在跑的分析留出收尾时间，
# 避免 docker stop 10 秒后 SIGKILL 留下孤儿会话。
CMD ["uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "30"]
