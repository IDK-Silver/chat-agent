from typing import Any, Protocol

from .schema import LLMResponse, Message, ToolDefinition


class LLMClient(Protocol):
    def chat(
        self,
        messages: list[Message],
        response_schema: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> str:
        """Send messages and return assistant response."""
        ...

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send messages with tool definitions and return response that may include tool calls."""
        ...


class ConversationCompactionClient(Protocol):
    def compact_messages(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
    ) -> list[Message]:
        """Compact a rendered conversation history for future turns."""
        ...
