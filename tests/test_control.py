"""Tests for chat_agent.control (ControlServer FastAPI app)."""

import pytest
import httpx

from chat_agent import control
from chat_agent.control import create_app


@pytest.fixture
def app():
    return create_app(shutdown_fn=lambda: None)


@pytest.fixture
def transport(app):
    return httpx.ASGITransport(app=app)


@pytest.mark.asyncio
async def test_health_returns_ok(transport):
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_shutdown_calls_fn():
    called = []
    app = create_app(shutdown_fn=lambda: called.append(True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/shutdown")
    assert resp.status_code == 200
    assert resp.json() == {"status": "shutting_down"}
    assert called == [True]


@pytest.mark.asyncio
async def test_shutdown_idempotent():
    count = []
    app = create_app(shutdown_fn=lambda: count.append(1))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/shutdown")
        await client.post("/shutdown")
    assert len(count) == 2


@pytest.mark.asyncio
async def test_new_session_calls_fn():
    called = []
    app = create_app(
        shutdown_fn=lambda: None,
        new_session_fn=lambda: called.append(True),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/session/new")
    assert resp.status_code == 200
    assert resp.json() == {"status": "new_session_requested"}
    assert called == [True]


@pytest.mark.asyncio
async def test_reload_calls_fn():
    called = []
    app = create_app(
        shutdown_fn=lambda: None,
        reload_fn=lambda: called.append(True),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/reload")
    assert resp.status_code == 200
    assert resp.json() == {"status": "reload_requested"}
    assert called == [True]


@pytest.mark.asyncio
async def test_new_session_returns_404_when_unavailable():
    app = create_app(shutdown_fn=lambda: None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/session/new")
    assert resp.status_code == 404
    assert resp.json() == {"error": "new-session is not supported"}


@pytest.mark.asyncio
async def test_reload_returns_404_when_unavailable():
    app = create_app(shutdown_fn=lambda: None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/reload")
    assert resp.status_code == 404
    assert resp.json() == {"error": "reload is not supported"}


def test_assert_control_slot_available_detects_existing_chat_cli(monkeypatch):
    monkeypatch.setattr(control, "_port_is_available", lambda _h, _p: False)
    monkeypatch.setattr(control, "_looks_like_control_api", lambda _h, _p: True)

    with pytest.raises(RuntimeError, match="another chat-cli instance is likely active"):
        control._assert_control_slot_available("127.0.0.1", 9001)


def test_assert_control_slot_available_detects_generic_port_conflict(monkeypatch):
    monkeypatch.setattr(control, "_port_is_available", lambda _h, _p: False)
    monkeypatch.setattr(control, "_looks_like_control_api", lambda _h, _p: False)

    with pytest.raises(RuntimeError, match="already in use"):
        control._assert_control_slot_available("127.0.0.1", 9001)
