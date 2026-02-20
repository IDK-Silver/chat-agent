"""FastAPI supervisor control API."""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .process import ManagedProcess
from .scheduler import Scheduler
from .schema import SupervisorConfig
from .upgrade import pull_and_post, self_restart, snapshot_watch_paths

logger = logging.getLogger(__name__)


def create_supervisor_app(
    config: SupervisorConfig,
    scheduler: Scheduler,
    processes: dict[str, ManagedProcess],
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
        await scheduler.stop_all()
        scheduler.request_stop()
        return JSONResponse({"status": "shutting_down"})

    return app
