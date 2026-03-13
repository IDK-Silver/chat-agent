"""Tests for chat_supervisor.process."""

import signal
from pathlib import Path

import pytest

from chat_supervisor import process
from chat_supervisor.process import ManagedProcess, resolve_cwd, topological_sort
from chat_supervisor.schema import ProcessConfig


class TestResolveCwd:
    def test_none_returns_base(self, tmp_path):
        assert resolve_cwd(None, tmp_path) == tmp_path

    def test_relative_path(self, tmp_path):
        result = resolve_cwd("sub/dir", tmp_path)
        assert result == (tmp_path / "sub" / "dir").resolve()

    def test_dot_relative(self, tmp_path):
        result = resolve_cwd("./copilot-proxy", tmp_path)
        assert result == (tmp_path / "copilot-proxy").resolve()

    def test_absolute_path(self, tmp_path):
        abs_path = "/opt/copilot-proxy"
        result = resolve_cwd(abs_path, tmp_path)
        assert result == Path(abs_path)


class TestTopologicalSort:
    def test_no_dependencies(self):
        procs = {
            "a": ProcessConfig(command=["a"]),
            "b": ProcessConfig(command=["b"]),
        }
        order = topological_sort(procs)
        assert set(order) == {"a", "b"}

    def test_linear_chain(self):
        procs = {
            "c": ProcessConfig(command=["c"], depends_on=["b"]),
            "b": ProcessConfig(command=["b"], depends_on=["a"]),
            "a": ProcessConfig(command=["a"]),
        }
        order = topological_sort(procs)
        assert order.index("a") < order.index("b") < order.index("c")

    def test_copilot_then_chatcli(self):
        procs = {
            "copilot-proxy": ProcessConfig(command=["uv", "run", "copilot-proxy"]),
            "chat-cli": ProcessConfig(
                command=["uv"], depends_on=["copilot-proxy"]
            ),
        }
        order = topological_sort(procs)
        assert order == ["copilot-proxy", "chat-cli"]

    def test_circular_dependency(self):
        procs = {
            "a": ProcessConfig(command=["a"], depends_on=["b"]),
            "b": ProcessConfig(command=["b"], depends_on=["a"]),
        }
        with pytest.raises(ValueError, match="Circular"):
            topological_sort(procs)

    def test_missing_dependency(self):
        procs = {
            "a": ProcessConfig(command=["a"], depends_on=["nonexistent"]),
        }
        with pytest.raises(ValueError, match="not defined"):
            topological_sort(procs)

    def test_disabled_process_skipped(self):
        procs = {
            "a": ProcessConfig(command=["a"], enabled=False),
            "b": ProcessConfig(command=["b"]),
        }
        order = topological_sort(procs)
        assert order == ["b"]


class TestProcessGroupSafety:
    def test_signal_pid_or_group_prefers_killpg(self, monkeypatch):
        calls: list[tuple[str, int, int]] = []
        monkeypatch.setattr(process, "_supports_process_group_kill", lambda: True)
        monkeypatch.setattr(process.os, "getpgid", lambda pid: pid)
        monkeypatch.setattr(process.os, "getpgrp", lambda: 999)
        monkeypatch.setattr(
            process.os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig))
        )
        monkeypatch.setattr(
            process.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig))
        )

        process._signal_pid_or_group(123, signal.SIGTERM)

        assert calls == [("killpg", 123, signal.SIGTERM)]

    def test_signal_pid_or_group_falls_back_to_pid_in_current_group(self, monkeypatch):
        calls: list[tuple[str, int, int]] = []
        monkeypatch.setattr(process, "_supports_process_group_kill", lambda: True)
        monkeypatch.setattr(process.os, "getpgid", lambda _pid: 777)
        monkeypatch.setattr(process.os, "getpgrp", lambda: 777)
        monkeypatch.setattr(
            process.os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig))
        )
        monkeypatch.setattr(
            process.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig))
        )

        process._signal_pid_or_group(123, signal.SIGTERM)

        assert calls == [("kill", 123, signal.SIGTERM)]

    def test_signal_pid_or_group_falls_back_to_pid_when_current_group_unknown(
        self, monkeypatch
    ):
        calls: list[tuple[str, int, int]] = []
        monkeypatch.setattr(process, "_supports_process_group_kill", lambda: True)
        monkeypatch.setattr(process.os, "getpgid", lambda _pid: 778)

        def raise_oserror():
            raise OSError("no current pgid")

        monkeypatch.setattr(process.os, "getpgrp", raise_oserror)
        monkeypatch.setattr(
            process.os, "killpg", lambda pid, sig: calls.append(("killpg", pid, sig))
        )
        monkeypatch.setattr(
            process.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig))
        )

        process._signal_pid_or_group(123, signal.SIGTERM)

        assert calls == [("kill", 123, signal.SIGTERM)]

    def test_cleanup_stale_kills_orphan_process_group(self, tmp_path, monkeypatch):
        cfg = ProcessConfig(command=["uv", "run", "chat-cli"])
        managed = ManagedProcess("chat-cli", cfg, tmp_path)
        pid_file = tmp_path / "logs" / "chat-cli.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("4242")

        monkeypatch.setattr(process, "_pid_is_alive", lambda _pid: False)
        pg_checks = iter([True, False])
        monkeypatch.setattr(
            process, "_process_group_is_alive", lambda _pid: next(pg_checks)
        )
        monkeypatch.setattr(process.time, "sleep", lambda _seconds: None)

        calls: list[tuple[int, int]] = []
        monkeypatch.setattr(
            process,
            "_signal_pid_or_group",
            lambda pid, sig: calls.append((pid, sig)),
        )

        managed.cleanup_stale()

        assert calls == [(4242, signal.SIGTERM)]
        assert not pid_file.exists()

    @pytest.mark.asyncio
    async def test_start_uses_start_new_session_on_posix(self, tmp_path, monkeypatch):
        cfg = ProcessConfig(command=["uv", "run", "chat-cli"])
        managed = ManagedProcess("chat-cli", cfg, tmp_path)

        monkeypatch.setattr(process, "_supports_process_group_kill", lambda: True)

        captured: dict[str, object] = {}

        class FakePopen:
            def __init__(self):
                self.pid = 999
                self.returncode = None

            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return FakePopen()

        monkeypatch.setattr(process.subprocess, "Popen", fake_popen)

        await managed.start()

        assert captured["command"] == ["uv", "run", "chat-cli"]
        kwargs = captured["kwargs"]
        assert isinstance(kwargs, dict)
        assert kwargs.get("start_new_session") is True

    @pytest.mark.asyncio
    async def test_start_can_preserve_foreground_tty(self, tmp_path, monkeypatch):
        cfg = ProcessConfig(command=["uv", "run", "chat-cli"], start_new_session=False)
        managed = ManagedProcess("chat-cli", cfg, tmp_path)

        monkeypatch.setattr(process, "_supports_process_group_kill", lambda: True)

        captured: dict[str, object] = {}

        class FakePopen:
            def __init__(self):
                self.pid = 1000
                self.returncode = None

            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return FakePopen()

        monkeypatch.setattr(process.subprocess, "Popen", fake_popen)

        await managed.start()

        assert captured["command"] == ["uv", "run", "chat-cli"]
        kwargs = captured["kwargs"]
        assert isinstance(kwargs, dict)
        assert "start_new_session" not in kwargs

    @pytest.mark.asyncio
    async def test_start_can_append_one_shot_args(self, tmp_path, monkeypatch):
        cfg = ProcessConfig(command=["uv", "run", "chat-cli"])
        managed = ManagedProcess("chat-cli", cfg, tmp_path)
        managed.queue_next_start_args(["--new"])

        captured: dict[str, object] = {}

        class FakePopen:
            def __init__(self):
                self.pid = 1001
                self.returncode = None

            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return FakePopen()

        monkeypatch.setattr(process.subprocess, "Popen", fake_popen)

        await managed.start()

        assert captured["command"] == ["uv", "run", "chat-cli", "--new"]

    @pytest.mark.asyncio
    async def test_stop_fallback_kills_managed_tree(self, tmp_path, monkeypatch):
        cfg = ProcessConfig(command=["uv", "run", "chat-cli"], shutdown_timeout=1)
        managed = ManagedProcess("chat-cli", cfg, tmp_path)

        class FakePopen:
            pid = 777
            returncode = None

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        managed._proc = FakePopen()  # type: ignore[assignment]

        async def fake_shutdown_via_api():
            return False

        monkeypatch.setattr(managed, "_shutdown_via_api", fake_shutdown_via_api)

        calls: list[tuple[int, int]] = []
        monkeypatch.setattr(
            process,
            "_signal_pid_or_group",
            lambda pid, sig: calls.append((pid, sig)),
        )

        stopped = await managed.stop()

        assert stopped is True
        assert calls[0] == (777, signal.SIGTERM)

    @pytest.mark.asyncio
    async def test_stop_fallback_without_new_session_signals_single_pid(
        self, tmp_path, monkeypatch
    ):
        cfg = ProcessConfig(
            command=["uv", "run", "chat-cli"],
            shutdown_timeout=1,
            start_new_session=False,
        )
        managed = ManagedProcess("chat-cli", cfg, tmp_path)

        class FakePopen:
            pid = 778
            returncode = None

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        managed._proc = FakePopen()  # type: ignore[assignment]

        async def fake_shutdown_via_api():
            return False

        monkeypatch.setattr(managed, "_shutdown_via_api", fake_shutdown_via_api)

        calls: list[tuple[int, int]] = []
        monkeypatch.setattr(
            process,
            "_signal_pid_or_group",
            lambda pid, sig: calls.append((pid, sig)),
        )

        stopped = await managed.stop()

        assert stopped is True
        assert calls == [(778, signal.SIGTERM)]
