"""FastAPI app for the monitoring web API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .cache import MetricsCache
from .pricing import fetch_pricing
from .settings import WebApiSettings
from .watcher import watch_sessions

logger = logging.getLogger(__name__)


class _WebSocketManager:
    """Tracks connected WebSocket clients and broadcasts messages."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


def create_app(settings: WebApiSettings) -> FastAPI:
    ws_manager = _WebSocketManager()
    cache_holder: dict[str, MetricsCache] = {}
    watcher_stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        # Startup: load pricing, build cache, start watcher
        pricing = await fetch_pricing(
            settings.pricing_url,
            settings.pricing_cache_path,
            settings.pricing_cache_ttl_hours,
        )
        cache = MetricsCache(settings.sessions_dir, pricing)
        cache.refresh_all()
        cache_holder["cache"] = cache
        logger.info(
            "Loaded %d sessions from %s", len(cache._files), settings.sessions_dir
        )

        # Start file watcher
        watcher_task = asyncio.create_task(
            watch_sessions(
                settings.sessions_dir,
                cache,
                ws_manager.broadcast,
                watcher_stop,
                soft_limit=settings.soft_limit_tokens,
            )
        )
        yield
        # Shutdown
        watcher_stop.set()
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass

    app = FastAPI(title="chat-web-api", docs_url=None, redoc_url=None, lifespan=lifespan)

    def _cache() -> MetricsCache:
        return cache_holder["cache"]

    # --- Health ---

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- REST API ---

    @app.get("/api/dashboard")
    async def dashboard(
        date_from: date | None = Query(None, alias="from"),
        date_to: date | None = Query(None, alias="to"),
    ) -> dict:
        today = date.today()
        df = date_from or today
        dt = date_to or today
        summary = _cache().get_dashboard(df, dt)
        return {
            "date_from": summary.date_from.isoformat(),
            "date_to": summary.date_to.isoformat(),
            "total_cost": summary.total_cost,
            "total_turns": summary.total_turns,
            "total_sessions": summary.total_sessions,
            "total_cache_read": summary.total_cache_read,
            "total_cache_write": summary.total_cache_write,
            "cache_hit_rate": summary.cache_hit_rate,
            "daily_costs": summary.daily_costs,
        }

    @app.get("/api/sessions")
    async def sessions(
        date_from: date | None = Query(None, alias="from"),
        date_to: date | None = Query(None, alias="to"),
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict:
        today = date.today()
        df = date_from or (today - timedelta(days=30))
        dt = date_to or today
        all_sessions = _cache().get_sessions_in_range(df, dt)
        page = all_sessions[offset : offset + limit]
        return {
            "sessions": [
                {
                    "session_id": s.session_id,
                    "status": s.status,
                    "created_at": s.created_at.isoformat(),
                    "updated_at": s.updated_at.isoformat(),
                    "turn_count": s.turn_count,
                    "total_cost": s.total_cost,
                    "cache_hit_rate": s.cache_hit_rate,
                    "peak_prompt_tokens": s.peak_prompt_tokens,
                }
                for s in page
            ],
            "total": len(all_sessions),
        }

    @app.get("/api/requests")
    async def all_requests(
        date_from: date | None = Query(None, alias="from"),
        date_to: date | None = Query(None, alias="to"),
        limit: int = Query(200, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict:
        today = date.today()
        df = date_from or (today - timedelta(days=30))
        dt = date_to or today
        all_reqs = _cache().get_all_requests(df, dt)
        page = all_reqs[offset : offset + limit]
        return {
            "requests": page,
            "total": len(all_reqs),
        }

    @app.get("/api/sessions/{session_id}")
    async def session_detail(session_id: str) -> dict:
        detail = _cache().get_session_detail(session_id)
        if detail is None:
            return {"error": "session not found"}
        return detail

    @app.get("/api/live")
    async def live() -> dict:
        status = _cache().get_live_status(settings.soft_limit_tokens)
        if status is None:
            return {"active": False}
        return status

    # --- WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws_manager.connect(ws)
        try:
            while True:
                # Keep connection alive; client can send pings
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            ws_manager.disconnect(ws)

    # --- Static files (Vue SPA) ---

    if settings.static_dir and (settings.static_dir / "index.html").exists():
        assets_dir = settings.static_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str) -> FileResponse:
            # Serve static files from public root (favicon.svg, icons.svg, etc.)
            candidate = settings.static_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(settings.static_dir / "index.html"))

    return app
