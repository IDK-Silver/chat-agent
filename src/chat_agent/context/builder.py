from ..llm.base import Message
from .conversation import Conversation


class ContextBuilder:
    """Assembles context to send to LLM."""

    def __init__(self, system_prompt: str | None = None):
        self.system_prompt = system_prompt

    def build(self, conversation: Conversation) -> list[Message]:
        """
        Build context from conversation history.

        Future extensions:
        - Dynamic system prompt generation
        - Memory injection
        - History compression/summarization
        - Additional context injection
        """
        messages = []

        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))

        messages.extend(conversation.get_messages())

        return messages
