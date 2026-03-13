"""Tests for chat_supervisor.scheduler."""

import pytest
from unittest.mock import AsyncMock
from pathlib import Path

from chat_supervisor.process import ManagedProcess, ProcessState
from chat_supervisor.scheduler import Scheduler
from chat_supervisor.schema import ProcessConfig, SupervisorConfig


def _make_scheduler(
    proc_configs: dict[str, ProcessConfig],
    processes: dict[str, ManagedProcess],
    interval_hours: int | None = None,
) -> Scheduler:
    config = SupervisorConfig.model_validate({
        "processes": {n: c.model_dump() for n, c in proc_configs.items()},
        "restart": {"interval_hours": interval_hours},
    })
    return Scheduler(config, processes)


class TestStartStopOrder:
    @pytest.mark.asyncio
    async def test_start_all_in_dependency_order(self):
        cfg_a = ProcessConfig(command=["a"])
        cfg_b = ProcessConfig(command=["b"], depends_on=["a"])
        proc_a = ManagedProcess("a", cfg_a, Path.cwd())
        proc_b = ManagedProcess("b", cfg_b, Path.cwd())
        proc_a.start = AsyncMock()
        proc_b.start = AsyncMock()

        scheduler = _make_scheduler(
            {"a": cfg_a, "b": cfg_b},
            {"a": proc_a, "b": proc_b},
        )
        await scheduler.start_all()

        # Both started
        proc_a.start.assert_awaited_once()
        proc_b.start.assert_awaited_once()

        # a before b (check call order)
        assert proc_a.start.await_count == 1
        assert proc_b.start.await_count == 1

    @pytest.mark.asyncio
    async def test_start_all_aborts_on_failed_health_check(self):
        cfg_a = ProcessConfig(command=["a"], health_check_url="http://a.test/health")
        cfg_b = ProcessConfig(command=["b"], depends_on=["a"])
        proc_a = ManagedProcess("a", cfg_a, Path.cwd())
        proc_b = ManagedProcess("b", cfg_b, Path.cwd())
        proc_a.start = AsyncMock()
        proc_a.wait_healthy = AsyncMock(return_value=False)
        proc_b.start = AsyncMock()

        scheduler = _make_scheduler(
            {"a": cfg_a, "b": cfg_b},
            {"a": proc_a, "b": proc_b},
        )

        with pytest.raises(RuntimeError, match="a failed health check"):
            await scheduler.start_all()

        proc_a.start.assert_awaited_once()
        proc_b.start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_all_in_reverse_order(self):
        cfg_a = ProcessConfig(command=["a"])
        cfg_b = ProcessConfig(command=["b"], depends_on=["a"])
        proc_a = ManagedProcess("a", cfg_a, Path.cwd())
        proc_b = ManagedProcess("b", cfg_b, Path.cwd())
        proc_a.state = ProcessState.RUNNING
        proc_b.state = ProcessState.RUNNING
        proc_a.stop = AsyncMock()
        proc_b.stop = AsyncMock()

        scheduler = _make_scheduler(
            {"a": cfg_a, "b": cfg_b},
            {"a": proc_a, "b": proc_b},
        )
        await scheduler.stop_all()

        proc_a.stop.assert_awaited_once()
        proc_b.stop.assert_awaited_once()


class TestRestartCycle:
    @pytest.mark.asyncio
    async def test_cycle_stops_participants_first(self):
        """join_restart_cycle processes should be stopped before others."""
        cfg_dep = ProcessConfig(command=["dep"])
        cfg_main = ProcessConfig(
            command=["main"],
            depends_on=["dep"],
            join_restart_cycle=True,
        )
        proc_dep = ManagedProcess("dep", cfg_dep, Path.cwd())
        proc_main = ManagedProcess("main", cfg_main, Path.cwd())
        proc_dep.state = ProcessState.RUNNING
        proc_main.state = ProcessState.RUNNING

        stop_order = []
        async def mock_stop_dep():
            stop_order.append("dep")
            proc_dep.state = ProcessState.STOPPED
            return True
        async def mock_stop_main():
            stop_order.append("main")
            proc_main.state = ProcessState.STOPPED
            return True

        proc_dep.stop = mock_stop_dep
        proc_main.stop = mock_stop_main
        proc_dep.start = AsyncMock()
        proc_main.start = AsyncMock()

        scheduler = _make_scheduler(
            {"dep": cfg_dep, "main": cfg_main},
            {"dep": proc_dep, "main": proc_main},
        )
        await scheduler.restart_cycle()

        # main (cycle participant) should be stopped before dep
        assert stop_order[0] == "main"

    @pytest.mark.asyncio
    async def test_cycle_skipped_if_already_cycling(self):
        cfg = ProcessConfig(command=["a"])
        proc = ManagedProcess("a", cfg, Path.cwd())
        proc.start = AsyncMock()
        proc.stop = AsyncMock()

        scheduler = _make_scheduler({"a": cfg}, {"a": proc})
        scheduler._cycling = True

        await scheduler.restart_cycle()
        proc.stop.assert_not_awaited()


class TestRequestStop:
    def test_request_stop_sets_flag(self):
        scheduler = _make_scheduler({}, {})
        scheduler._running = True
        scheduler.request_stop()
        assert scheduler._running is False
