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
    chat_cfg = ProcessConfig(
        command=["uv", "run", "chat-cli"],
        control_url="http://127.0.0.1:9001",
    )
    chat_proc = ManagedProcess("chat-cli", chat_cfg, Path.cwd())
    chat_proc.state = ProcessState.RUNNING
    chat_proc._proc = MagicMock(pid=4321)
    chat_proc.request_control = AsyncMock(
        return_value=(200, {"status": "new_session_requested"})
    )
    return {"test-proc": proc, "chat-cli": chat_proc}


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
async def test_restart_all_endpoint(transport, mock_scheduler):
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/restart")
    assert resp.status_code == 200
    assert resp.json() == {"status": "restarted"}
    mock_scheduler.restart_cycle.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_new_session_endpoint(transport, mock_processes):
    proc = mock_processes["chat-cli"]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/new-session")

    assert resp.status_code == 200
    assert resp.json() == {"status": "new_session_requested"}
    proc.request_control.assert_awaited_once_with("POST", "/session/new")


@pytest.mark.asyncio
async def test_reload_endpoint(transport, mock_processes):
    proc = mock_processes["chat-cli"]
    proc.request_control = AsyncMock(
        return_value=(200, {"status": "reload_requested"})
    )

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/reload")

    assert resp.status_code == 200
    assert resp.json() == {"status": "reload_requested"}
    proc.request_control.assert_awaited_once_with("POST", "/reload")


@pytest.mark.asyncio
async def test_new_session_endpoint_requires_running_chat_cli(
    mock_scheduler,
    mock_processes,
):
    mock_processes["chat-cli"].state = ProcessState.STOPPED
    app = create_supervisor_app(SupervisorConfig(), mock_scheduler, mock_processes)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/new-session")

    assert resp.status_code == 409
    assert resp.json() == {"error": "chat-cli is not running"}


@pytest.mark.asyncio
async def test_reload_endpoint_requires_running_chat_cli(
    mock_scheduler,
    mock_processes,
):
    mock_processes["chat-cli"].state = ProcessState.STOPPED
    app = create_supervisor_app(SupervisorConfig(), mock_scheduler, mock_processes)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/reload")

    assert resp.status_code == 409
    assert resp.json() == {"error": "chat-cli is not running"}
