"""`proxy claude-code` entry point for the native Claude Code proxy."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
import sys
import webbrowser

import uvicorn

from chat_agent.core.config import load_app_timezone
from chat_agent.timezone_utils import configure_runtime_timezone

from .app import create_app
from .auth import (
    ClaudeCodeOAuthClient,
    StoredClaudeCodeTokenStore,
)
from .settings import ClaudeCodeProxySettings

OVERVIEW_EPILOG = """\
commands:
  serve                  Start the proxy (default when no command is given)
  login                  Browser OAuth login; repeat to add more accounts
  tokens                 Manage stored tokens (list / promote / remove)

examples:
  proxy claude-code login                  Log in a Claude account
  proxy claude-code login                  Run again to add a second account
  proxy claude-code tokens list            Show stored tokens, highest priority first
  proxy claude-code tokens promote <id>    Make a token the highest priority
  proxy claude-code tokens remove <id>     Delete a stored token
  proxy claude-code serve                  Serve on http://127.0.0.1:4142
  proxy claude-code serve --port 4200      Serve on a custom port

Multiple logins provide failover: serve normally uses the highest-priority token
and switches to the next one when upstream returns 401/403/429 or times out while
waiting for response headers. Priority defaults to the newest login and can be
changed with `tokens promote` or from the web dashboard's Proxy page.
Run `proxy claude-code <command> --help` for command-specific flags.
"""

SERVE_EPILOG = """\
examples:
  proxy claude-code serve
  proxy claude-code serve --host 0.0.0.0 --api-key secret123   # allow LAN clients
  proxy claude-code serve --access-token sk-ant-...   # bypass the token store

Localhost requests never need credentials. Non-localhost requests must present
the inbound API key (x-api-key or Authorization: Bearer); without --api-key /
CLAUDE_CODE_PROXY_API_KEY they are rejected, so binding a public host never
silently exposes upstream quota.

Any flag left unset falls back to its CLAUDE_CODE_PROXY_* environment variable,
then to a built-in default.
"""

LOGIN_EPILOG = """\
Opens the Claude authorization URL in your browser, then asks you to paste back the
`code#state` value Anthropic shows on the callback page. The resulting token is
appended to the store; run this again to add more accounts for failover.
"""


def _add_common_oauth_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--oauth-client-id", help="Override Claude OAuth client ID.")
    parser.add_argument("--oauth-scope", help="Override Claude OAuth scope string.")


def build_overview_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy claude-code",
        description="Local proxy that forwards Claude Code requests upstream to Anthropic.",
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


def _build_oauth_client(settings: ClaudeCodeProxySettings) -> ClaudeCodeOAuthClient:
    return ClaudeCodeOAuthClient(
        request_timeout=settings.request_timeout,
        client_id=settings.oauth_client_id,
        scope=settings.oauth_scope,
    )


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy claude-code serve",
        description=(
            "Start the proxy server. Uses stored OAuth tokens with 401/403/429 "
            "and upstream read-timeout failover."
        ),
        epilog=SERVE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    parser.add_argument("--request-timeout", type=float, help="Upstream request timeout (s).")
    parser.add_argument("--anthropic-base-url", help="Upstream Anthropic API base URL.")
    parser.add_argument("--anthropic-version", help="Anthropic API version header.")
    parser.add_argument("--beta-headers", help="Comma-separated anthropic-beta header values.")
    parser.add_argument("--required-system-prompt", help="Required system prompt to prepend.")
    parser.add_argument("--user-agent", help="User-Agent header for upstream requests.")
    parser.add_argument(
        "--access-token",
        help="Bypass the OAuth token store and use this access token directly.",
    )
    parser.add_argument(
        "--api-key",
        help=(
            "Inbound API key required from non-localhost clients. "
            "Localhost requests never need it."
        ),
    )
    _add_common_oauth_flags(parser)
    return parser


def build_login_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy claude-code login",
        description="Browser OAuth login. Appends a token to the store; repeat to add accounts.",
        epilog=LOGIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--request-timeout", type=float, help="OAuth HTTP timeout (s).")
    _add_common_oauth_flags(parser)
    parser.add_argument(
        "--code",
        help="Paste the manual Anthropic callback code in `code#state` format.",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not attempt to open the authorization URL automatically.",
    )
    return parser


def build_tokens_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy claude-code tokens",
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


def _settings_from_serve_args(args: argparse.Namespace) -> ClaudeCodeProxySettings:
    # Every flag defaults to None when unset; use `is not None` so explicit falsy
    # values (--port 0, --request-timeout 0, --beta-headers "") are honored.
    settings = ClaudeCodeProxySettings.from_env()
    if args.host is not None:
        settings = replace(settings, host=args.host)
    if args.port is not None:
        settings = replace(settings, port=args.port)
    if args.request_timeout is not None:
        settings = replace(settings, request_timeout=args.request_timeout)
    if args.anthropic_base_url is not None:
        settings = replace(settings, anthropic_base_url=args.anthropic_base_url.rstrip("/"))
    if args.anthropic_version is not None:
        settings = replace(settings, anthropic_version=args.anthropic_version)
    if args.beta_headers is not None:
        settings = replace(settings, beta_headers=args.beta_headers)
    if args.required_system_prompt is not None:
        settings = replace(settings, required_system_prompt=args.required_system_prompt)
    if args.user_agent is not None:
        settings = replace(settings, user_agent=args.user_agent)
    if args.access_token is not None:
        settings = replace(settings, access_token=args.access_token)
    if args.api_key is not None:
        settings = replace(settings, api_key=args.api_key)
    if args.oauth_client_id is not None:
        settings = replace(settings, oauth_client_id=args.oauth_client_id)
    if args.oauth_scope is not None:
        settings = replace(settings, oauth_scope=args.oauth_scope)
    return settings


def _settings_from_login_args(args: argparse.Namespace) -> ClaudeCodeProxySettings:
    settings = ClaudeCodeProxySettings.for_login_from_env()
    if args.request_timeout is not None:
        settings = replace(settings, request_timeout=args.request_timeout)
    if args.oauth_client_id is not None:
        settings = replace(settings, oauth_client_id=args.oauth_client_id)
    if args.oauth_scope is not None:
        settings = replace(settings, oauth_scope=args.oauth_scope)
    return settings


def _run_browser_login(
    settings: ClaudeCodeProxySettings, args: argparse.Namespace
) -> int:
    oauth = _build_oauth_client(settings)
    authorization = oauth.begin_authorization()

    print(
        "Claude browser OAuth login\n"
        f"Authorization URL: {authorization.authorization_url}\n"
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

    token = oauth.exchange_manual_code(manual_code, authorization=authorization)
    StoredClaudeCodeTokenStore().save(token)
    print(f"Saved Claude OAuth token (id={token.id}) to {token_store_path()}", flush=True)
    return 0


def run_login(args: argparse.Namespace) -> int:
    settings = _settings_from_login_args(args)
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
    store = StoredClaudeCodeTokenStore()
    if args.tokens_action == "list":
        tokens = store.load_all()
        if not tokens:
            print("No tokens stored. Run `proxy claude-code login` first.")
            return 0
        for token in tokens:
            print(
                f"{token.id}  source={token.source}  expires_at={token.expires_at.isoformat()}"
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
    return str(StoredClaudeCodeTokenStore().path)


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
