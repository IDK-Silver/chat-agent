import json
from contextlib import contextmanager
from typing import Iterator
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.live import Live

from .formatter import format_tool_call, format_tool_result
from ..llm.content import content_to_text
from ..llm.schema import ContentPart, Message, ToolCall


class ChatConsole:
    """Rich-based console output for chat interface."""

    def __init__(self, *, debug: bool = False, show_tool_use: bool = False) -> None:
        self.console = Console()
        self.debug = debug
        self.show_tool_use = show_tool_use

    def set_debug(self, enabled: bool) -> None:
        """Enable or disable debug-mode console output."""
        self.debug = enabled

    def set_show_tool_use(self, enabled: bool) -> None:
        """Enable or disable tool call/result display."""
        self.show_tool_use = enabled

    @staticmethod
    def _is_failed_tool_result(result: str) -> bool:
        """Check whether a tool result indicates failure."""
        if result.startswith("Error"):
            return True
        if not result.startswith("{"):
            return False
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and payload.get("status") == "failed"

    @staticmethod
    def _indent_lines(text: str, prefix: str) -> str:
        """Indent every line in text with a fixed prefix."""
        if not text:
            return prefix.rstrip()
        return "\n".join(f"{prefix}{line}" for line in text.splitlines())

    def print_tool_call(self, tool_call: ToolCall) -> None:
        """Print tool call in blue."""
        if not self.show_tool_use:
            return
        text = format_tool_call(tool_call)
        self.console.print(
            self._indent_lines(text, "  "),
            style="blue",
            markup=False,
        )

    def print_tool_result(
        self, tool_call: ToolCall, result: str | list[ContentPart],
    ) -> None:
        """Print tool result in gray, indented."""
        # Normalize multimodal results to text for display
        if isinstance(result, list):
            display_result = content_to_text(result)
        else:
            display_result = result
        failed = self._is_failed_tool_result(display_result)
        text = format_tool_result(tool_call, display_result)
        if not self.show_tool_use:
            if failed:
                self.print_warning(f"{tool_call.name} failed: {text}")
            return

        indented = self._indent_lines(text, "    ")
        if failed:
            self.console.print(indented, style="red", markup=False)
        else:
            self.console.print(indented, style="dim", markup=False)

    def print_assistant(self, content: str | None) -> None:
        """Print assistant response with Markdown rendering."""
        if not content:
            return
        md = Markdown(content)
        self.console.print(md)
        self.console.print()

    def print_error(self, message: str) -> None:
        """Print error message in red."""
        self.console.print(f"[red]Error: {escape(message)}[/red]")

    def print_warning(self, message: str, *, indent: int = 0) -> None:
        """Print warning message in yellow."""
        prefix = " " * max(0, indent)
        lines = escape(message).splitlines() or [""]
        self.console.print(f"{prefix}[yellow]Warning: {lines[0]}[/yellow]")
        for line in lines[1:]:
            self.console.print(f"{prefix}         [yellow]{line}[/yellow]")

    def print_info(self, message: str) -> None:
        """Print info message."""
        self.console.print(message, markup=False)

    def print_debug(self, label: str, message: str) -> None:
        """Print debug message in dim yellow."""
        self.console.print(f"  [dim yellow][DEBUG {escape(label)}][/dim yellow] [dim]{escape(message)}[/dim]")

    def print_debug_block(self, label: str, content: str) -> None:
        """Print debug label with multiline content block below it."""
        self.console.print(f"  [dim yellow][DEBUG {escape(label)}][/dim yellow]")
        for line in content.splitlines():
            self.console.print(f"    [dim]{escape(line)}[/dim]")

    def print_welcome(self) -> None:
        """Print welcome message."""
        self.console.print("Chat started. Type /help for commands.\n")

    def print_goodbye(self) -> None:
        """Print goodbye message."""
        self.console.print("Bye!")

    def print_resume_history(
        self,
        messages: list[Message],
        replay_turns: int | None,
        show_tool_calls: bool,
        timezone: str = "Asia/Taipei",
    ) -> None:
        """Print previous conversation history when resuming a session.

        Groups messages into turns (user msg + subsequent assistant/tool msgs
        until the next user msg) and displays the last *replay_turns* turns.
        """
        if not messages:
            return

        self.console.clear()
        tz = ZoneInfo(timezone)

        # Split messages into turns; each turn starts with a user message.
        turns: list[list[Message]] = []
        for msg in messages:
            if msg.role == "user":
                turns.append([msg])
            elif turns:
                turns[-1].append(msg)
            # Messages before the first user message are skipped (system, etc.)

        if not turns:
            return

        if replay_turns is not None:
            visible_turns = turns[-replay_turns:]
        else:
            visible_turns = turns

        omitted = sum(len(t) for t in turns) - sum(len(t) for t in visible_turns)
        if omitted > 0:
            self.console.print(
                f"... ({omitted} earlier messages)",
                style="dim",
            )
            self.console.print()

        for turn in visible_turns:
            for msg in turn:
                if msg.role == "user":
                    content = (msg.content or "").strip()
                    if content:
                        # Format timestamp to match input prompt style
                        time_prefix = ""
                        if msg.timestamp:
                            local_time = msg.timestamp.astimezone(tz)
                            time_prefix = local_time.strftime("%m/%d-%I:%M %p") + " "
                        lines = content.splitlines()
                        self.console.print(
                            f"[#888888]{time_prefix}[/#888888]> {escape(lines[0])}",
                        )
                        for line in lines[1:]:
                            self.console.print(
                                f"... {line}",
                                markup=False,
                            )
                        self.console.print()
                elif msg.role == "assistant" and not msg.tool_calls:
                    self.print_assistant(msg.content)
                elif msg.role == "assistant" and msg.tool_calls:
                    if show_tool_calls:
                        for tc in msg.tool_calls:
                            text = format_tool_call(tc)
                            self.console.print(
                                self._indent_lines(text, "  "),
                                style="blue",
                                markup=False,
                            )
                elif msg.role == "tool":
                    if show_tool_calls:
                        result = (msg.content or "")
                        preview = result.split("\n")[0][:80]
                        if len(result) > len(preview):
                            preview += "..."
                        self.console.print(
                            f"    {preview}",
                            style="dim",
                            markup=False,
                        )

        self.console.print()

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
