"""chat-supervisor entry point."""

import asyncio
import logging
import socket
import signal
import sys
from pathlib import Path

import httpx
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

_SERVER_STARTUP_TIMEOUT_SEC = 5.0
_SERVER_STARTUP_POLL_SEC = 0.05


class SupervisorStartupError(RuntimeError):
    """Raised when the supervisor cannot start safely."""


def _port_is_available(host: str, port: int) -> bool:
    """Return False when the bind address is already occupied."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    bind_host = host
    if host == "localhost":
        bind_host = "127.0.0.1"
        family = socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
        except OSError:
            return False
    return True


def _probe_http_host(bind_host: str) -> str:
    """Map wildcard binds to a loopback address for local probe requests."""
    if bind_host in ("0.0.0.0", "localhost"):
        return "127.0.0.1"
    if bind_host == "::":
        return "::1"
    return bind_host


async def _looks_like_supervisor(host: str, port: int) -> bool:
    """Check whether the occupied port responds like chat-supervisor."""
    probe_host = _probe_http_host(host)
    url = f"http://{probe_host}:{port}/status"
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(url)
    except Exception:
        return False
    if resp.status_code != 200:
        return False
    try:
        data = resp.json()
    except ValueError:
        return False
    return isinstance(data, dict)


async def _assert_supervisor_slot_available(host: str, port: int) -> None:
    """Fail fast before starting child processes when API port is occupied."""
    if _port_is_available(host, port):
        return
    if await _looks_like_supervisor(host, port):
        raise SupervisorStartupError(
            f"chat-supervisor is already running on {host}:{port}"
        )
    raise SupervisorStartupError(
        f"Supervisor API address {host}:{port} is already in use"
    )


async def _wait_for_server_started(
    server: uvicorn.Server,
    server_task: asyncio.Task[None],
) -> None:
    """Wait until uvicorn marks startup complete or exits with an error."""
    deadline = asyncio.get_running_loop().time() + _SERVER_STARTUP_TIMEOUT_SEC
    while asyncio.get_running_loop().time() < deadline:
        if server_task.done():
            try:
                await server_task
            except SystemExit as exc:
                raise SupervisorStartupError(
                    "Supervisor API failed to start"
                ) from exc
            raise SupervisorStartupError(
                "Supervisor API exited during startup"
            )
        if getattr(server, "started", False):
            return
        await asyncio.sleep(_SERVER_STARTUP_POLL_SEC)
    raise SupervisorStartupError(
        "Timed out while waiting for supervisor API startup"
    )


async def _run(config_path: str = "supervisor.yaml") -> None:
    config = load_supervisor_config(config_path)
    base_cwd = Path.cwd()
    await _assert_supervisor_slot_available(config.server.host, config.server.port)

    # Build managed processes in dependency order
    startup_order = topological_sort(config.processes)
    processes: dict[str, ManagedProcess] = {}
    for name in startup_order:
        proc_config = config.processes[name]
        if proc_config.enabled:
            processes[name] = ManagedProcess(name, proc_config, base_cwd)

    scheduler = Scheduler(config, processes)

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

    server_task = asyncio.create_task(server.serve(), name="supervisor-api")
    try:
        await _wait_for_server_started(server, server_task)

        # Only touch child processes after the supervisor API is confirmed up.
        scheduler.cleanup_stale()
        await scheduler.start_all()

        scheduler_task = asyncio.create_task(scheduler.run(), name="supervisor-scheduler")
        await asyncio.gather(server_task, scheduler_task)
    except Exception:
        if not server.should_exit:
            server.should_exit = True
        raise


async def _shutdown(scheduler: Scheduler, server: uvicorn.Server) -> None:
    logger.info("Shutting down...")
    await scheduler.stop_all()
    scheduler.request_stop()
    server.should_exit = True


def main() -> None:
    """Entry point for chat-supervisor."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else "supervisor.yaml"
    try:
        asyncio.run(_run(config_path))
    except SupervisorStartupError as e:
        logger.error("%s", e)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
