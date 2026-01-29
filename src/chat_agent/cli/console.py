from contextlib import contextmanager
from typing import Iterator

from rich.console import Console
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.live import Live

from .formatter import format_tool_call, format_tool_result
from ..llm.schema import ToolCall


class ChatConsole:
    """Rich-based console output for chat interface."""

    def __init__(self) -> None:
        self.console = Console()

    def print_tool_call(self, tool_call: ToolCall) -> None:
        """Print tool call in blue."""
        text = format_tool_call(tool_call)
        self.console.print(f"  [blue]{text}[/blue]")

    def print_tool_result(self, tool_call: ToolCall, result: str) -> None:
        """Print tool result in gray, indented."""
        text = format_tool_result(tool_call, result)
        if result.startswith("Error"):
            self.console.print(f"    [red]{text}[/red]")
        else:
            self.console.print(f"    [dim]{text}[/dim]")

    def print_assistant(self, content: str) -> None:
        """Print assistant response with Markdown rendering."""
        if not content:
            return
        md = Markdown(content)
        self.console.print(md)
        self.console.print()

    def print_error(self, message: str) -> None:
        """Print error message in red."""
        self.console.print(f"[red]Error: {message}[/red]")

    def print_info(self, message: str) -> None:
        """Print info message."""
        self.console.print(message)

    def print_welcome(self) -> None:
        """Print welcome message."""
        self.console.print("Chat started. Type /help for commands.\n")

    def print_goodbye(self) -> None:
        """Print goodbye message."""
        self.console.print("Bye!")

    @contextmanager
    def spinner(self, text: str = "Thinking...") -> Iterator[None]:
        """Show a spinner while processing."""
        with Live(
            Spinner("dots", text=text, style="blue"),
            console=self.console,
            refresh_per_second=10,
            transient=True,
        ):
            yield
