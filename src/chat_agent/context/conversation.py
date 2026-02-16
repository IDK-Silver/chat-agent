from collections.abc import Callable
from datetime import datetime, timezone as tz
from typing import Literal

from ..llm.schema import ContentPart, Message, ToolCall

Role = Literal["user", "assistant", "system", "tool"]


class Conversation:
    """Stores conversation history."""

    def __init__(
        self,
        on_message: Callable[[Message], None] | None = None,
    ):
        self._messages: list[Message] = []
        self._on_message = on_message

    def add(
        self,
        role: Role,
        content: str,
        timestamp: datetime | None = None,
    ) -> None:
        msg = Message(
            role=role,
            content=content,
            timestamp=timestamp or datetime.now(tz.utc),
        )
        self._messages.append(msg)
        if self._on_message is not None:
            self._on_message(msg)

    def add_assistant_with_tools(
        self, content: str | None, tool_calls: list[ToolCall]
    ) -> None:
        """Add an assistant message that includes tool calls."""
        msg = Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            timestamp=datetime.now(tz.utc),
        )
        self._messages.append(msg)
        if self._on_message is not None:
            self._on_message(msg)

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
        self._messages.append(msg)
        if self._on_message is not None:
            self._on_message(msg)

    def get_messages(self) -> list[Message]:
        return list(self._messages)

    def compact(self, preserve_turns: int) -> int:
        """Remove old turns, keeping only the last preserve_turns.

        A turn = one user message + all subsequent non-user messages.
        Returns number of messages removed.
        """
        turns: list[list[Message]] = []
        current_turn: list[Message] = []
        for msg in self._messages:
            if msg.role == "user" and current_turn:
                turns.append(current_turn)
                current_turn = []
            current_turn.append(msg)
        if current_turn:
            turns.append(current_turn)

        if len(turns) <= preserve_turns:
            return 0

        kept = [msg for turn in turns[-preserve_turns:] for msg in turn]
        removed = len(self._messages) - len(kept)
        self._messages = kept
        return removed

    def clear(self) -> None:
        self._messages.clear()
