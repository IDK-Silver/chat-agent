"""Standalone executable entry point for the native Copilot proxy."""

from __future__ import annotations

import argparse
from dataclasses import replace
import os
import sys
import webbrowser

import uvicorn

from chat_agent.core.config import load_app_timezone
from chat_agent.timezone_utils import configure_runtime_timezone

from .app import create_app
from .auth import GitHubDeviceFlowClient, GitHubTokenStore, resolve_token_path
from .settings import CopilotProxySettings


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="copilot-proxy")
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    parser.add_argument(
        "--token-path",
        help="Override token store path (defaults to platform config dir).",
    )
    return parser


def build_login_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="copilot-proxy login")
    parser.add_argument(
        "--token-path",
        help="Override token store path (defaults to platform config dir).",
    )
    parser.add_argument(
        "--client-id",
        help="Override GitHub OAuth app client ID.",
    )
    parser.add_argument(
        "--scope",
        help="Override device-flow scope string.",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not attempt to open the verification URL automatically.",
    )
    return parser


def _build_device_flow_client(settings: CopilotProxySettings) -> GitHubDeviceFlowClient:
    return GitHubDeviceFlowClient(
        auth_base_url=settings.github_web_base_url,
        github_api_base_url=settings.github_api_base_url,
        client_id=settings.oauth_client_id,
        scope=settings.oauth_scope,
        request_timeout=settings.request_timeout,
        editor_version=settings.editor_version,
        editor_plugin_version=settings.editor_plugin_version,
        user_agent=settings.user_agent,
        api_version=settings.api_version,
    )


def run_login(args: argparse.Namespace) -> int:
    settings = CopilotProxySettings.for_login_from_env()
    if args.token_path:
        settings = replace(settings, token_path=resolve_token_path(args.token_path))
    if args.client_id:
        settings = replace(settings, oauth_client_id=args.client_id)
    if args.scope:
        settings = replace(settings, oauth_scope=args.scope)

    device_flow = _build_device_flow_client(settings)
    device_code = device_flow.request_device_code()

    print(
        "GitHub device login\n"
        f"Verification URL: {device_code.verification_uri}\n"
        f"User code: {device_code.user_code}\n"
        f"Token path: {settings.token_path}",
        flush=True,
    )
    if not args.no_open_browser:
        try:
            webbrowser.open(device_code.verification_uri)
        except Exception:
            pass
    print("Waiting for authorization...", flush=True)

    try:
        access_token = device_flow.poll_access_token(device_code)
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130

    device_flow.verify_copilot_access(access_token.access_token)
    github_login = device_flow.fetch_user_login(access_token.access_token)
    stored_token = device_flow.build_stored_token(
        access_token,
        github_login=github_login,
    )
    GitHubTokenStore(settings.token_path).save(stored_token)

    if github_login:
        print(f"Authorized GitHub user: {github_login}", flush=True)
    print(f"Saved GitHub token to {settings.token_path}", flush=True)
    return 0


def run_serve(args: argparse.Namespace) -> None:
    configure_runtime_timezone(load_app_timezone())
    if args.token_path:
        os.environ["COPILOT_PROXY_TOKEN_PATH"] = args.token_path
    settings = CopilotProxySettings.from_env()
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


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "login":
        args = build_login_parser().parse_args(argv[1:])
        raise SystemExit(run_login(args))

    if argv and argv[0] == "serve":
        argv = argv[1:]
    args = build_serve_parser().parse_args(argv)
    run_serve(args)


if __name__ == "__main__":
    main()
