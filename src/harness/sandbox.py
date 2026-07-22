"""Sandbox isolation module"""
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
