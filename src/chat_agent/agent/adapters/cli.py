"""CLI channel adapter: connects terminal input/output to the AgentCore queue."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ..schema import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..core import AgentCore
    from ...cli.console import ChatConsole
    from ...cli.input import ChatInput
    from ...cli.commands import CommandHandler, CommandResult
    from ...context import Conversation, ContextBuilder
    from ...session import SessionManager
    from ...workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class CLIAdapter:
    """CLI channel adapter.

    Reads user input from a terminal (via ``ChatInput``) in a background
    thread and pushes ``InboundMessage`` items into the AgentCore queue.
    Responses are printed to the terminal via ``ChatConsole``.
    """

    channel_name = "cli"
    priority = 0

    def __init__(
        self,
        *,
        chat_input: ChatInput,
        console: ChatConsole,
        commands: CommandHandler,
        session_mgr: SessionManager,
        conversation: Conversation,
        builder: ContextBuilder,
        workspace: WorkspaceManager,
        agent_os_dir: Path,
        user_id: str,
        display_name: str,
        picker_fn: Callable[..., int | None],
    ) -> None:
        self._chat_input = chat_input
        self._console = console
        self._commands = commands
        self._session_mgr = session_mgr
        self._conversation = conversation
        self._builder = builder
        self._workspace = workspace
        self._agent_os_dir = agent_os_dir
        self._user_id = user_id
        self._display_name = display_name
        self._picker_fn = picker_fn

        self._agent: AgentCore | None = None
        self._turn_done = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # ChannelAdapter protocol
    # ------------------------------------------------------------------

    def start(self, agent: AgentCore) -> None:
        self._agent = agent
        self._turn_done.set()  # ready for first input
        self._thread = threading.Thread(
            target=self._input_loop, name="cli-input", daemon=True,
        )
        self._thread.start()

    def send(self, message: OutboundMessage) -> None:
        # Display is handled by console.print_outbound() in _process_inbound.
        # Future adapters (LINE) will use this to actually deliver the message.
        pass

    def on_turn_complete(self) -> None:
        self._turn_done.set()

    def stop(self) -> None:
        self._turn_done.set()  # unblock if waiting

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _input_loop(self) -> None:
        """Background thread: read input, handle commands, push to queue."""
        assert self._agent is not None

        while True:
            self._turn_done.wait()
            self._turn_done.clear()

            try:
                user_input = self._chat_input.get_input()
            except EOFError:
                user_input = None

            if user_input is None:
                # Ctrl-D / EOF / double Ctrl-C
                self._agent.request_shutdown(graceful=True)
                return

            # Double ESC: interactive history rollback
            if self._chat_input.wants_history_select:
                self._handle_history_select()
                self._turn_done.set()
                continue

            user_input = user_input.strip()
            if not user_input:
                self._turn_done.set()
                continue

            # Slash commands (handled locally, never enter the queue)
            if self._commands.is_command(user_input):
                should_stop = self._handle_command(user_input)
                if should_stop:
                    return
                self._turn_done.set()
                continue

            # Normal message -> enqueue
            msg = InboundMessage(
                channel="cli",
                content=user_input,
                priority=self.priority,
                sender=self._user_id,
            )
            self._agent.enqueue(msg)
            # _turn_done will be set by on_turn_complete() after processing

    def _handle_history_select(self) -> None:
        """Interactive rollback picker (double-ESC)."""
        msgs = self._conversation.get_messages()
        user_turns = [(i, m) for i, m in enumerate(msgs) if m.role == "user"]
        if not user_turns:
            return
        recent = user_turns[-10:]
        items = []
        for _idx, m in recent:
            preview = (m.content or "")[:60].replace("\n", " ")
            if len(m.content or "") > 60:
                preview += "..."
            items.append(preview)
        choice = self._picker_fn(
            items, title="\u9078\u64c7\u8981\u56de\u9000\u5230\u7684\u8f38\u5165\uff1a",
        )
        if choice is not None:
            selected_idx, selected_msg = recent[choice]
            prev_input = selected_msg.content or ""
            self._conversation._messages = self._conversation._messages[:selected_idx]
            self._session_mgr.rewrite_messages(self._conversation.get_messages())
            self._chat_input.set_prefill(prev_input)
            self._console.print_info("\u5df2\u56de\u9000\u3002")

    def _handle_command(self, text: str) -> bool:
        """Execute a slash command. Returns True if the agent should stop."""
        from ...cli.commands import CommandResult

        result = self._commands.execute(text)

        if result == CommandResult.EXIT:
            self._session_mgr.finalize("exited")
            self._console.print_goodbye()
            self._agent.request_shutdown(graceful=False)
            return True

        if result == CommandResult.CLEAR:
            self._conversation.clear()
        elif result == CommandResult.COMPACT:
            removed = self._conversation.compact(self._builder.preserve_turns)
            if removed:
                self._session_mgr.finalize("compacted")
                self._session_mgr.create(self._user_id, self._display_name)
                self._conversation._on_message = self._session_mgr.append_message
                self._console.print_info(
                    f"Context compacted: {removed} messages removed.",
                )
            else:
                self._console.print_info("Context is already compact.")
        elif result == CommandResult.RELOAD_SYSTEM_PROMPT:
            try:
                reloaded = self._workspace.get_system_prompt("brain")
                self._builder.system_prompt = reloaded.replace(
                    "{agent_os_dir}", str(self._agent_os_dir),
                )
                self._console.print_info("System prompt reloaded.")
            except FileNotFoundError as e:
                self._console.print_error(f"Failed to reload system prompt: {e}")

        return False
