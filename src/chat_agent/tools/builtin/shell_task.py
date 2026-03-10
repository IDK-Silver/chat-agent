"""Background shell task tool."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ...llm.schema import ToolDefinition, ToolParameter
from ..executor import ShellExecutor
from ..security import is_memory_write_shell_command

logger = logging.getLogger(__name__)
_DEFAULT_SHUTDOWN_JOIN_TIMEOUT_SECONDS = 3.0

SHELL_TASK_DEFINITION = ToolDefinition(
    name="shell_task",
    description=(
        "Start a background non-interactive shell task and return immediately. "
        "Use this only when you can continue without the command output in this turn. "
        "The final result is delivered later as a [shell_task, from system] message."
    ),
    parameters={
        "command": ToolParameter(
            type="string",
            description=(
                "The non-interactive shell command to run in the background. "
                "Use execute_shell instead when you need the output in this turn."
            ),
        ),
        "timeout": ToolParameter(
            type="integer",
            description=(
                "Timeout in seconds for the background command. "
                "Clamped to at least the configured default; cannot lower it."
            ),
        ),
    },
    required=["command"],
)


class ShellTaskManager:
    """Own background shell task lifecycle for dispatch and shutdown."""

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        shutdown_join_timeout: float = _DEFAULT_SHUTDOWN_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        self._closing = threading.Event()
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._threads: set[threading.Thread] = set()
        self._shutdown_join_timeout = shutdown_join_timeout

    def is_closing(self) -> bool:
        """Return whether shutdown has started."""
        return self._closing.is_set()

    def try_acquire_slot(self) -> bool:
        """Reserve one background shell task slot."""
        return self._semaphore.acquire(blocking=False)

    def release_slot(self) -> None:
        """Release one background shell task slot."""
        self._semaphore.release()

    def start_thread(self, thread: threading.Thread) -> bool:
        """Start and track a background thread unless shutdown has started."""
        with self._lock:
            if self._closing.is_set():
                return False
            thread.start()
            self._threads.add(thread)
            return True

    def finish_thread(self, thread: threading.Thread) -> None:
        """Forget a completed background thread."""
        with self._lock:
            self._threads.discard(thread)

    def enqueue_if_open(self, queue, msg) -> bool:
        """Queue a result only while background shell tasks remain open."""
        with self._lock:
            if self._closing.is_set():
                return False
            queue.put(msg)
            return True

    def shutdown(self) -> None:
        """Stop accepting work and wait briefly for active tasks to exit."""
        with self._lock:
            self._closing.set()
            threads = list(self._threads)

        deadline = time.monotonic() + self._shutdown_join_timeout
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)


def create_shell_task(
    *,
    queue,
    cwd_provider: Callable[[], Path],
    agent_os_dir: Path,
    blacklist: list[str] | None = None,
    timeout: int = 30,
    export_env: list[str] | None = None,
    max_concurrent: int = 2,
    manager: ShellTaskManager | None = None,
) -> Callable[..., str]:
    """Create a queue-backed shell_task tool."""
    manager = manager or ShellTaskManager(max_concurrent=max_concurrent)
    default_timeout = timeout

    def shell_task(command: str = "", timeout: int | None = None, **kwargs) -> str:
        del kwargs
        if queue is None:
            return "Error: shell_task requires a queue-backed runtime."
        if not command:
            return "Error: command is required."
        if is_memory_write_shell_command(command, agent_os_dir=agent_os_dir):
            return "Error: Direct memory writes via shell are blocked. Use memory_edit."
        if manager.is_closing():
            return "[SHELL UNAVAILABLE] Background shell tasks are shutting down."
        if not manager.try_acquire_slot():
            return (
                "[SHELL BUSY] Too many background shell tasks are already running. "
                "Wait for a result before starting another one."
            )
        cwd = cwd_provider()
        thread_ref: list[threading.Thread | None] = [None]

        def _run_background(command: str, timeout_override: int | None, cwd: Path) -> None:
            from ...agent.schema import InboundMessage

            try:
                executor = ShellExecutor(
                    agent_os_dir=cwd,
                    blacklist=blacklist,
                    timeout=default_timeout,
                    export_env=export_env,
                    is_cancel_requested=manager.is_closing,
                )
                output = executor.execute(command, timeout=timeout_override)
                header = "[SHELL TASK ERROR]" if output.startswith("Error") else "[SHELL TASK RESULT]"
                body = output if output else "(no output)"
                msg = InboundMessage(
                    channel="shell_task",
                    content=(
                        f"{header}\n"
                        f"Command: {command}\n"
                        f"CWD: {cwd}\n\n"
                        f"{body}"
                    ),
                    priority=0,
                    sender="system",
                    metadata={
                        "shell_command": command,
                        "shell_cwd": str(cwd),
                    },
                )
                manager.enqueue_if_open(queue, msg)
            except Exception as e:
                logger.error("Background shell task error: %s", e)
                msg = InboundMessage(
                    channel="shell_task",
                    content=(
                        "[SHELL TASK ERROR]\n"
                        f"Command: {command}\n\n"
                        f"Error: {e}"
                    ),
                    priority=0,
                    sender="system",
                    metadata={"shell_command": command},
                )
                manager.enqueue_if_open(queue, msg)
            finally:
                thread = thread_ref[0]
                if thread is not None:
                    manager.finish_thread(thread)
                manager.release_slot()

        thread = threading.Thread(
            target=_run_background,
            args=(command, timeout, cwd),
            daemon=True,
            name="shell-task",
        )
        thread_ref[0] = thread
        try:
            if not manager.start_thread(thread):
                manager.release_slot()
                return "[SHELL UNAVAILABLE] Background shell tasks are shutting down."
        except Exception:
            manager.release_slot()
            raise
        return (
            "[SHELL DISPATCHED] Background shell task accepted. "
            "Result will be delivered as a [shell_task, from system] message."
        )

    return shell_task
