from collections.abc import Callable
from datetime import datetime, timezone as tz
from typing import Literal
from typing import Any

from ..llm.schema import ContentPart, Message, ToolCall
from ..session.schema import SessionEntry

Role = Literal["user", "assistant", "system", "tool"]


class Conversation:
    """Stores conversation history as SessionEntry objects."""

    def __init__(
        self,
        on_message: Callable[[SessionEntry], None] | None = None,
    ):
        self._messages: list[SessionEntry] = []
        self._on_message = on_message

    def add(
        self,
        role: Role,
        content: str,
        *,
        channel: str | None = None,
        sender: str | None = None,
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        msg = Message(
            role=role,
            content=content,
            timestamp=timestamp or datetime.now(tz.utc),
        )
        entry = SessionEntry(message=msg, channel=channel, sender=sender, metadata=metadata)
        self._messages.append(entry)
        if self._on_message is not None:
            self._on_message(entry)

    def add_assistant_with_tools(
        self,
        content: str | None,
        tool_calls: list[ToolCall],
        *,
        reasoning_content: str | None = None,
        channel: str | None = None,
    ) -> None:
        """Add an assistant message that includes tool calls."""
        msg = Message(
            role="assistant",
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=tool_calls,
            timestamp=datetime.now(tz.utc),
        )
        entry = SessionEntry(message=msg, channel=channel)
        self._messages.append(entry)
        if self._on_message is not None:
            self._on_message(entry)

    def add_tool_result(
        self, tool_call_id: str, name: str, result: str | list[ContentPart],
    ) -> None:
        """Add a tool result message."""
        msg = Message(
            role="tool",
            content=result,
            tool_call_id=tool_call_id,
            name=name,
            timestamp=datetime.now(tz.utc),
        )
        entry = SessionEntry(message=msg)
        self._messages.append(entry)
        if self._on_message is not None:
            self._on_message(entry)

    def get_messages(self) -> list[SessionEntry]:
        return list(self._messages)

    def compact(self, preserve_turns: int) -> int:
        """Remove old turns, keeping only the last preserve_turns.

        A turn = one user message + all subsequent non-user messages.
        Returns number of messages removed.
        """
        turns: list[list[SessionEntry]] = []
        current_turn: list[SessionEntry] = []
        for entry in self._messages:
            if entry.role == "user" and current_turn:
                turns.append(current_turn)
                current_turn = []
            current_turn.append(entry)
        if current_turn:
            turns.append(current_turn)

        if len(turns) <= preserve_turns:
            return 0

        kept = [entry for turn in turns[-preserve_turns:] for entry in turn]
        removed = len(self._messages) - len(kept)
        self._messages = kept
        return removed

    def clear(self) -> None:
        self._messages.clear()
