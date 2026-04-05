"""Standalone executable entry point for the monitoring web API."""

from __future__ import annotations

import argparse
from dataclasses import replace
import sys

import uvicorn

from .app import create_app
from .settings import WebApiSettings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chat-web-api")
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    return parser


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "serve":
        argv = argv[1:]
    args = _build_parser().parse_args(argv)
    settings = WebApiSettings.from_env()
    if args.host:
        settings = replace(settings, host=args.host)
    if args.port:
        settings = replace(settings, port=args.port)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
