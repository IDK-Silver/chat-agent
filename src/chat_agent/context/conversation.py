from ..llm.base import Message


class Conversation:
    """Stores conversation history."""

    def __init__(self):
        self._messages: list[Message] = []

    def add(self, role: str, content: str) -> None:
        self._messages.append(Message(role=role, content=content))

    def get_messages(self) -> list[Message]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()
