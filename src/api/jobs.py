"""异步分析任务注册表 / Async analysis job registry.

解决的问题（企业级审计发现）
    `/analyze` 整段 `await asyncio.to_thread(...)` 同步阻塞：请求一直挂到分析
    结束（分钟级），调用方**无法取消、无法查进度、无法设置幂等键**；配合
    没有 graceful shutdown，滚动发布会 SIGKILL 在跑的分析并留下孤儿会话。
    实际并发上限只是 asyncio 默认线程池 `min(32, cpu+4)`，排队请求无反馈。

设计
    - 提交即返回 job_id（HTTP 202），执行在后台任务里；
    - **并发闸门**：同时运行的任务数有上限，超出的排队而不是一起冲进线程池；
    - **单任务超时**：超过 deadline 取消任务并标记 `TIMEOUT`；
    - **幂等键**：同一 (actor, idempotency_key) 重复提交返回同一个 job；
    - **进度可查**：`progress` 字段由执行体回调更新。

⚠️ 进程内存储：与既有的吊销列表/用户库一致，单 worker 下正确。
   多 worker 部署需要外置（Redis/DB），这一点在日志与文档里显式说明，
   避免被误认为已具备分布式语义。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    """任务状态。终态：SUCCEEDED / FAILED / TIMEOUT / CANCELLED。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED,
                        JobStatus.TIMEOUT, JobStatus.CANCELLED}


@dataclass
class Job:
    """一个分析任务。"""

    job_id: str
    actor: str
    session_id: str
    mode: str
    project_path: str
    status: JobStatus = JobStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: str = ""
    error_code: str = ""
    result: str = ""
    degraded: bool = False
    stats: dict = field(default_factory=dict)
    idempotency_key: str = ""
    _task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def elapsed_ms(self) -> int:
        end = self.finished_at or time.time()
        start = self.started_at or self.created_at
        return int((end - start) * 1000)

    @property
    def queue_ms(self) -> int:
        if self.started_at is None:
            return int((time.time() - self.created_at) * 1000)
        return int((self.started_at - self.created_at) * 1000)

    def to_dict(self) -> dict:
        """对外表示（不含内部 task 句柄）。"""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "mode": self.mode,
            "session_id": self.session_id,
            "project_path": self.project_path,
            "progress": self.progress,
            "error_code": self.error_code,
            "degraded": self.degraded,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_ms": self.elapsed_ms,
            "queue_ms": self.queue_ms,
            "terminal": self.status.terminal,
        }


class JobRegistry:
    """进程内的任务注册表 + 并发闸门。

    并发上限用 `asyncio.Semaphore`：超出的任务保持 QUEUED 排队，
    而不是全部塞进 asyncio 线程池（那样等于没有上限，只是把排队转移了位置）。
    """

    def __init__(self, max_concurrent: int = 4, default_timeout_s: float = 900.0,
                 retention: int = 200):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._jobs: dict[str, Job] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
        self.default_timeout_s = default_timeout_s
        self._retention = retention

    # ─── 查询 ───

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def get_owned(self, job_id: str, actor: str) -> Job | None:
        """按所有者取任务：非所有者视为不存在（不泄露他人任务信息）。"""
        job = self._jobs.get(job_id)
        if job is None or job.actor != actor:
            return None
        return job

    def find_idempotent(self, actor: str, key: str) -> Job | None:
        if not key:
            return None
        job_id = self._idempotency.get((actor, key))
        return self._jobs.get(job_id) if job_id else None

    def counts(self) -> dict[str, int]:
        counts = {status.value: 0 for status in JobStatus}
        for job in self._jobs.values():
            counts[job.status.value] += 1
        counts["total"] = len(self._jobs)
        return counts

    # ─── 生命周期 ───

    def create(self, *, actor: str, session_id: str, mode: str, project_path: str,
               idempotency_key: str = "") -> Job:
        job = Job(job_id=uuid.uuid4().hex[:16], actor=actor, session_id=session_id,
                  mode=mode, project_path=project_path,
                  idempotency_key=idempotency_key)
        self._jobs[job.job_id] = job
        if idempotency_key:
            self._idempotency[(actor, idempotency_key)] = job.job_id
        self._evict_old()
        return job

    async def start(self, job: Job, runner, *, timeout_s: float | None = None):
        """在并发闸门内运行 runner。

        runner 是 `async (job) -> None`，负责写 job.result / stats / status。
        这里统一处理排队、超时、取消与异常——执行体只关心业务。
        """
        timeout = timeout_s if timeout_s is not None else self.default_timeout_s
        await self._semaphore.acquire()
        try:
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            try:
                await asyncio.wait_for(runner(job), timeout=timeout)
                if not job.status.terminal:
                    job.status = JobStatus.SUCCEEDED
            except asyncio.TimeoutError:
                # 超时必须显式标记：否则调用方会一直等一个永远不会完成的任务
                job.status = JobStatus.TIMEOUT
                job.error_code = f"job_timeout:{int(timeout)}s"
            except asyncio.CancelledError:
                job.status = JobStatus.CANCELLED
                job.error_code = "job_cancelled"
                raise
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error_code = f"job_failed:{type(exc).__name__}"
                job.progress = str(exc)[:200]
            finally:
                job.finished_at = time.time()
        finally:
            self._semaphore.release()

    def cancel(self, job: Job) -> bool:
        """请求取消：仅对未进入终态的任务有效。"""
        if job.status.terminal:
            return False
        if job._task is not None and not job._task.done():
            job._task.cancel()
            return True
        # 还在排队（尚未拿到信号量）：直接置为取消，start() 会跳过执行
        job.status = JobStatus.CANCELLED
        job.error_code = "job_cancelled_before_start"
        job.finished_at = time.time()
        return True

    def set_task(self, job: Job, task: asyncio.Task) -> None:
        job._task = task

    def _evict_old(self) -> None:
        """按保留量淘汰最旧的终态任务，避免进程内无限增长。

        排序键用 `finished_at or created_at` 时必须注意：`finished_at` 可能是
        0.0（测试或早期时间戳），`or` 会把它当假值而改用 created_at。
        这里对 None/0 都显式回退，保证"最旧"的判定稳定。
        """
        if len(self._jobs) <= self._retention:
            return
        terminal = sorted(
            (j for j in self._jobs.values() if j.status.terminal),
            key=lambda j: j.finished_at if j.finished_at is not None else j.created_at)
        excess = len(self._jobs) - self._retention
        for job in terminal[:excess]:
            self._jobs.pop(job.job_id, None)
            if job.idempotency_key:
                self._idempotency.pop((job.actor, job.idempotency_key), None)

    def reset(self) -> None:
        """清空（测试用）。"""
        self._jobs.clear()
        self._idempotency.clear()
