"""Tests for background shell_task tool."""

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

from chat_agent.tools.builtin.shell_task import (
    SHELL_TASK_DEFINITION,
    ShellTaskManager,
    create_shell_task,
)
from chat_agent.tools.executor import ShellExecutor


class _QueueStub:
    def __init__(self) -> None:
        self.items: list[object] = []
        self.event = threading.Event()

    def put(self, msg) -> None:
        self.items.append(msg)
        self.event.set()


class TestShellTaskDefinition:
    def test_name_and_params(self):
        assert SHELL_TASK_DEFINITION.name == "shell_task"
        assert "command" in SHELL_TASK_DEFINITION.parameters
        assert "timeout" in SHELL_TASK_DEFINITION.parameters
        assert SHELL_TASK_DEFINITION.required == ["command"]


class TestCreateShellTask:
    def test_dispatches_and_injects_result(self, tmp_path: Path):
        queue = _QueueStub()
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: tmp_path,
            agent_os_dir=tmp_path,
        )

        output = fn(command="echo hello")

        assert "[SHELL DISPATCHED]" in output
        assert queue.event.wait(2)
        msg = queue.items[0]
        assert msg.channel == "shell_task"
        assert msg.sender == "system"
        assert "[SHELL TASK RESULT]" in msg.content
        assert "Command: echo hello" in msg.content
        assert "hello" in msg.content

    def test_empty_command_returns_error(self, tmp_path: Path):
        queue = _QueueStub()
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: tmp_path,
            agent_os_dir=tmp_path,
        )

        output = fn(command="")

        assert "Error" in output
        assert not queue.event.is_set()

    def test_busy_when_concurrency_limit_reached(self, tmp_path: Path):
        queue = _QueueStub()
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: tmp_path,
            agent_os_dir=tmp_path,
            max_concurrent=1,
        )

        cmd = f'{sys.executable} -c "import time; time.sleep(0.5)"'
        first = fn(command=cmd)
        second = fn(command="echo later")

        assert "[SHELL DISPATCHED]" in first
        assert "[SHELL BUSY]" in second
        assert queue.event.wait(2)

    def test_shutdown_rejects_new_work(self, tmp_path: Path):
        queue = _QueueStub()
        manager = ShellTaskManager(max_concurrent=1)
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: tmp_path,
            agent_os_dir=tmp_path,
            manager=manager,
        )

        manager.shutdown()
        output = fn(command="echo hello")

        assert "[SHELL UNAVAILABLE]" in output
        assert not queue.event.is_set()

    def test_blocks_memory_write_commands(self, tmp_path: Path):
        queue = _QueueStub()
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: tmp_path,
            agent_os_dir=tmp_path,
        )

        output = fn(command="echo nope > memory/agent/recent.md")

        assert "Error: Direct memory writes via shell are blocked." in output
        assert not queue.event.is_set()

    def test_uses_dispatch_time_cwd_snapshot(self, tmp_path: Path):
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()

        foreground = ShellExecutor(agent_os_dir=tmp_path)
        foreground.execute(f"cd '{first}'")

        queue = _QueueStub()
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: foreground.cwd,
            agent_os_dir=tmp_path,
        )

        pending: list[tuple[object, tuple[object, ...]]] = []

        class _DeferredThread:
            def __init__(self, target=None, args=(), daemon=None, name=None):
                del daemon, name
                pending.append((target, args))

            def start(self):
                return None

        with patch("chat_agent.tools.builtin.shell_task.threading.Thread", _DeferredThread):
            output = fn(command="pwd")

        foreground.execute(f"cd '{second}'")
        target, args = pending[0]
        target(*args)

        assert "[SHELL DISPATCHED]" in output
        msg = queue.items[0]
        assert f"CWD: {first}" in msg.content
        assert str(first) in msg.content

    def test_shutdown_kills_active_background_process(self, tmp_path: Path):
        queue = _QueueStub()
        manager = ShellTaskManager(max_concurrent=1)
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: tmp_path,
            agent_os_dir=tmp_path,
            manager=manager,
        )
        pid_file = tmp_path / "shell.pid"

        output = fn(command=f"echo $$ > '{pid_file}'; sleep 60")

        assert "[SHELL DISPATCHED]" in output
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not pid_file.exists():
            time.sleep(0.05)
        assert pid_file.exists()

        pid = int(pid_file.read_text().strip())
        manager.shutdown()

        kill_deadline = time.monotonic() + 2
        while time.monotonic() < kill_deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"Background shell process {pid} survived shutdown")

        assert not queue.event.is_set()

    def test_shutdown_suppresses_result_injection(self, tmp_path: Path):
        queue = _QueueStub()
        manager = ShellTaskManager(max_concurrent=1)
        fn = create_shell_task(
            queue=queue,
            cwd_provider=lambda: tmp_path,
            agent_os_dir=tmp_path,
            manager=manager,
        )

        output = fn(
            command=(
                f'{sys.executable} -c "import pathlib,time; '
                f'pathlib.Path(r\'{tmp_path / "started"}\').write_text(\'1\'); '
                "time.sleep(60)\""
            )
        )

        assert "[SHELL DISPATCHED]" in output
        started = tmp_path / "started"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not started.exists():
            time.sleep(0.05)
        assert started.exists()

        manager.shutdown()

        assert not queue.event.wait(0.3)
