from dataclasses import dataclass
from typing import Protocol


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str


class LLMClient(Protocol):
    def chat(self, messages: list[Message]) -> str:
        """Send messages and return assistant response."""
        ...
