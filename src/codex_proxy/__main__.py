"""`proxy codex` entry point for the native Codex proxy."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
import sys
import webbrowser

import uvicorn

from lincy.core.config import load_app_timezone
from lincy.timezone_utils import configure_runtime_timezone

from .app import create_app
from .auth import (
    CodexAuthLoader,
    CodexOAuthClient,
    StoredCodexTokenStore,
    new_token_id,
    parse_manual_callback_value,
    wait_for_browser_callback,
)
from .settings import CodexProxySettings

OVERVIEW_EPILOG = """\
commands:
  serve                  Start the proxy (default when no command is given)
  login                  Browser OAuth login; repeat to add more accounts
  tokens                 Manage stored tokens (list / promote / remove)

examples:
  proxy codex login                        Log in a ChatGPT account
  proxy codex login                        Run again to add a second account
  proxy codex login --from-codex           Import the official `codex login` auth
  proxy codex tokens list                  Show stored tokens, highest priority first
  proxy codex tokens promote <id>          Make a token the highest priority
  proxy codex tokens remove <id>           Delete a stored token
  proxy codex serve                        Serve on http://127.0.0.1:4143
  proxy codex serve --port 4200            Serve on a custom port

Multiple logins provide failover: serve normally uses the highest-priority token
and switches to the next one when upstream returns 401/403/429 or times out while
waiting for response headers. The official `~/.codex/auth.json` (if present) is
always available as a lowest-priority fallback, skipped when a stored token
already covers the same ChatGPT account. Priority defaults to the newest login
and can be changed with `tokens promote` or from the web dashboard's Proxy page.
Run `proxy codex <command> --help` for command-specific flags.
"""

SERVE_EPILOG = """\
examples:
  proxy codex serve
  proxy codex serve --host 0.0.0.0 --api-key secret123   # allow LAN clients

Localhost requests never need credentials for any endpoint. Non-localhost clients
must present the inbound API key (x-api-key or Authorization: Bearer) for the
management surface (/usage, /login*, /tokens*); without --api-key /
CODEX_PROXY_API_KEY they are rejected. /chat and /compact stay ungated (the local
provider client sends no key), same as before this proxy had a token pool.

Any flag left unset falls back to its CODEX_PROXY_* environment variable, then to
a built-in default.
"""

LOGIN_EPILOG = """\
Opens the Codex authorization URL in your browser and waits for the local
callback (http://localhost:1455/auth/callback) to complete the login
automatically. If the browser cannot reach the local listener (e.g. headless /
SSH), copy the URL it redirected to (or just `code#state`) and pass it via
--code instead. The resulting token is appended to the store; run this again
to add more accounts for failover.
"""


def _add_common_oauth_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--oauth-client-id", help="Override Codex OAuth client ID.")
    parser.add_argument("--oauth-scope", help="Override Codex OAuth scope string.")


def build_overview_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy codex",
        description="Local proxy that forwards Codex requests upstream to the ChatGPT Codex backend.",
        epilog=OVERVIEW_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve", "login", "tokens"],
        help="Subcommand to run (default: serve).",
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


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy codex serve",
        description=(
            "Start the proxy server. Uses stored OAuth tokens (plus the official "
            "codex CLI auth as a lowest-priority fallback) with 401/403/429 and "
            "upstream read-timeout failover."
        ),
        epilog=SERVE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    parser.add_argument("--request-timeout", type=float, help="Upstream request timeout (s).")
    parser.add_argument("--codex-base-url", help="Upstream ChatGPT Codex backend base URL.")
    parser.add_argument(
        "--api-key",
        help=(
            "Inbound API key required from non-localhost clients on the "
            "management surface. Localhost requests never need it."
        ),
    )
    _add_common_oauth_flags(parser)
    return parser


def build_login_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy codex login",
        description="Browser OAuth login. Appends a token to the store; repeat to add accounts.",
        epilog=LOGIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--request-timeout", type=float, help="OAuth HTTP timeout (s).")
    _add_common_oauth_flags(parser)
    parser.add_argument(
        "--code",
        help="Paste the callback URL (or `code#state`) instead of waiting for the local listener.",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not attempt to open the authorization URL automatically.",
    )
    parser.add_argument(
        "--from-codex",
        action="store_true",
        help="Import the official Codex CLI auth (~/.codex/auth.json) instead of browser OAuth.",
    )
    return parser


def build_tokens_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy codex tokens",
        description=(
            "Manage stored OAuth tokens. Priority is newest-first; serve uses the top "
            "one and fails over on 401/403/429 or upstream read timeout."
        ),
    )
    sub = parser.add_subparsers(dest="tokens_action", required=True)
    sub.add_parser("list", help="List stored tokens (newest first).")
    promote = sub.add_parser("promote", help="Move a token to the front of the priority order.")
    promote.add_argument("id", help="Token id to promote.")
    remove = sub.add_parser("remove", help="Delete a stored token.")
    remove.add_argument("id", help="Token id to remove.")
    return parser


def _settings_from_serve_args(args: argparse.Namespace) -> CodexProxySettings:
    # Every flag defaults to None when unset; use `is not None` so explicit falsy
    # values (--port 0, --request-timeout 0) are honored.
    settings = CodexProxySettings.from_env()
    if args.host is not None:
        settings = replace(settings, host=args.host)
    if args.port is not None:
        settings = replace(settings, port=args.port)
    if args.request_timeout is not None:
        settings = replace(settings, request_timeout=args.request_timeout)
    if args.codex_base_url is not None:
        settings = replace(settings, codex_base_url=args.codex_base_url.rstrip("/"))
    if args.api_key is not None:
        settings = replace(settings, api_key=args.api_key)
    if args.oauth_client_id is not None:
        settings = replace(settings, oauth_client_id=args.oauth_client_id)
    if args.oauth_scope is not None:
        settings = replace(settings, oauth_scope=args.oauth_scope)
    return settings


def _settings_from_login_args(args: argparse.Namespace) -> CodexProxySettings:
    settings = CodexProxySettings.for_login_from_env()
    if args.request_timeout is not None:
        settings = replace(settings, request_timeout=args.request_timeout)
    if args.oauth_client_id is not None:
        settings = replace(settings, oauth_client_id=args.oauth_client_id)
    if args.oauth_scope is not None:
        settings = replace(settings, oauth_scope=args.oauth_scope)
    return settings


def _run_import_from_codex(settings: CodexProxySettings) -> int:
    loader = CodexAuthLoader(path=settings.codex_auth_path)
    try:
        loaded = loader.load()
    except ValueError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    if loaded is None:
        print(
            f"No official Codex auth found at {settings.codex_auth_path}. Run `codex login` first.",
            file=sys.stderr,
        )
        return 1
    # The loader stamps the fixed fallback id; give the store its own fresh id
    # so it becomes a normal, independently promotable/removable pool entry.
    imported = loaded.model_copy(update={"id": new_token_id()})
    StoredCodexTokenStore(settings.token_path).save(imported)
    print(f"Imported Codex auth (id={imported.id}) to {settings.token_path}", flush=True)
    return 0


def _run_browser_login(settings: CodexProxySettings, args: argparse.Namespace) -> int:
    oauth = _build_oauth_client(settings)
    authorization = oauth.begin_authorization()

    print(
        "Codex browser OAuth login\n"
        f"Authorization URL: {authorization.authorization_url}",
        flush=True,
    )

    if args.code:
        code, returned_state = parse_manual_callback_value(args.code)
    else:
        if not args.no_open_browser:
            try:
                webbrowser.open(authorization.authorization_url)
            except Exception:
                pass
        try:
            code, returned_state = wait_for_browser_callback(authorization)
        except KeyboardInterrupt:
            print("Canceled.", file=sys.stderr)
            return 130

    token = oauth.exchange_callback_code(
        code, returned_state=returned_state, authorization=authorization
    )
    StoredCodexTokenStore(settings.token_path).save(token)
    print(f"Saved Codex OAuth token (id={token.id}) to {settings.token_path}", flush=True)
    return 0


def run_login(args: argparse.Namespace) -> int:
    settings = _settings_from_login_args(args)
    if args.from_codex:
        return _run_import_from_codex(settings)
    try:
        return _run_browser_login(settings, args)
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1


def run_serve(args: argparse.Namespace) -> None:
    configure_runtime_timezone(load_app_timezone())
    settings = _settings_from_serve_args(args)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
    )


def run_tokens(args: argparse.Namespace) -> int:
    store = StoredCodexTokenStore(CodexProxySettings.for_login_from_env().token_path)
    if args.tokens_action == "list":
        tokens = store.load_all()
        if not tokens:
            print("No tokens stored. Run `proxy codex login` first.")
            return 0
        for token in tokens:
            print(
                f"{token.id}  source={token.source}  account={token.account_id}  "
                f"expires_at={token.expires_at.isoformat()}"
            )
        return 0
    if args.tokens_action == "promote":
        if store.promote(args.id):
            print(f"Promoted token {args.id}.")
            return 0
        print(f"No token with id {args.id}.", file=sys.stderr)
        return 1
    # Remaining action is "remove" (the subparser is required with fixed choices).
    if store.remove(args.id):
        print(f"Removed token {args.id}.")
        return 0
    print(f"No token with id {args.id}.", file=sys.stderr)
    return 1


def token_store_path() -> str:
    return str(CodexProxySettings.for_login_from_env().token_path)


def main(argv: Sequence[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        args = build_serve_parser().parse_args([])
        run_serve(args)
        return

    head = argv[0]
    if head in ("-h", "--help"):
        # Top-level help: show the command overview and usage examples, then exit.
        build_overview_parser().parse_args(["--help"])
        return
    if head == "serve":
        args = build_serve_parser().parse_args(argv[1:])
        run_serve(args)
        return
    if head == "login":
        args = build_login_parser().parse_args(argv[1:])
        raise SystemExit(run_login(args))
    if head == "tokens":
        args = build_tokens_parser().parse_args(argv[1:])
        raise SystemExit(run_tokens(args))

    # Backward-compat: bare flags (no subcommand) are treated as `serve`.
    args = build_serve_parser().parse_args(argv)
    run_serve(args)


if __name__ == "__main__":
    main()
