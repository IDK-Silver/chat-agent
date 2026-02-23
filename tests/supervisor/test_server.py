"""Tests for chat_supervisor.server (FastAPI supervisor API)."""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from chat_supervisor.process import ManagedProcess, ProcessState
from chat_supervisor.scheduler import Scheduler
from chat_supervisor.schema import ProcessConfig, SupervisorConfig
from chat_supervisor.server import create_supervisor_app


@pytest.fixture
def mock_processes():
    cfg = ProcessConfig(command=["echo", "test"])
    proc = ManagedProcess("test-proc", cfg, Path.cwd())
    proc.state = ProcessState.RUNNING
    proc._proc = MagicMock(pid=1234)
    return {"test-proc": proc}


@pytest.fixture
def mock_scheduler(mock_processes):
    config = SupervisorConfig.model_validate({
        "processes": {"test-proc": {"command": ["echo", "test"]}},
    })
    scheduler = Scheduler(config, mock_processes)
    scheduler.stop_all = AsyncMock()
    scheduler.restart_cycle = AsyncMock()
    return scheduler


@pytest.fixture
def app(mock_scheduler, mock_processes):
    config = SupervisorConfig()
    return create_supervisor_app(config, mock_scheduler, mock_processes)


@pytest.fixture
def transport(app):
    return httpx.ASGITransport(app=app)


@pytest.mark.asyncio
async def test_status_endpoint(transport, mock_processes):
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "test-proc" in data
    assert data["test-proc"]["state"] == "running"
    assert data["test-proc"]["pid"] == 1234


@pytest.mark.asyncio
async def test_restart_endpoint(transport, mock_processes):
    proc = mock_processes["test-proc"]
    proc.stop = AsyncMock()
    proc.start = AsyncMock()

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/restart/test-proc")
    assert resp.status_code == 200
    proc.stop.assert_awaited_once()
    proc.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_unknown_process(transport):
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/restart/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_shutdown_endpoint(transport, mock_scheduler):
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/shutdown")
    assert resp.status_code == 200
    assert resp.json()["status"] == "shutting_down"
    mock_scheduler.stop_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_endpoint_uses_full_supervisor_callback(
    mock_scheduler,
    mock_processes,
):
    callback = AsyncMock()
    app = create_supervisor_app(
        SupervisorConfig(),
        mock_scheduler,
        mock_processes,
        shutdown_supervisor=callback,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/shutdown")
    assert resp.status_code == 200
    assert resp.json()["status"] == "shutting_down"
    callback.assert_awaited_once()
    mock_scheduler.stop_all.assert_not_awaited()
