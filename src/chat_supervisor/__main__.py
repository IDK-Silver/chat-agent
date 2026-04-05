"""chat-supervisor entry point."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import logging
import socket
import signal
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from .config import load_supervisor_config
from .process import ManagedProcess, topological_sort
from .scheduler import ProcessStartupError, Scheduler
from .server import create_supervisor_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("chat_supervisor")

_SERVER_STARTUP_TIMEOUT_SEC = 5.0
_SERVER_STARTUP_POLL_SEC = 0.05
_SERVER_SHUTDOWN_TIMEOUT_SEC = 5.0


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


async def _run(
    config_path: str = "supervisor.yaml",
    *,
    chat_cli_new: bool = False,
) -> None:
    config = load_supervisor_config(config_path)
    base_cwd = Path.cwd()
    await _assert_supervisor_slot_available(config.server.host, config.server.port)

    startup_order = topological_sort(config.processes)
    processes: dict[str, ManagedProcess] = {}
    for name in startup_order:
        proc_config = config.processes[name]
        if proc_config.enabled:
            processes[name] = ManagedProcess(name, proc_config, base_cwd)
    if chat_cli_new:
        chat_cli = processes.get("chat-cli")
        if chat_cli is None:
            raise SupervisorStartupError(
                "chat-cli-new requested but chat-cli is not enabled in supervisor config"
            )
        chat_cli.queue_next_start_args(["--new"])

    scheduler = Scheduler(config, processes)

    server: uvicorn.Server | None = None

    async def shutdown_supervisor() -> None:
        assert server is not None
        await _shutdown(scheduler, server)

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
    scheduler_task: asyncio.Task[None] | None = None

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.ensure_future(_shutdown(scheduler, server)),
        )

    server_task = asyncio.create_task(server.serve(), name="supervisor-api")
    try:
        await _wait_for_server_started(server, server_task)
        scheduler.cleanup_stale()
        try:
            await scheduler.start_all()
        except ProcessStartupError as exc:
            raise SupervisorStartupError(str(exc)) from exc
        scheduler_task = asyncio.create_task(
            scheduler.run(),
            name="supervisor-scheduler",
        )
        await asyncio.gather(server_task, scheduler_task)
    except Exception:
        scheduler.request_stop()
        await scheduler.stop_all()
        if not server.should_exit:
            server.should_exit = True
        if scheduler_task is not None and not scheduler_task.done():
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        await _await_server_shutdown(server, server_task)
        raise


async def _await_server_shutdown(
    server: uvicorn.Server,
    server_task: asyncio.Task[None],
) -> None:
    """Wait for uvicorn to exit cleanly after should_exit is set."""
    if server_task.done():
        with suppress(asyncio.CancelledError):
            await server_task
        return

    server.should_exit = True
    try:
        await asyncio.wait_for(server_task, timeout=_SERVER_SHUTDOWN_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        server_task.cancel()
        with suppress(asyncio.CancelledError):
            await server_task


async def _shutdown(scheduler: Scheduler, server: uvicorn.Server) -> None:
    logger.info("Shutting down...")
    await scheduler.stop_all()
    scheduler.request_stop()
    server.should_exit = True


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default="supervisor.yaml",
        help="Config file name under cfgs/ (default: supervisor.yaml)",
    )
    parser.add_argument("--host", help="Override supervisor API host")
    parser.add_argument("--port", type=int, help="Override supervisor API port")


def build_parser() -> argparse.ArgumentParser:
    """Build the unified chat-supervisor CLI."""
    parser = argparse.ArgumentParser(prog="chat-supervisor")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument(
        "--config",
        default="supervisor.yaml",
        help="Config file name under cfgs/ (default: supervisor.yaml)",
    )
    start_parser.add_argument(
        "--chat-cli-new",
        action="store_true",
        default=False,
        help="Start the managed chat-cli with --new on its first spawn.",
    )

    check_parser = subparsers.add_parser("check", help="Preflight environment checks")
    check_parser.add_argument(
        "--config",
        default="supervisor.yaml",
        help="Config file name under cfgs/ (default: supervisor.yaml)",
    )

    for name in ("status", "stop", "upgrade", "new-session", "reload"):
        subparser = subparsers.add_parser(name)
        _add_connection_options(subparser)

    restart_parser = subparsers.add_parser("restart")
    _add_connection_options(restart_parser)
    restart_parser.add_argument("name", nargs="?", help="Managed process name")
    restart_parser.add_argument(
        "--new-session",
        action="store_true",
        default=False,
        help="For 'restart chat-cli' only: restart chat-cli with --new on the next spawn.",
    )
    return parser


def _resolve_base_url(config_name: str, host: str | None, port: int | None) -> str:
    if host is not None and port is not None:
        return f"http://{host}:{port}"
    cfg = load_supervisor_config(config_name)
    resolved_host = host or cfg.server.host
    resolved_port = port or cfg.server.port
    return f"http://{resolved_host}:{resolved_port}"


def _request_json(
    base_url: str,
    method: str,
    path: str,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        resp = client.request(method, path)
    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text}
    return resp.status_code, payload


def _print_payload(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _run_control_command(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args.config, args.host, args.port)

    method = "GET"
    path = "/status"
    if args.command == "stop":
        method = "POST"
        path = "/shutdown"
    elif args.command == "upgrade":
        method = "POST"
        path = "/upgrade"
    elif args.command == "new-session":
        method = "POST"
        path = "/new-session"
    elif args.command == "reload":
        method = "POST"
        path = "/reload"
    elif args.command == "restart":
        method = "POST"
        if args.new_session:
            if args.name != "chat-cli":
                print(
                    "Error: --new-session only supports 'restart chat-cli'.",
                    file=sys.stderr,
                )
                return 2
            path = "/restart/chat-cli?new_session=true"
        else:
            path = f"/restart/{args.name}" if args.name else "/restart"

    try:
        status_code, payload = _request_json(base_url, method, path)
    except httpx.HTTPError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _print_payload(payload)
    if status_code >= 400:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for chat-supervisor."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        from .check import run_check

        return run_check(args.config)

    if args.command == "start":
        try:
            asyncio.run(_run(args.config, chat_cli_new=args.chat_cli_new))
        except (SupervisorStartupError, ProcessStartupError) as exc:
            logger.error("%s", exc)
            return 1
        return 0

    return _run_control_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
