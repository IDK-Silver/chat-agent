"""Managed subprocess wrapper with lifecycle control."""

import asyncio
import logging
import os
import signal
import subprocess
import time
from typing import Any
from enum import Enum
from io import TextIOWrapper
from pathlib import Path

import httpx

from .schema import ProcessConfig

logger = logging.getLogger(__name__)

_MAX_CRASH_COUNT = 5
_BACKOFF_BASE = 2.0  # seconds
_BACKOFF_MAX = 60.0  # seconds


def _supports_process_group_kill() -> bool:
    """Return True when process-group signaling is available."""
    return os.name != "nt"


def _pid_is_alive(pid: int) -> bool:
    """Best-effort liveness check for a single PID."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_group_is_alive(pgid: int) -> bool:
    """Best-effort liveness check for a POSIX process group."""
    if not _supports_process_group_kill():
        return False
    try:
        os.killpg(pgid, 0)
    except OSError:
        return False
    return True


def _signal_pid_or_group(pid: int, sig: int) -> None:
    """Signal a detached child group first, else fall back to a single PID."""
    if _supports_process_group_kill():
        try:
            pgid = os.getpgid(pid)
        except OSError:
            pgid = None
        if pgid is not None:
            try:
                current_pgid = os.getpgrp()
            except OSError:
                current_pgid = None
            if pgid > 0 and current_pgid is not None and pgid != current_pgid:
                try:
                    os.killpg(pgid, sig)
                    return
                except OSError:
                    pass
    os.kill(pid, sig)


class ProcessState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"


class ManagedProcess:
    """Wraps a child process with lifecycle management."""

    def __init__(self, name: str, config: ProcessConfig, base_cwd: Path):
        self.name = name
        self.config = config
        self.state = ProcessState.STOPPED
        self._proc: subprocess.Popen | None = None
        self._cwd = resolve_cwd(config.cwd, base_cwd)
        self._log_file: TextIOWrapper | None = None
        self._log_dir = base_cwd / "logs"
        self._crash_count = 0
        self._next_restart_at = 0.0  # monotonic time

    def cleanup_stale(self) -> None:
        """Kill leftover process from a previous supervisor run (via PID file)."""
        pid_file = self._pid_file_path()
        if not pid_file.is_file():
            return
        try:
            old_pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pid_file.unlink(missing_ok=True)
            return

        pid_alive = _pid_is_alive(old_pid)
        pg_alive = _process_group_is_alive(old_pid)
        if not pid_alive and not pg_alive:
            pid_file.unlink(missing_ok=True)
            return

        target_label = "process group" if pg_alive else "process"
        logger.warning("%s: killing stale %s (%d)", self.name, target_label, old_pid)
        try:
            _signal_pid_or_group(old_pid, signal.SIGTERM)
            # Brief wait for graceful exit
            for _ in range(10):
                time.sleep(0.5)
                if not _pid_is_alive(old_pid) and not _process_group_is_alive(old_pid):
                    break
            else:
                _signal_pid_or_group(old_pid, signal.SIGKILL)
        except OSError:
            pass
        pid_file.unlink(missing_ok=True)

    async def start(self) -> None:
        """Start the child process."""
        if self._proc is not None and self._proc.poll() is None:
            logger.warning("%s: already running (pid %d)", self.name, self._proc.pid)
            return

        self.state = ProcessState.STARTING
        env = {**os.environ, **self.config.env}

        stdout_target = None
        stderr_target = None
        if self.config.log_output:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_dir / f"{self.name}.log"
            self._log_file = open(log_path, "a")  # noqa: SIM115
            stdout_target = self._log_file
            stderr_target = subprocess.STDOUT
            logger.info("%s: output redirected to %s", self.name, log_path)

        popen_kwargs: dict[str, object] = {
            "cwd": str(self._cwd),
            "env": env,
            "stdout": stdout_target,
            "stderr": stderr_target,
        }
        if _supports_process_group_kill() and self.config.start_new_session:
            # Put the managed command (and its descendants) in a dedicated process
            # group so fallback shutdown can reliably kill wrappers like `uv run`.
            popen_kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(self.config.command, **popen_kwargs)
        logger.info("%s: started (pid %d)", self.name, self._proc.pid)
        self._write_pid(self._proc.pid)

        if self.config.startup_delay > 0:
            await asyncio.sleep(self.config.startup_delay)

        if self._proc.poll() is None:
            self.state = ProcessState.RUNNING
            self._crash_count = 0
        else:
            self.state = ProcessState.CRASHED
            self._record_crash()
            logger.error(
                "%s: exited immediately with code %d",
                self.name,
                self._proc.returncode,
            )

    async def wait_healthy(self) -> bool:
        """Poll health_check_url until 200 or timeout.

        Returns True if healthy, False if timed out.
        Skipped if health_check_url is not configured.
        """
        url = self.config.health_check_url
        if not url:
            return True

        deadline = time.monotonic() + self.config.health_check_timeout
        interval = self.config.health_check_interval

        logger.info("%s: waiting for health check at %s", self.name, url)
        async with httpx.AsyncClient() as client:
            while time.monotonic() < deadline:
                try:
                    resp = await client.get(url, timeout=5.0)
                    if resp.status_code == 200:
                        logger.info("%s: health check passed", self.name)
                        return True
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                await asyncio.sleep(interval)

        logger.error(
            "%s: health check timed out after %.0fs",
            self.name, self.config.health_check_timeout,
        )
        return False

    def _close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _cleanup_stop(self) -> None:
        """Shared cleanup after process stops."""
        self._close_log()
        self._remove_pid()

    async def stop(self) -> bool:
        """Gracefully stop the process. Returns True if stopped cleanly."""
        if self._proc is None or self._proc.poll() is not None:
            self.state = ProcessState.STOPPED
            self._cleanup_stop()
            return True

        self.state = ProcessState.STOPPING

        # Try control URL first (HTTP graceful shutdown)
        if self.config.control_url:
            if await self._shutdown_via_api():
                self._cleanup_stop()
                return True

        # Fallback: terminate the managed process tree (wrapper + child).
        self._terminate_tree()
        try:
            self._proc.wait(timeout=self.config.shutdown_timeout)
            self.state = ProcessState.STOPPED
            self._cleanup_stop()
            logger.info("%s: terminated cleanly", self.name)
            return True
        except subprocess.TimeoutExpired:
            self._kill_tree()
            self._proc.wait(timeout=5)
            self.state = ProcessState.STOPPED
            self._cleanup_stop()
            logger.warning("%s: killed after timeout", self.name)
            return False

    async def _shutdown_via_api(self) -> bool:
        """POST /shutdown to the process control API."""
        try:
            status_code, _payload = await self.request_control("POST", "/shutdown")
            if status_code != 200:
                logger.warning("%s: control API returned %d", self.name, status_code)
                return False
            logger.info("%s: shutdown request accepted", self.name)
        except Exception as e:
            logger.warning(
                "%s: control API unreachable (%s), falling back to terminate",
                self.name,
                e,
            )
            return False

        # Wait for process to exit
        deadline = time.monotonic() + self.config.shutdown_timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                self.state = ProcessState.STOPPED
                return True
            await asyncio.sleep(1)

        logger.warning("%s: did not exit after API shutdown", self.name)
        return False

    async def request_control(
        self,
        method: str,
        path: str,
        *,
        timeout: float = 10.0,
    ) -> tuple[int, Any]:
        """Send a request to the managed process control API."""
        if not self.config.control_url:
            raise RuntimeError(f"{self.name} has no control_url configured")
        url = f"{self.config.control_url}{path}"
        async with httpx.AsyncClient() as client:
            resp = await client.request(method, url, timeout=timeout)
        try:
            payload: Any = resp.json()
        except ValueError:
            payload = {"raw": resp.text}
        return resp.status_code, payload

    def check_health(self) -> bool:
        """Return True if process is running."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def _terminate_tree(self) -> None:
        if self._proc is None:
            return
        try:
            _signal_pid_or_group(self._proc.pid, signal.SIGTERM)
        except OSError:
            pass

    def _kill_tree(self) -> None:
        if self._proc is None:
            return
        try:
            _signal_pid_or_group(self._proc.pid, signal.SIGKILL)
        except OSError:
            pass

    def detect_crash(self) -> bool:
        """Check if the process crashed since last check."""
        if self._proc is None:
            return False
        if self.state == ProcessState.RUNNING and self._proc.poll() is not None:
            self.state = ProcessState.CRASHED
            self._record_crash()
            self._close_log()
            logger.error(
                "%s: crashed with exit code %d",
                self.name,
                self._proc.returncode,
            )
            return True
        return False

    def should_restart(self) -> bool:
        """Check if auto-restart should proceed (respects backoff)."""
        if self._crash_count >= _MAX_CRASH_COUNT:
            logger.warning(
                "%s: suppressed auto-restart (%d consecutive crashes, max %d)",
                self.name, self._crash_count, _MAX_CRASH_COUNT,
            )
            return False
        now = time.monotonic()
        if now < self._next_restart_at:
            return False
        return True

    def reset_crash_count(self) -> None:
        """Reset crash counter (called on intentional restart cycle)."""
        self._crash_count = 0
        self._next_restart_at = 0.0

    # -- PID file helpers --

    def _pid_file_path(self) -> Path:
        return self._log_dir / f"{self.name}.pid"

    def _write_pid(self, pid: int) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._pid_file_path().write_text(str(pid))

    def _remove_pid(self) -> None:
        self._pid_file_path().unlink(missing_ok=True)

    def _record_crash(self) -> None:
        self._crash_count += 1
        delay = min(_BACKOFF_BASE * (2 ** (self._crash_count - 1)), _BACKOFF_MAX)
        self._next_restart_at = time.monotonic() + delay
        logger.info(
            "%s: crash #%d, next restart in %.0fs",
            self.name, self._crash_count, delay,
        )

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None


def resolve_cwd(cwd: str | None, base: Path) -> Path:
    """Resolve working directory: None=base, relative=base/cwd, absolute=as-is."""
    if cwd is None:
        return base
    p = Path(cwd)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def topological_sort(processes: dict[str, ProcessConfig]) -> list[str]:
    """Return enabled process names in dependency order (startup order).

    Processes with no dependencies come first.
    Raises ValueError on circular dependencies or missing references.
    """
    visited: set[str] = set()
    order: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"Circular dependency involving {name}")
        visiting.add(name)
        for dep in processes[name].depends_on:
            if dep not in processes:
                raise ValueError(
                    f"{name} depends on {dep} which is not defined"
                )
            visit(dep)
        visiting.discard(name)
        visited.add(name)
        order.append(name)

    for name in processes:
        if processes[name].enabled:
            visit(name)

    return order
