"""Control API server for external process management.

Runs a FastAPI app in a daemon thread via uvicorn, exposing
/health and /shutdown endpoints for supervisor integration.
"""

import logging
import threading
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

logger = logging.getLogger(__name__)


def create_app(shutdown_fn: Callable[[], None]) -> FastAPI:
    """Build FastAPI app with shutdown/health endpoints."""
    app = FastAPI(title="chat-agent-control", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/shutdown")
    def shutdown() -> JSONResponse:
        shutdown_fn()
        return JSONResponse({"status": "shutting_down"})

    return app


class ControlServer:
    """Run the control API in a daemon thread."""

    def __init__(self, host: str, port: int, shutdown_fn: Callable[[], None]):
        self._host = host
        self._port = port
        self._app = create_app(shutdown_fn)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=server.run,
            daemon=True,
            name="control-api",
        )
        self._thread.start()
        logger.info("Control API started on %s:%d", self._host, self._port)
