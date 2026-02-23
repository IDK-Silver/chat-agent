"""chat-supervisor entry point."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

import uvicorn

from .config import load_supervisor_config
from .process import ManagedProcess, topological_sort
from .scheduler import Scheduler
from .server import create_supervisor_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("chat_supervisor")


async def _run(config_path: str = "supervisor.yaml") -> None:
    config = load_supervisor_config(config_path)
    base_cwd = Path.cwd()

    # Build managed processes in dependency order
    startup_order = topological_sort(config.processes)
    processes: dict[str, ManagedProcess] = {}
    for name in startup_order:
        proc_config = config.processes[name]
        if proc_config.enabled:
            processes[name] = ManagedProcess(name, proc_config, base_cwd)

    scheduler = Scheduler(config, processes)

    # Kill leftover processes from previous run
    scheduler.cleanup_stale()

    # Start all processes
    await scheduler.start_all()

    server: uvicorn.Server | None = None

    async def shutdown_supervisor() -> None:
        # API-triggered shutdown should follow the same full-exit path as signals.
        assert server is not None
        await _shutdown(scheduler, server)

    # Build API server
    app = create_supervisor_app(
        config,
        scheduler,
        processes,
        shutdown_supervisor=shutdown_supervisor,
    )
    uvi_config = uvicorn.Config(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="info",
    )
    server = uvicorn.Server(uvi_config)

    # Signal handling for graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.ensure_future(_shutdown(scheduler, server)),
        )

    # Run server and scheduler concurrently
    await asyncio.gather(
        server.serve(),
        scheduler.run(),
    )


async def _shutdown(scheduler: Scheduler, server: uvicorn.Server) -> None:
    logger.info("Shutting down...")
    await scheduler.stop_all()
    scheduler.request_stop()
    server.should_exit = True


def main() -> None:
    """Entry point for chat-supervisor."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else "supervisor.yaml"
    asyncio.run(_run(config_path))


if __name__ == "__main__":
    main()
