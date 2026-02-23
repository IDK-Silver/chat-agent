"""CLI client for the chat-supervisor control API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from .config import load_supervisor_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chat-supervisorctl")
    parser.add_argument(
        "--config",
        default="supervisor.yaml",
        help="Config file name under cfgs/ (default: supervisor.yaml)",
    )
    parser.add_argument("--host", help="Override supervisor API host")
    parser.add_argument(
        "--port",
        type=int,
        help="Override supervisor API port",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")

    restart_parser = subparsers.add_parser("restart")
    restart_parser.add_argument("name", help="Managed process name")

    subparsers.add_parser("upgrade")
    subparsers.add_parser("stop")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base_url = _resolve_base_url(args.config, args.host, args.port)

    method = "GET"
    path = "/status"
    if args.command == "restart":
        method = "POST"
        path = f"/restart/{args.name}"
    elif args.command == "upgrade":
        method = "POST"
        path = "/upgrade"
    elif args.command == "stop":
        method = "POST"
        path = "/shutdown"

    try:
        status_code, payload = _request_json(base_url, method, path)
    except httpx.HTTPError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _print_payload(payload)
    if status_code >= 400:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
