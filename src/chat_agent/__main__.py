import sys

from .cli import main
from .cli.init import init_command


def run() -> None:
    """Entry point with subcommand support."""
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        # Remove 'init' from argv before passing to init_command
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        init_command()
    else:
        main()


if __name__ == "__main__":
    run()
