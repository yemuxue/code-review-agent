"""Sandbox isolation module

进程级沙箱：subprocess + 线程 + 双重 timeout + 临时目录

生产级升级方案：Docker 容器隔离
  docker run --rm --network=none --read-only \
    --memory=256m --cpus=1 \
    --security-opt=no-new-privileges \
    -v /tmp/sandbox:/work:rw \
    alpine sh -c "command"

  对比：
  - 当前：进程级 subprocess，临时目录隔离
  - Docker：网络隔离 + 只读根FS + 内存限制 + seccomp
  - 面试话术见模块末尾
"""
from __future__ import annotations
import os, shutil, tempfile, subprocess, threading
from contextlib import contextmanager

class SandboxResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, timed_out: bool):
        self.exit_code = exit_code; self.stdout = stdout; self.stderr = stderr; self.timed_out = timed_out
    @property
    def success(self) -> bool: return self.exit_code == 0 and not self.timed_out
    def summary(self) -> str:
        s = f"[{'TIMEOUT' if self.timed_out else f'EXIT:{self.exit_code}'}]"
        if self.stdout: s += f"\nSTDOUT:\n{self.stdout[:2000]}"
        if self.stderr: s += f"\nSTDERR:\n{self.stderr[:1000]}"
        return s

class Sandbox:
    def __init__(self, base_dir: str | None = None, timeout: int = 60):
        self.base_dir = base_dir or tempfile.gettempdir()
        self.timeout = timeout
        self._temp_dir: str | None = None
        self._original_cwd: str | None = None

    @contextmanager
    def isolate(self):
        self._temp_dir = tempfile.mkdtemp(prefix="sandbox_", dir=self.base_dir)
        self._original_cwd = os.getcwd()
        os.chdir(self._temp_dir)
        try: yield self._temp_dir
        finally:
            os.chdir(self._original_cwd)
            if self._temp_dir and os.path.exists(self._temp_dir):
                try: shutil.rmtree(self._temp_dir, ignore_errors=True)
                except: pass

    def run(self, command: list[str], timeout: int | None = None, env: dict | None = None) -> SandboxResult:
        timeout = timeout or self.timeout
        result = SandboxResult(exit_code=-1, stdout="", stderr="", timed_out=False)
        def target():
            try:
                proc = subprocess.run(command, capture_output=True, text=True,
                                      timeout=timeout, env=env or os.environ.copy(),
                                      cwd=self._temp_dir or os.getcwd())
                result.exit_code = proc.returncode; result.stdout = proc.stdout; result.stderr = proc.stderr
            except subprocess.TimeoutExpired:
                result.timed_out = True; result.stderr = f"Timeout after {timeout}s"
            except Exception as e:
                result.stderr = f"Sandbox error: {type(e).__name__}: {e}"
        t = threading.Thread(target=target); t.start(); t.join(timeout=timeout+5)
        if t.is_alive(): result.timed_out = True; result.stderr = f"Timeout after {timeout}s"
        return result


class DockerSandbox:
    """
    Docker 容器级沙箱 — 生产环境升级方案

    隔离对比：
        当前 Sandbox: 进程级 subprocess + 临时目录
        DockerSandbox:  容器级 --network=none --read-only --memory=256m

    使用:
        sandbox = DockerSandbox(image="python:3.11-slim")
        result = sandbox.run(["python", "-c", "print(2+2)"])

    面试话术: "当前项目的沙箱是进程级——线程+subprocess+临时目录，
    适合内网开发环境。生产环境会升级为 Docker 容器隔离：禁用网络、
    只读根文件系统、256MB 内存上限、禁止提权——这四条把攻击面从
    '能访问整个操作系统'压缩到 '只能在一个受限容器里跑几行代码'。"
    """

    def __init__(self, image: str = "alpine:latest", timeout: int = 60,
                 memory_limit: str = "256m", cpu_limit: str = "1"):
        self.image = image
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit

    def run(self, command: list[str]) -> SandboxResult:
        """在 Docker 容器中执行命令"""
        import subprocess as _sp
        docker_cmd = [
            "docker", "run", "--rm",
            "--network=none",           # ① 网络隔离
            "--read-only",              # ② 只读根文件系统
            f"--memory={self.memory_limit}",  # ③ 内存限制
            f"--cpus={self.cpu_limit}",       # ④ CPU 限制
            "--security-opt=no-new-privileges",  # ⑤ 禁止提权
            self.image,
        ] + (command if isinstance(command, list) else ["sh", "-c", command])

        try:
            proc = _sp.run(
                docker_cmd, capture_output=True, text=True,
                timeout=self.timeout,
            )
            return SandboxResult(
                exit_code=proc.returncode,
                stdout=proc.stdout, stderr=proc.stderr,
                timed_out=False,
            )
        except _sp.TimeoutExpired:
            return SandboxResult(exit_code=-1, stdout="", stderr="Docker timeout",
                                timed_out=True)
        except FileNotFoundError:
            return SandboxResult(exit_code=-1, stdout="", stderr="Docker not installed",
                                timed_out=False)
