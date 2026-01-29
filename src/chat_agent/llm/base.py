from typing import Protocol

from .schema import LLMResponse, Message, ToolDefinition


class LLMClient(Protocol):
    def chat(self, messages: list[Message]) -> str:
        """Send messages and return assistant response."""
        ...

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Send messages with tool definitions and return response that may include tool calls."""
        ...
