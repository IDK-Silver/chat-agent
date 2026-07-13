"""`proxy grok` entry point for the native SuperGrok OAuth proxy."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
import os
import sys
import webbrowser

import uvicorn

from chat_agent.core.config import load_app_timezone
from chat_agent.timezone_utils import configure_runtime_timezone

from .app import create_app
from .auth import (
    DeviceAuthorizationError,
    GrokAuthError,
    GrokOAuthClient,
    GrokTokenStore,
    resolve_token_path,
)
from .settings import GrokProxySettings


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxy grok")
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    parser.add_argument(
        "--token-path",
        help="Override token store path (defaults to platform config dir).",
    )
    parser.add_argument(
        "--access-token",
        help="Bypass the OAuth token store and use this access token directly.",
    )
    return parser


def build_login_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxy grok login")
    parser.add_argument(
        "--token-path",
        help="Override token store path (defaults to platform config dir).",
    )
    parser.add_argument(
        "--client-id",
        help="Override xAI OAuth app client ID.",
    )
    parser.add_argument(
        "--scope",
        help="Override OAuth scope string.",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not attempt to open the verification URL automatically.",
    )
    return parser


def _build_oauth_client(settings: GrokProxySettings) -> GrokOAuthClient:
    return GrokOAuthClient(
        request_timeout=settings.request_timeout,
        client_id=settings.oauth_client_id,
        scope=settings.oauth_scope,
        discovery_url=settings.oauth_discovery_url,
    )


def run_login(args: argparse.Namespace) -> int:
    settings = GrokProxySettings.for_login_from_env()
    if args.token_path:
        settings = replace(settings, token_path=resolve_token_path(args.token_path))
    if args.client_id:
        settings = replace(settings, oauth_client_id=args.client_id)
    if args.scope:
        settings = replace(settings, oauth_scope=args.scope)

    oauth = _build_oauth_client(settings)
    try:
        discovery = oauth.discover()
        device_code = oauth.request_device_code(
            device_authorization_endpoint=discovery.device_authorization_endpoint,
        )
    except (DeviceAuthorizationError, GrokAuthError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1

    verification_url = device_code.verification_uri_complete or device_code.verification_uri
    print(
        "xAI SuperGrok device login\n"
        f"Verification URL: {verification_url}\n"
        f"User code: {device_code.user_code}\n"
        f"Token path: {settings.token_path}",
        flush=True,
    )
    if not args.no_open_browser:
        try:
            webbrowser.open(verification_url)
        except Exception:
            pass
    print("Waiting for authorization...", flush=True)

    try:
        tokens = oauth.poll_access_token(
            device_code,
            token_endpoint=discovery.token_endpoint,
        )
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130
    except (DeviceAuthorizationError, GrokAuthError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1

    stored = oauth.build_stored_token(
        tokens,
        token_endpoint=discovery.token_endpoint,
        source="oauth_device_code",
    )
    GrokTokenStore(settings.token_path).save(stored)
    print(f"Saved SuperGrok OAuth token to {settings.token_path}", flush=True)
    return 0


def run_serve(args: argparse.Namespace) -> None:
    configure_runtime_timezone(load_app_timezone())
    if args.token_path:
        os.environ["GROK_PROXY_TOKEN_PATH"] = args.token_path
    if getattr(args, "access_token", None):
        os.environ["GROK_PROXY_ACCESS_TOKEN"] = args.access_token
    settings = GrokProxySettings.from_env()
    if args.host:
        settings = replace(settings, host=args.host)
    if args.port:
        settings = replace(settings, port=args.port)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
    )


def main(argv: Sequence[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "login":
        args = build_login_parser().parse_args(argv[1:])
        raise SystemExit(run_login(args))

    if argv and argv[0] == "serve":
        argv = argv[1:]
    args = build_serve_parser().parse_args(argv)
    run_serve(args)


if __name__ == "__main__":
    main()
