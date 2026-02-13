from enum import Enum
from typing import Callable

from .console import ChatConsole


class CommandResult(Enum):
    """Result of command execution."""
    CONTINUE = "continue"  # Continue chat loop
    SHUTDOWN = "shutdown"  # Exit with memory saving
    EXIT = "exit"  # Exit immediately without saving
    CLEAR = "clear"  # Clear conversation history


class CommandHandler:
    """Handler for slash commands."""

    def __init__(self, console: ChatConsole) -> None:
        self._console = console
        self._commands: dict[str, tuple[Callable[[], CommandResult], str]] = {
            "/help": (self._help, "Show available commands"),
            "/clear": (self._clear, "Clear conversation history"),
            "/shutdown": (self._shutdown, "Exit with memory saving"),
            "/exit": (self._exit, "Exit immediately (no save)"),
        }

    def is_command(self, text: str) -> bool:
        """Check if text is a slash command."""
        return text.startswith("/")

    def execute(self, text: str) -> CommandResult:
        """Execute a slash command."""
        cmd = text.split()[0].lower()
        if cmd in self._commands:
            handler, _ = self._commands[cmd]
            return handler()
        else:
            self._console.print_error(f"Unknown command: {cmd}")
            self._console.print_info("Type /help for available commands.")
            return CommandResult.CONTINUE

    def _help(self) -> CommandResult:
        """Show help message."""
        self._console.print_info("\nAvailable commands:")
        for cmd, (_, desc) in self._commands.items():
            self._console.print_info(f"  {cmd:10} - {desc}")
        self._console.print_info("")
        return CommandResult.CONTINUE

    def _clear(self) -> CommandResult:
        """Clear conversation - returns CLEAR to signal app to reset."""
        self._console.print_info("Conversation cleared.\n")
        return CommandResult.CLEAR

    def _shutdown(self) -> CommandResult:
        """Shutdown with memory saving."""
        return CommandResult.SHUTDOWN

    def _exit(self) -> CommandResult:
        """Exit immediately without saving."""
        return CommandResult.EXIT
