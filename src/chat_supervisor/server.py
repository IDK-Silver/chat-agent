"""FastAPI supervisor control API."""

import asyncio
from collections.abc import Awaitable, Callable
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx

from .process import ManagedProcess, ProcessState
from .scheduler import Scheduler
from .schema import SupervisorConfig
from .upgrade import pull_and_post, self_restart, snapshot_watch_paths

logger = logging.getLogger(__name__)


def create_supervisor_app(
    config: SupervisorConfig,
    scheduler: Scheduler,
    processes: dict[str, ManagedProcess],
    shutdown_supervisor: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Build the supervisor FastAPI application."""
    app = FastAPI(title="chat-supervisor", docs_url=None, redoc_url=None)

    @app.get("/status")
    def status() -> dict:
        return {
            name: {
                "state": proc.state.value,
                "pid": proc.pid,
            }
            for name, proc in processes.items()
        }

    @app.post("/restart/{name}")
    async def restart(name: str) -> JSONResponse:
        if name not in processes:
            return JSONResponse(
                {"error": f"Unknown process: {name}"}, status_code=404
            )
        proc = processes[name]
        await proc.stop()
        await proc.start()
        return JSONResponse({"status": "restarted", "pid": proc.pid})

    @app.post("/restart")
    async def restart_all() -> JSONResponse:
        await scheduler.restart_cycle()
        return JSONResponse({"status": "restarted"})

    @app.post("/new-session")
    async def new_session() -> JSONResponse:
        proc = processes.get("chat-cli")
        if proc is None:
            return JSONResponse(
                {"error": "chat-cli is not managed by this supervisor"},
                status_code=404,
            )
        if proc.state != ProcessState.RUNNING:
            return JSONResponse(
                {"error": "chat-cli is not running"},
                status_code=409,
            )
        try:
            status_code, payload = await proc.request_control("POST", "/session/new")
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": f"chat-cli control API unreachable: {exc}"},
                status_code=503,
            )
        return JSONResponse(payload, status_code=status_code)

    @app.post("/upgrade")
    async def upgrade() -> JSONResponse:
        """git pull + post_pull commands + restart cycle."""
        watch_before = snapshot_watch_paths(config.upgrade.self_watch_paths)

        ok, err = pull_and_post(config.upgrade)
        if not ok:
            return JSONResponse({"error": err}, status_code=500)

        await scheduler.restart_cycle()

        watch_after = snapshot_watch_paths(config.upgrade.self_watch_paths)
        needs_self_restart = watch_before != watch_after

        if needs_self_restart:
            logger.info("Self-watch paths changed; scheduling self-restart")
            asyncio.get_event_loop().call_later(1.0, self_restart)

        return JSONResponse({
            "status": "upgraded",
            "self_restart": needs_self_restart,
        })

    @app.post("/shutdown")
    async def shutdown() -> JSONResponse:
        if shutdown_supervisor is not None:
            await shutdown_supervisor()
        else:
            await scheduler.stop_all()
            scheduler.request_stop()
        return JSONResponse({"status": "shutting_down"})

    return app
