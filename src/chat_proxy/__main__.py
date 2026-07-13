"""Unified `proxy` command that dispatches to the per-provider proxies.

Every proxy keeps its own package (settings/auth/service/app); this module only
owns provider-name routing so there is exactly one console script to remember:

    proxy <provider> [command ...]
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import sys


def _claude_code_main() -> Callable[[Sequence[str]], None]:
    from claude_code_proxy.__main__ import main

    return main


def _codex_main() -> Callable[[Sequence[str]], None]:
    from codex_proxy.__main__ import main

    return main


def _copilot_main() -> Callable[[Sequence[str]], None]:
    from copilot_proxy.__main__ import main

    return main


def _grok_main() -> Callable[[Sequence[str]], None]:
    from grok_proxy.__main__ import main

    return main


# Import lazily so `proxy <provider>` never pays for the other providers.
PROVIDER_LOADERS: dict[str, Callable[[], Callable[[Sequence[str]], None]]] = {
    "claude-code": _claude_code_main,
    "codex": _codex_main,
    "copilot": _copilot_main,
    "grok": _grok_main,
}

USAGE = """\
usage: proxy <provider> [command ...]

Local proxies that forward chat-agent LLM traffic upstream with subscription
OAuth credentials. One provider per subcommand:

  claude-code    Anthropic Claude Code proxy    serve / login / tokens  (:4142)
  codex          OpenAI Codex proxy             serve                   (:4143)
  copilot        GitHub Copilot proxy           serve / login           (:4141)
  grok           xAI SuperGrok proxy            serve / login           (:4144)

examples:
  proxy claude-code login              Log in a Claude account (repeat to add more)
  proxy claude-code tokens list        Show stored Claude tokens
  proxy claude-code serve              Start the Claude Code proxy
  proxy copilot login                  GitHub device-code login
  proxy grok serve --port 4200         Serve on a custom port

`serve` is the default when a provider is given without a command.
Run `proxy <provider> --help` for provider-specific commands and flags.
"""


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE, end="")
        return

    provider = args[0]
    loader = PROVIDER_LOADERS.get(provider)
    if loader is None:
        print(f"proxy: unknown provider '{provider}'\n", file=sys.stderr)
        print(USAGE, end="", file=sys.stderr)
        raise SystemExit(2)
    loader()(args[1:])


if __name__ == "__main__":
    main()
