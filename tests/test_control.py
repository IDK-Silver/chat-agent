"""Tests for chat_agent.control (ControlServer FastAPI app)."""

import pytest
import httpx

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
