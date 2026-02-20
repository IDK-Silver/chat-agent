"""Managed subprocess wrapper with lifecycle control."""

import asyncio
import logging
import os
import subprocess
import time
from enum import Enum
from io import TextIOWrapper
from pathlib import Path

import httpx

from .schema import ProcessConfig

logger = logging.getLogger(__name__)


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

        self._proc = subprocess.Popen(
            self.config.command,
            cwd=str(self._cwd),
            env=env,
            stdout=stdout_target,
            stderr=stderr_target,
        )
        logger.info("%s: started (pid %d)", self.name, self._proc.pid)

        if self.config.startup_delay > 0:
            await asyncio.sleep(self.config.startup_delay)

        if self._proc.poll() is None:
            self.state = ProcessState.RUNNING
        else:
            self.state = ProcessState.CRASHED
            logger.error(
                "%s: exited immediately with code %d",
                self.name,
                self._proc.returncode,
            )

    def _close_log(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    async def stop(self) -> bool:
        """Gracefully stop the process. Returns True if stopped cleanly."""
        if self._proc is None or self._proc.poll() is not None:
            self.state = ProcessState.STOPPED
            self._close_log()
            return True

        self.state = ProcessState.STOPPING

        # Try control URL first (HTTP graceful shutdown)
        if self.config.control_url:
            if await self._shutdown_via_api():
                self._close_log()
                return True

        # Fallback: terminate
        self._proc.terminate()
        try:
            self._proc.wait(timeout=self.config.shutdown_timeout)
            self.state = ProcessState.STOPPED
            self._close_log()
            logger.info("%s: terminated cleanly", self.name)
            return True
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
            self.state = ProcessState.STOPPED
            self._close_log()
            logger.warning("%s: killed after timeout", self.name)
            return False

    async def _shutdown_via_api(self) -> bool:
        """POST /shutdown to the process control API."""
        url = f"{self.config.control_url}/shutdown"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, timeout=10)
                if resp.status_code != 200:
                    logger.warning(
                        "%s: control API returned %d", self.name, resp.status_code
                    )
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

    def check_health(self) -> bool:
        """Return True if process is running."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def detect_crash(self) -> bool:
        """Check if the process crashed since last check."""
        if self._proc is None:
            return False
        if self.state == ProcessState.RUNNING and self._proc.poll() is not None:
            self.state = ProcessState.CRASHED
            logger.error(
                "%s: crashed with exit code %d",
                self.name,
                self._proc.returncode,
            )
            return True
        return False

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
