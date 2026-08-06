"""Sandbox isolation module

进程级沙箱：subprocess + 线程 + 单一 timeout + 临时目录

⚠️ 重要：进程级沙箱（Sandbox）只隔离工作目录和进程环境，**不是安全隔离**。
   它不提供内存/CPU/网络/系统调用限制，也无法防御恶意代码。
   不要把不可信代码视为已被此沙箱安全隔离。

生产级升级方案：Docker 容器隔离
  docker run --rm --network=none --read-only \
    --memory=256m --cpus=1 --cap-drop=ALL --pids-limit=64 \
    --security-opt=no-new-privileges \
    -v /tmp/sandbox:/work:rw \
    alpine sh -c "command"

  对比：
  - 当前：进程级 subprocess，临时目录隔离
  - Docker：网络隔离 + 只读根FS + 内存限制 + capabilities/pids 限制
  - 面试话术见模块末尾
"""
from __future__ import annotations
import os, signal, shlex, shutil, tempfile, subprocess, threading
from contextlib import contextmanager
from pathlib import Path

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
    """进程级沙箱：临时目录 + 子进程 + 单层超时。

    注意：这不是安全隔离（见模块 docstring）。用于开发环境/内网工具链，
    防止命令意外污染宿主目录，而不是防御恶意代码。

    ⚠️ base_dir 必须远离用户主目录：
    - 若临时目录位于主目录树下（如 %TEMP%），git 等工具会向上遍历找到
      主目录的 .git，穿透隔离泄露敏感文件（.ssh/.aws 等）。
    - 默认改为项目内 .sandbox/（Git 已忽略该目录），git 向上找不到
      宿主仓库，且不影响主目录。
    """

    def __init__(self, base_dir: str | None = None, timeout: int = 60,
                 allowed_commands: set[str] | None = None):
        # 默认隔离目录：项目根 .sandbox/（不在用户主目录树下）
        if base_dir is None:
            try:
                _proj = Path(__file__).resolve().parent.parent.parent
                _sb = _proj / ".sandbox"
                _sb.mkdir(parents=True, exist_ok=True)
                base_dir = str(_sb)
            except Exception:
                base_dir = tempfile.gettempdir()
        self.base_dir = base_dir
        self.timeout = timeout
        # Per-thread isolation context; never chdir the parent process.
        self._local = threading.local()
        # Optional executable allowlist (basename of argv[0]). Empty = no filter.
        self.allowed_commands = set(allowed_commands or [])

    @property
    def _temp_dir(self) -> str | None:
        return getattr(self._local, "temp_dir", None)

    @contextmanager
    def isolate(self):
        """Yield a fresh temp dir. Per-call local state; no os.chdir anywhere.

        并发安全：状态保存在线程局部变量中，多个线程/多次调用互不干扰，
        绝不会改变整个进程的工作目录。
        """
        temp_dir = tempfile.mkdtemp(prefix="sandbox_", dir=self.base_dir)
        self._local.temp_dir = temp_dir
        try:
            yield temp_dir
        finally:
            self._local.temp_dir = None
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except OSError:
                    # Best-effort cleanup; catch only OSError so that
                    # KeyboardInterrupt/SystemExit are never swallowed.
                    pass

    def run(self, command, timeout: int | None = None, env: dict | None = None) -> SandboxResult:
        if timeout is None:
            timeout = self.timeout
        if timeout < 0:
            raise ValueError(f"timeout must be >= 0, got {timeout}")
        command = self._coerce_command(command)
        self._validate_command(command)
        env = self._build_env(env)

        temp_dir = self._temp_dir
        cleanup_temp = False
        if temp_dir is None:
            # 调用方未包 isolate() 时自动隔离，绝不回退到宿主 CWD。
            temp_dir = tempfile.mkdtemp(prefix="sandbox_", dir=self.base_dir)
            cleanup_temp = True

        # holder 是线程与调用方之间唯一的共享对象；返回的 SandboxResult
        # 总是在 join 之后重新构造，线程无法在 run() 返回后修改它。
        holder: dict = {}
        t = threading.Thread(
            target=self._run_in_thread,
            args=(command, env, temp_dir, timeout, holder),
            daemon=True,  # 绝不阻塞解释器退出
        )
        t.start()
        t.join(timeout=timeout + 5)

        if t.is_alive():
            # 子进程已被线程内 Popen timeout 显式 kill；此处只是兜底等待。
            # daemon 线程即使卡死也不会阻塞退出，且只写 holder，不影响返回值。
            return SandboxResult(exit_code=-1, stdout="",
                                 stderr=f"Timeout after {timeout}s", timed_out=True)

        if cleanup_temp and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except OSError:
                pass

        if "error" in holder:
            return SandboxResult(exit_code=-1, stdout="", stderr=holder["error"], timed_out=False)
        return SandboxResult(
            exit_code=holder.get("exit_code", -1),
            stdout=holder.get("stdout", ""),
            stderr=holder.get("stderr", ""),
            timed_out=holder.get("timed_out", False),
        )

    @staticmethod
    def _coerce_command(command):
        """把字符串命令安全地拆成参数列表，并校验类型。"""
        if isinstance(command, str):
            command = shlex.split(command)
        if not isinstance(command, (list, tuple)) or not command:
            raise TypeError("command must be a non-empty string or a list of strings")
        if not all(isinstance(c, str) and c for c in command):
            raise TypeError("command must contain only non-empty strings")
        return list(command)

    def _validate_command(self, command) -> None:
        """可选命令白名单：配置后仅允许白名单内的可执行文件名。"""
        if not self.allowed_commands:
            return
        exe = os.path.basename(command[0])
        if exe not in self.allowed_commands:
            raise ValueError(
                f"Command '{exe}' is not in the allowed allowlist: {sorted(self.allowed_commands)}"
            )

    @staticmethod
    def _build_env(env: dict | None) -> dict:
        """最小化、无密钥的环境：绝不把宿主 os.environ 传给沙箱进程。"""
        minimal = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", tempfile.gettempdir()),
            "TMPDIR": tempfile.gettempdir(),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        if env is None:
            return minimal
        merged = minimal.copy()
        merged.update(env)
        return merged

    @staticmethod
    def _kill_process_tree(proc) -> None:
        """杀掉整个进程组（含孙进程），失败时退化为只杀直接子进程。"""
        if hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                return
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass

    def _run_in_thread(self, command, env, temp_dir, timeout, holder) -> None:
        proc = None
        try:
            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=temp_dir,
            )
            if hasattr(os, "setsid"):
                popen_kwargs["start_new_session"] = True  # 独立进程组，便于整树 kill
            proc = subprocess.Popen(command, **popen_kwargs)
        except Exception as e:
            holder["error"] = f"Sandbox error: {type(e).__name__}: {e}"
            return
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            holder["exit_code"] = proc.returncode
            holder["stdout"] = stdout
            holder["stderr"] = stderr
            holder["timed_out"] = False
        except subprocess.TimeoutExpired:
            # 单一超时层：先显式 kill 进程树，再 reap 输出，保证子进程被终止。
            self._kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate()
            except Exception:
                stdout, stderr = "", ""
            holder["exit_code"] = proc.returncode
            holder["stdout"] = stdout
            holder["stderr"] = stderr
            holder["timed_out"] = True


class DockerSandbox:
    """
    Docker 容器级沙箱 — 生产环境升级方案

    隔离对比：
        当前 Sandbox: 进程级 subprocess + 临时目录（非安全隔离）
        DockerSandbox:  容器级 --network=none --read-only --cap-drop=ALL
                        --pids-limit --ulimit --tmpfs /tmp --memory=256m

    使用:
        sandbox = DockerSandbox(image="python:3.11-slim")
        result = sandbox.run(["python", "-c", "print(2+2)"], cwd="/tmp/mywork")

    面试话术: "当前项目的沙箱是进程级——线程+subprocess+临时目录，
    适合内网开发环境。生产环境会升级为 Docker 容器隔离：禁用网络、
    只读根文件系统、256MB 内存上限、禁止提权——这四条把攻击面从
    '能访问整个操作系统'压缩到 '只能在一个受限容器里跑几行代码'。"
    """

    def __init__(self, image: str = "alpine:3.20", timeout: int = 60,
                 memory_limit: str = "256m", cpu_limit: str = "1"):
        self.image = image          # 固定版本标签，避免浮动 tag 供应链风险
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit

    def run(self, command, timeout: int | None = None,
            env: dict | None = None, cwd: str | None = None) -> SandboxResult:
        """在 Docker 容器中执行命令（仅接受 list 形式，禁止 shell 注入）。

        输出写入临时文件而非内存，避免长命令无界占用内存。
        """
        import subprocess as _sp
        if timeout is None:
            timeout = self.timeout
        if timeout < 0:
            raise ValueError(f"timeout must be >= 0, got {timeout}")
        command = self._validate_command(command)

        workdir = cwd
        cleanup_workdir = False
        if workdir is None:
            workdir = tempfile.mkdtemp(prefix="docker_sandbox_")
            cleanup_workdir = True
        else:
            os.makedirs(workdir, exist_ok=True)
            workdir = os.path.abspath(workdir)

        env_vars = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "TMPDIR": "/tmp",
        }
        env_vars.update(env or {})

        docker_cmd = [
            "docker", "run", "--rm",
            "--network=none",                     # ① 网络隔离
            "--read-only",                        # ② 只读根文件系统
            "--cap-drop=ALL",                     # ③ 丢弃所有 Linux capabilities
            "--pids-limit=64",                    # ④ 限制进程/线程数，防 fork 炸弹
            "--ulimit", "nofile=64:64",           # ⑤ 限制打开文件数
            "--tmpfs", "/tmp:rw,size=64m,nosuid,nodev",  # ⑥ 可写临时目录
            f"--memory={self.memory_limit}",      # ⑦ 内存限制
            f"--cpus={self.cpu_limit}",           # ⑧ CPU 限制
            "--security-opt=no-new-privileges",   # ⑨ 禁止提权
            "--workdir", "/work",
            "-v", f"{workdir}:/work:rw",          # ⑩ 工作目录映射（读写）
        ]
        for k, v in env_vars.items():
            docker_cmd += ["-e", f"{k}={v}"]
        docker_cmd += [self.image] + command

        stdout_fd, stdout_path = tempfile.mkstemp(prefix="docker_stdout_")
        stderr_fd, stderr_path = tempfile.mkstemp(prefix="docker_stderr_")
        os.close(stdout_fd); os.close(stderr_fd)
        try:
            with open(stdout_path, "wb") as so, open(stderr_path, "wb") as se:
                proc = _sp.run(docker_cmd, stdout=so, stderr=se, timeout=timeout)
            with open(stdout_path, "rb") as f:
                stdout = f.read().decode("utf-8", errors="replace")
            with open(stderr_path, "rb") as f:
                stderr = f.read().decode("utf-8", errors="replace")
            return SandboxResult(exit_code=proc.returncode, stdout=stdout,
                                 stderr=stderr, timed_out=False)
        except _sp.TimeoutExpired:
            return SandboxResult(exit_code=-1, stdout="",
                                 stderr=f"Docker timeout after {timeout}s", timed_out=True)
        except FileNotFoundError:
            return SandboxResult(exit_code=-1, stdout="", stderr="Docker not installed",
                                 timed_out=False)
        finally:
            for path in (stdout_path, stderr_path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            if cleanup_workdir:
                shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _validate_command(command):
        """只接受 list 命令；拒绝字符串以避免 sh -c 任意 shell 注入。"""
        if isinstance(command, str):
            raise TypeError(
                "DockerSandbox.run requires a list of arguments "
                "(e.g. ['python', '-c', 'print(1)']); string commands are rejected "
                "to avoid shell injection."
            )
        if not isinstance(command, (list, tuple)) or not command:
            raise TypeError("command must be a non-empty list of strings")
        if not all(isinstance(c, str) and c for c in command):
            raise TypeError("command must contain only non-empty strings")
        return list(command)
