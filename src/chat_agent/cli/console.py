import json
from contextlib import contextmanager
from typing import Iterator
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.live import Live

from .formatter import format_tool_call, format_tool_result, format_gui_tool_call, format_gui_tool_result
from ..llm.content import content_to_text
from ..llm.schema import ContentPart, Message, ToolCall


class ChatConsole:
    """Rich-based console output for chat interface."""

    def __init__(self, *, debug: bool = False, show_tool_use: bool = False) -> None:
        self.console = Console()
        self.debug = debug
        self.show_tool_use = show_tool_use
        self.gui_intent_max_chars: int | None = None
        self._current_user: str | None = None

    def set_current_user(self, user_id: str) -> None:
        """Set user id for channel label formatting."""
        self._current_user = user_id

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
        text = format_tool_call(
            tool_call, gui_intent_max_chars=self.gui_intent_max_chars,
        )
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

    def print_gui_step(
        self,
        tool_call: ToolCall,
        result: str,
        step: int,
        max_steps: int,
        elapsed_sec: float = 0.0,
        total_elapsed_sec: float = 0.0,
        *,
        worker_timing: dict[str, float] | None = None,
        instruction_max_chars: int = 60,
        text_max_chars: int = 40,
        worker_result_max_chars: int = 100,
        result_max_chars: int = 60,
    ) -> None:
        """Print a GUI manager internal step."""
        if not self.show_tool_use:
            return

        call_text = format_gui_tool_call(
            tool_call,
            instruction_max_chars=instruction_max_chars,
            text_max_chars=text_max_chars,
        )
        step_tag = f" {elapsed_sec:.1f}s" if elapsed_sec > 0 else ""
        total_tag = f" | {total_elapsed_sec:.1f}s" if total_elapsed_sec > 0 else ""
        self.console.print(
            f"    [{step}/{max_steps}{step_tag}{total_tag}] {call_text}",
            style="cyan",
            markup=False,
        )

        # Show worker timing breakdown (screenshot vs inference)
        if worker_timing:
            ss = worker_timing.get("screenshot", 0.0)
            inf = worker_timing.get("inference", 0.0)
            self.console.print(
                f"      screenshot: {ss:.1f}s  inference: {inf:.1f}s",
                style="dim cyan",
                markup=False,
            )

        result_text = format_gui_tool_result(
            tool_call,
            result,
            worker_result_max_chars=worker_result_max_chars,
            result_max_chars=result_max_chars,
        )
        if result_text:
            failed = self._is_failed_tool_result(result)
            style = "red" if failed else "dim"
            self.console.print(
                self._indent_lines(result_text, "      "),
                style=style,
                markup=False,
            )

    # ------------------------------------------------------------------
    # Channel display (queue-visible turn sections)
    # ------------------------------------------------------------------

    def _format_channel_label(self, channel: str, sender: str | None) -> str:
        if sender and sender != self._current_user:
            return f"\\[{channel} \u00b7 {sender}]"
        return f"\\[{channel}]"

    def print_inbound(self, channel: str, sender: str | None, content: str) -> None:
        """Print inbound message section."""
        label = self._format_channel_label(channel, sender)
        self.console.rule(f"[bold]received {label}[/bold]", style="cyan")
        self.console.print(escape(content))
        self.console.rule(style="cyan")
        self.console.print()

    def print_processing(self, channel: str, sender: str | None) -> None:
        """Print processing section header. Tool calls/spinner appear after."""
        label = self._format_channel_label(channel, sender)
        self.console.rule(f"[bold]processing {label}[/bold]", style="yellow")

    def print_outbound(self, channel: str, sender: str | None, content: str | None) -> None:
        """Print outbound response section with Markdown rendering."""
        if not content:
            return
        label = self._format_channel_label(channel, sender)
        self.console.print()
        self.console.rule(f"[bold]response {label}[/bold]", style="green")
        md = Markdown(content)
        self.console.print(md)
        self.console.rule(style="green")
        self.console.print()

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
            tool_call_map: dict[str, ToolCall] = {}
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
                    text_content = (
                        content_to_text(msg.content)
                        if isinstance(msg.content, list)
                        else msg.content
                    )
                    self.print_assistant(text_content)
                elif msg.role == "assistant" and msg.tool_calls:
                    # Always show intermediate text (matches live behavior)
                    text_content = (
                        content_to_text(msg.content)
                        if isinstance(msg.content, list)
                        else (msg.content or "")
                    )
                    if text_content.strip():
                        self.print_assistant(text_content)
                    for tc in msg.tool_calls:
                        tool_call_map[tc.id] = tc
                    if show_tool_calls:
                        for tc in msg.tool_calls:
                            if tc.name.startswith("_"):
                                continue
                            text = format_tool_call(tc)
                            self.console.print(
                                self._indent_lines(text, "  "),
                                style="blue",
                                markup=False,
                            )
                elif msg.role == "tool":
                    if show_tool_calls and not (msg.name or "").startswith("_"):
                        result_text = (
                            content_to_text(msg.content)
                            if isinstance(msg.content, list)
                            else (msg.content or "")
                        )
                        matched_tc = tool_call_map.get(msg.tool_call_id or "")
                        if matched_tc:
                            text = format_tool_result(matched_tc, result_text)
                        else:
                            preview = result_text.split("\n")[0][:80]
                            if len(result_text) > len(preview):
                                preview += "..."
                            text = preview
                        failed = self._is_failed_tool_result(result_text)
                        indented = self._indent_lines(text, "    ")
                        style = "red" if failed else "dim"
                        self.console.print(indented, style=style, markup=False)

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
