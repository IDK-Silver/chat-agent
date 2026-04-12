"""Standalone executable entry point for the native Claude Code proxy."""

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
from .auth import (
    ClaudeCodeCredentialLoader,
    ClaudeCodeOAuthClient,
    ClaudeCodeTokenStore,
    resolve_credentials_path,
    resolve_token_path,
)
from .settings import ClaudeCodeProxySettings


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-code-proxy")
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    parser.add_argument(
        "--token-path",
        help="Override proxy token store path (defaults to platform config dir).",
    )
    parser.add_argument(
        "--credentials-path",
        help="Override Claude Code credentials path.",
    )
    return parser


def build_login_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-code-proxy login")
    parser.add_argument(
        "--token-path",
        help="Override proxy token store path (defaults to platform config dir).",
    )
    parser.add_argument(
        "--credentials-path",
        help="Override Claude Code credentials path for `--from-claude-code`.",
    )
    parser.add_argument(
        "--client-id",
        help="Override Claude OAuth client ID.",
    )
    parser.add_argument(
        "--scope",
        help="Override Claude OAuth scope string.",
    )
    parser.add_argument(
        "--code",
        help="Paste the manual Anthropic callback code in `code#state` format.",
    )
    parser.add_argument(
        "--from-claude-code",
        action="store_true",
        help="Import existing Claude Code credentials instead of running browser OAuth.",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not attempt to open the authorization URL automatically.",
    )
    return parser


def _build_oauth_client(settings: ClaudeCodeProxySettings) -> ClaudeCodeOAuthClient:
    return ClaudeCodeOAuthClient(
        request_timeout=settings.request_timeout,
        client_id=settings.oauth_client_id,
        scope=settings.oauth_scope,
    )


def _load_login_settings(args: argparse.Namespace) -> ClaudeCodeProxySettings:
    settings = ClaudeCodeProxySettings.for_login_from_env()
    if args.token_path:
        settings = replace(settings, token_path=resolve_token_path(args.token_path))
    if args.credentials_path:
        settings = replace(
            settings,
            credentials_path=resolve_credentials_path(args.credentials_path),
        )
    if args.client_id:
        settings = replace(settings, oauth_client_id=args.client_id)
    if args.scope:
        settings = replace(settings, oauth_scope=args.scope)
    return settings


def _run_login_from_claude_code(settings: ClaudeCodeProxySettings) -> int:
    token = ClaudeCodeCredentialLoader(path=settings.credentials_path).load()
    if token is None:
        path_detail = (
            str(settings.credentials_path)
            if settings.credentials_path is not None
            else "default Claude Code credentials locations"
        )
        raise RuntimeError(f"No Claude Code credentials found in {path_detail}.")
    ClaudeCodeTokenStore(settings.token_path).save(token)
    print(
        "Imported Claude Code credentials\n"
        f"Token path: {settings.token_path}",
        flush=True,
    )
    return 0


def _run_browser_login(settings: ClaudeCodeProxySettings, args: argparse.Namespace) -> int:
    oauth = _build_oauth_client(settings)
    authorization = oauth.begin_authorization()

    print(
        "Claude browser OAuth login\n"
        f"Authorization URL: {authorization.authorization_url}\n"
        f"Token path: {settings.token_path}\n"
        "After approving in your browser, Anthropic will show a code in `code#state` format.",
        flush=True,
    )
    if not args.no_open_browser:
        try:
            webbrowser.open(authorization.authorization_url)
        except Exception:
            pass

    try:
        manual_code = args.code or input("Paste `code#state`: ").strip()
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130

    token = oauth.exchange_manual_code(
        manual_code,
        authorization=authorization,
    )
    ClaudeCodeTokenStore(settings.token_path).save(token)
    print(f"Saved Claude OAuth token to {settings.token_path}", flush=True)
    return 0


def run_login(args: argparse.Namespace) -> int:
    settings = _load_login_settings(args)
    try:
        if args.from_claude_code:
            return _run_login_from_claude_code(settings)
        return _run_browser_login(settings, args)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1


def run_serve(args: argparse.Namespace) -> None:
    configure_runtime_timezone(load_app_timezone())
    if args.token_path:
        os.environ["CLAUDE_CODE_PROXY_TOKEN_PATH"] = args.token_path
    if args.credentials_path:
        os.environ["CLAUDE_CODE_PROXY_CREDENTIALS_PATH"] = args.credentials_path
    settings = ClaudeCodeProxySettings.from_env()
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
