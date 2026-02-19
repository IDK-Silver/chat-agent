import argparse
import os
import sys

from .cli import main
from .cli.init import init_command


def _resolve_user(args_user: str | None) -> str:
    """Resolve user from --user flag, .env file, or CHAT_AGENT_USER env var."""
    if args_user:
        return args_user
    from dotenv import dotenv_values

    env_user = dotenv_values().get("CHAT_AGENT_USER") or os.environ.get(
        "CHAT_AGENT_USER"
    )
    if env_user:
        return env_user
    print(
        "Error: no user specified.\n"
        "  Use --user <name> or set CHAT_AGENT_USER environment variable.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def run() -> None:
    """Entry point with subcommand support."""
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        if any(arg.startswith("--user") for arg in sys.argv[2:]):
            print("Error: --user is not allowed with 'init'", file=sys.stderr)
            raise SystemExit(2)

        # Remove 'init' from argv before passing to init_command
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        init_command()
    else:
        parser = argparse.ArgumentParser(prog="chat_agent")
        parser.add_argument(
            "--user",
            default=None,
            help="User selector (user_id or display name). Falls back to CHAT_AGENT_USER env var.",
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--new",
            action="store_true",
            default=False,
            help="Start a new session (default: continue last session).",
        )
        group.add_argument(
            "--resume",
            nargs="?",
            const="",
            default=None,
            help="Resume a session. No value: interactive picker. With value: resume specific session_id.",
        )
        group.add_argument(
            "--continue",
            dest="continue_session",
            action="store_true",
            default=False,
            help="Auto-resume the most recent session (this is the default).",
        )
        args = parser.parse_args()

        user = _resolve_user(args.user)

        # Default behavior: continue last session
        if args.new:
            resume_val = None
        elif args.resume is not None:
            resume_val = args.resume
        elif args.continue_session:
            resume_val = "__continue__"
        else:
            # No flag → default to continue
            resume_val = "__continue__"

        main(user=user, resume=resume_val)


if __name__ == "__main__":
    run()
