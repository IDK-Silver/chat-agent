from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys


COMMANDS = {
    "/help": "Show available commands",
    "/clear": "Clear conversation history",
    "/shutdown": "Exit with memory saving",
    "/exit": "Exit immediately (no save)",
}

_PROMPT_REFRESH_INTERVAL_SECONDS = 1.0


class CommandCompleter(Completer):
    """Completer for slash commands."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # Only complete at the start of input and when starting with /
        if not text.startswith("/"):
            return
        # Don't complete if there's content after a space
        if " " in text:
            return

        for cmd, desc in COMMANDS.items():
            if cmd.startswith(text):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display_meta=desc,
                )


class ChatInput:
    """Prompt toolkit based input with history and multiline support."""

    def __init__(self, timezone: str = "Asia/Taipei", bottom_toolbar=None) -> None:
        self.timezone = timezone
        history_dir = Path.home() / ".chat-agent"
        history_dir.mkdir(exist_ok=True)
        history_file = history_dir / "history"

        self._bindings = self._create_bindings()
        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_file)),
            key_bindings=self._bindings,
            completer=CommandCompleter(),
            complete_while_typing=True,
            multiline=True,
            prompt_continuation="... ",
            bottom_toolbar=bottom_toolbar,
        )

    def _create_bindings(self) -> KeyBindings:
        """Create key bindings for multiline editing."""
        bindings = KeyBindings()

        @bindings.add(Keys.Enter)
        def submit(event):
            """Submit on Enter (single line) or when line ends with newline."""
            buffer = event.app.current_buffer
            text = buffer.text

            # Submit if empty or single line
            if not text or "\n" not in text:
                buffer.validate_and_handle()
            else:
                # Insert newline for multiline editing
                buffer.insert_text("\n")

        @bindings.add(Keys.ControlJ)  # Ctrl+Enter alternative
        def force_newline(event):
            """Force insert newline."""
            event.app.current_buffer.insert_text("\n")

        return bindings

    def _get_prompt(self) -> HTML:
        """Generate prompt with current date and time."""
        now = datetime.now(ZoneInfo(self.timezone))
        # Format: 02/05-11:32 PM
        time_str = now.strftime("%m/%d-%I:%M %p")
        return HTML(f"<style fg='#888888'>{time_str}</style> &gt; ")

    def get_input(self) -> str | None:
        """
        Get user input with prompt.

        Returns:
            User input string, or None on EOF/keyboard interrupt.
        """
        try:
            return self._session.prompt(
                self._get_prompt,
                refresh_interval=_PROMPT_REFRESH_INTERVAL_SECONDS,
            )
        except (EOFError, KeyboardInterrupt):
            return None
