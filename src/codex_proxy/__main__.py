"""Standalone executable entry point for the native Codex proxy."""

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
    CodexAuthLoader,
    CodexOAuthClient,
    CodexTokenStore,
    resolve_codex_auth_path,
    resolve_token_path,
    wait_for_browser_callback,
)
from .settings import CodexProxySettings


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-proxy")
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    parser.add_argument(
        "--token-path",
        help="Override proxy token store path (defaults to platform config dir).",
    )
    parser.add_argument(
        "--codex-auth-path",
        help="Override official Codex auth path for fallback import.",
    )
    return parser


def build_login_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-proxy login")
    parser.add_argument(
        "--token-path",
        help="Override proxy token store path (defaults to platform config dir).",
    )
    parser.add_argument(
        "--codex-auth-path",
        help="Override official Codex auth path for `--from-codex`.",
    )
    parser.add_argument(
        "--client-id",
        help="Override Codex OAuth client ID.",
    )
    parser.add_argument(
        "--scope",
        help="Override Codex OAuth scope string.",
    )
    parser.add_argument(
        "--from-codex",
        action="store_true",
        help="Import existing official Codex auth instead of running browser OAuth.",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not attempt to open the authorization URL automatically.",
    )
    return parser


def _build_oauth_client(settings: CodexProxySettings) -> CodexOAuthClient:
    return CodexOAuthClient(
        request_timeout=settings.request_timeout,
        client_id=settings.oauth_client_id,
        authorize_url=settings.oauth_authorize_url,
        token_url=settings.oauth_token_url,
        redirect_uri=settings.oauth_redirect_uri,
        scope=settings.oauth_scope,
    )


def _load_login_settings(args: argparse.Namespace) -> CodexProxySettings:
    settings = CodexProxySettings.for_login_from_env()
    if args.token_path:
        settings = replace(settings, token_path=resolve_token_path(args.token_path))
    if args.codex_auth_path:
        settings = replace(
            settings,
            codex_auth_path=resolve_codex_auth_path(args.codex_auth_path),
        )
    if args.client_id:
        settings = replace(settings, oauth_client_id=args.client_id)
    if args.scope:
        settings = replace(settings, oauth_scope=args.scope)
    return settings


def _run_login_from_codex(settings: CodexProxySettings) -> int:
    auth_path = settings.codex_auth_path or resolve_codex_auth_path()
    token = CodexAuthLoader(path=auth_path).load()
    if token is None:
        raise RuntimeError(f"No Codex auth found in {auth_path}.")
    CodexTokenStore(settings.token_path).save(token)
    print(
        "Imported Codex OAuth state\n"
        f"Codex auth: {auth_path}\n"
        f"Token path: {settings.token_path}",
        flush=True,
    )
    return 0


def _run_browser_login(settings: CodexProxySettings, args: argparse.Namespace) -> int:
    oauth = _build_oauth_client(settings)
    authorization = oauth.begin_authorization()

    print(
        "Codex browser OAuth login\n"
        f"Authorization URL: {authorization.authorization_url}\n"
        f"Token path: {settings.token_path}\n"
        f"Redirect URI: {authorization.redirect_uri}",
        flush=True,
    )

    callback_error: Exception | None = None
    try:
        def _open_browser() -> None:
            if args.no_open_browser:
                return
            try:
                webbrowser.open(authorization.authorization_url)
            except Exception:
                pass

        code, returned_state = wait_for_browser_callback(
            authorization,
            on_ready=_open_browser,
        )
        token = oauth.exchange_callback_code(
            code,
            returned_state=returned_state,
            authorization=authorization,
        )
        CodexTokenStore(settings.token_path).save(token)
        print(f"Saved Codex OAuth token to {settings.token_path}", flush=True)
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError) as exc:
        callback_error = exc
    if callback_error is not None:
        print(str(callback_error), file=sys.stderr, flush=True)
    return 1


def run_login(args: argparse.Namespace) -> int:
    settings = _load_login_settings(args)
    if args.from_codex:
        try:
            return _run_login_from_codex(settings)
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr, flush=True)
            return 1
    return _run_browser_login(settings, args)


def run_serve(args: argparse.Namespace) -> None:
    configure_runtime_timezone(load_app_timezone())
    if args.token_path:
        os.environ["CODEX_PROXY_TOKEN_PATH"] = args.token_path
    if args.codex_auth_path:
        os.environ["CODEX_PROXY_CODEX_AUTH_PATH"] = args.codex_auth_path
    settings = CodexProxySettings.from_env()
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
