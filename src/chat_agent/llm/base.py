from typing import Protocol

from .schema import Message


class LLMClient(Protocol):
    def chat(self, messages: list[Message]) -> str:
        """Send messages and return assistant response."""
        ...
