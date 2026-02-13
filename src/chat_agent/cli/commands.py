from enum import Enum
from typing import Callable

from .console import ChatConsole


class CommandResult(Enum):
    """Result of command execution."""
    CONTINUE = "continue"  # Continue chat loop
    SHUTDOWN = "shutdown"  # Exit with memory saving
    EXIT = "exit"  # Exit immediately without saving
    CLEAR = "clear"  # Clear conversation history
    RELOAD_SYSTEM_PROMPT = "reload_system_prompt"  # Reload system prompt from disk


class CommandHandler:
    """Handler for slash commands."""

    def __init__(self, console: ChatConsole) -> None:
        self._console = console
        self._commands: dict[str, tuple[Callable[[str], CommandResult], str]] = {
            "/help": (self._help, "Show available commands"),
            "/clear": (self._clear, "Clear conversation history"),
            "/shutdown": (self._shutdown, "Exit with memory saving"),
            "/exit": (self._exit, "Exit immediately (no save)"),
            "/reload": (self._reload, "Reload resources (e.g. system-prompt)"),
        }

    def is_command(self, text: str) -> bool:
        """Check if text is a slash command."""
        return text.startswith("/")

    def execute(self, text: str) -> CommandResult:
        """Execute a slash command."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""
        if cmd in self._commands:
            handler, _ = self._commands[cmd]
            return handler(args)
        else:
            self._console.print_error(f"Unknown command: {cmd}")
            self._console.print_info("Type /help for available commands.")
            return CommandResult.CONTINUE

    def _help(self, _args: str) -> CommandResult:
        """Show help message."""
        self._console.print_info("\nAvailable commands:")
        for cmd, (_, desc) in self._commands.items():
            self._console.print_info(f"  {cmd:10} - {desc}")
        self._console.print_info("")
        return CommandResult.CONTINUE

    def _clear(self, _args: str) -> CommandResult:
        """Clear conversation - returns CLEAR to signal app to reset."""
        self._console.print_info("Conversation cleared.\n")
        return CommandResult.CLEAR

    def _shutdown(self, _args: str) -> CommandResult:
        """Shutdown with memory saving."""
        return CommandResult.SHUTDOWN

    def _exit(self, _args: str) -> CommandResult:
        """Exit immediately without saving."""
        return CommandResult.EXIT

    def _reload(self, args: str) -> CommandResult:
        """Reload resources."""
        if not args:
            self._console.print_info("Usage: /reload system-prompt")
            return CommandResult.CONTINUE
        if args == "system-prompt":
            return CommandResult.RELOAD_SYSTEM_PROMPT
        self._console.print_error(f"Unknown reload target: {args}")
        self._console.print_info("Usage: /reload system-prompt")
        return CommandResult.CONTINUE
