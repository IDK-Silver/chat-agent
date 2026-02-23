import json
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.live import Live

from .formatter import format_tool_call, format_tool_result, format_gui_tool_call, format_gui_tool_result
from .claude_code_stream_json import parse_claude_code_stream_json_line
from ..llm.content import content_to_text
from ..llm.schema import ContentPart, ToolCall
from ..session.schema import SessionEntry


class ChatConsole:
    """Rich-based console output for chat interface."""

    def __init__(self, *, debug: bool = False, show_tool_use: bool = False) -> None:
        self.console = Console()
        self.debug = debug
        self.show_tool_use = show_tool_use
        self.gui_intent_max_chars: int | None = None
        self._current_user: str | None = None
        self._timezone: str | None = None
        self._ctx_status_provider = None

    def set_current_user(self, user_id: str) -> None:
        """Set user id for channel label formatting."""
        self._current_user = user_id

    def set_timezone(self, timezone: str) -> None:
        """Set timezone for channel display timestamps."""
        self._timezone = timezone

    def set_debug(self, enabled: bool) -> None:
        """Enable or disable debug-mode console output."""
        self.debug = enabled

    def set_show_tool_use(self, enabled: bool) -> None:
        """Enable or disable tool call/result display."""
        self.show_tool_use = enabled

    def set_ctx_status_provider(self, provider) -> None:
        """Set a callback that returns a short context status string."""
        self._ctx_status_provider = provider

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
    def _has_tool_warnings(result: str) -> bool:
        """Check whether a JSON tool result contains non-fatal warnings."""
        if not result.startswith("{"):
            return False
        try:
            payload = json.loads(result)
        except json.JSONDecodeError:
            return False
        if not isinstance(payload, dict):
            return False
        warnings = payload.get("warnings")
        return isinstance(warnings, list) and bool(warnings)

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
        has_warnings = self._has_tool_warnings(display_result)
        text = format_tool_result(tool_call, display_result)
        if not self.show_tool_use:
            if failed:
                self.print_warning(f"{tool_call.name} failed: {text}")
            elif has_warnings:
                self.print_warning(f"{tool_call.name} warnings: {text}")
            return

        indented = self._indent_lines(text, "    ")
        if failed:
            self.console.print(indented, style="red", markup=False)
        elif has_warnings:
            self.console.print(indented, style="yellow", markup=False)
        else:
            self.console.print(indented, style="dim", markup=False)

    def print_shell_stream_line(self, line: str) -> None:
        """Display a single streaming stdout line with stream-json awareness.

        Parses the line; shows tool_use events as ``[stream] ToolName``,
        ignores noisy delta/ping events.  Controlled by *show_tool_use*.
        """
        if not self.show_tool_use:
            return
        event = parse_claude_code_stream_json_line(line)
        if event.kind == "tool_use":
            self.console.print(
                f"    [stream] {event.tool_name}",
                style="dim cyan",
                markup=False,
            )
        # text and ignored events are not displayed during streaming;
        # the full result is printed by print_tool_result afterwards.

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

    def _ts_str(self, dt: datetime | None = None) -> str:
        """Format a datetime (or now) in the configured timezone."""
        tz = ZoneInfo(self._timezone) if self._timezone else None
        t = dt.astimezone(tz) if dt else datetime.now(tz)
        return t.strftime("%m/%d %H:%M:%S")

    def print_inbound(
        self, channel: str, sender: str | None, content: str,
        *, ts: datetime | None = None,
    ) -> None:
        """Print inbound message section."""
        label = self._format_channel_label(channel, sender)
        ts_str = self._ts_str(ts)
        self.console.rule(
            f"[bold]received {label}[/bold] [dim]{ts_str}[/dim]", style="cyan",
        )
        self.console.print(escape(content))
        self.console.rule(style="cyan")
        self.console.print()

    def print_processing(self, channel: str, sender: str | None) -> None:
        """Print processing section header. Tool calls/spinner appear after."""
        label = self._format_channel_label(channel, sender)
        suffix = ""
        if callable(self._ctx_status_provider):
            try:
                status = self._ctx_status_provider()
            except Exception:
                status = None
            if status:
                suffix = f" [dim]{escape(str(status))}[/dim]"
        self.console.rule(f"[bold]processing {label}[/bold]{suffix}", style="yellow")

    def print_outbound(
        self, channel: str, sender: str | None, content: str | None,
        *, ts: datetime | None = None, attachments: list[str] | None = None,
    ) -> None:
        """Print outbound response section with Markdown rendering."""
        if not content:
            return
        label = self._format_channel_label(channel, sender)
        ts_str = self._ts_str(ts)
        self.console.print()
        self.console.rule(
            f"[bold]response {label}[/bold] [dim]{ts_str}[/dim]", style="green",
        )
        md = Markdown(content)
        self.console.print(md)
        if attachments:
            from pathlib import Path
            names = ", ".join(Path(p).name for p in attachments)
            self.console.print(f"  [dim][Attachments: {escape(names)}][/dim]")
        self.console.rule(style="green")
        self.console.print()

    def print_inner_thoughts(
        self, channel: str, sender: str | None, content: str | None,
    ) -> None:
        """Print LLM inner thoughts (not sent to any channel)."""
        if not content or not content.strip():
            return
        label = self._format_channel_label(channel, sender)
        ts_str = self._ts_str()
        self.console.print()
        self.console.rule(
            f"[bold]thoughts {label}[/bold] [dim]{ts_str}[/dim]",
            style="magenta",
        )
        md = Markdown(content)
        self.console.print(md)
        self.console.rule(style="magenta")
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
        entries: list[SessionEntry],
        replay_turns: int | None,
        show_tool_calls: bool,
    ) -> None:
        """Print previous conversation history when resuming a session.

        Uses the same banner format as the live message flow so that
        resume/continue output looks identical to real-time display.
        """
        if not entries:
            return

        self.console.clear()

        # Split entries into turns; each turn starts with a user entry.
        turns: list[list[SessionEntry]] = []
        for entry in entries:
            if entry.role == "user":
                turns.append([entry])
            elif turns:
                turns[-1].append(entry)

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
            # Extract channel/sender from the user entry (first in the turn)
            user_entry = turn[0]
            channel = user_entry.channel or "cli"
            sender = user_entry.sender

            # --- received banner ---
            user_content = (user_entry.content or "")
            if isinstance(user_content, list):
                user_content = content_to_text(user_content)
            user_content = user_content.strip()
            if user_content:
                self.print_inbound(channel, sender, user_content, ts=user_entry.timestamp)

            # --- processing banner ---
            self.print_processing(channel, sender)

            # --- tool calls and intermediate text ---
            tool_call_map: dict[str, ToolCall] = {}
            last_response_text: str | None = None
            last_response_ts: datetime | None = None

            for entry in turn[1:]:
                if entry.role == "assistant" and entry.tool_calls:
                    # Intermediate assistant text
                    text_content = (
                        content_to_text(entry.content)
                        if isinstance(entry.content, list)
                        else (entry.content or "")
                    )
                    if text_content.strip():
                        self.print_assistant(text_content)
                    for tc in entry.tool_calls:
                        tool_call_map[tc.id] = tc
                    if show_tool_calls:
                        for tc in entry.tool_calls:
                            if tc.name.startswith("_"):
                                continue
                            text = format_tool_call(tc)
                            self.console.print(
                                self._indent_lines(text, "  "),
                                style="blue",
                                markup=False,
                            )
                elif entry.role == "assistant" and not entry.tool_calls:
                    # Final response text (will be shown in response banner)
                    text_content = (
                        content_to_text(entry.content)
                        if isinstance(entry.content, list)
                        else entry.content
                    )
                    last_response_text = text_content
                    last_response_ts = entry.timestamp
                elif entry.role == "tool":
                    if show_tool_calls and not (entry.name or "").startswith("_"):
                        result_text = (
                            content_to_text(entry.content)
                            if isinstance(entry.content, list)
                            else (entry.content or "")
                        )
                        matched_tc = tool_call_map.get(entry.tool_call_id or "")
                        if matched_tc:
                            text = format_tool_result(matched_tc, result_text)
                        else:
                            text = result_text
                        failed = self._is_failed_tool_result(result_text)
                        indented = self._indent_lines(text, "    ")
                        style = "red" if failed else "dim"
                        self.console.print(indented, style=style, markup=False)

            # --- response banner ---
            self.print_outbound(channel, sender, last_response_text, ts=last_response_ts)

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
