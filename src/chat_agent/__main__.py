import argparse
import sys

from .cli import main
from .cli.init import init_command


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
            required=True,
            help="User selector (user_id or display name).",
        )
        args = parser.parse_args()
        main(user=args.user)


if __name__ == "__main__":
    run()
