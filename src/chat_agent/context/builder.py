from datetime import datetime
from zoneinfo import ZoneInfo

from ..llm.base import Message
from .conversation import Conversation


class ContextBuilder:
    """Assembles context to send to LLM."""

    def __init__(
        self,
        system_prompt: str | None = None,
        timezone: str = "Asia/Taipei",
    ):
        self.system_prompt = system_prompt
        self.timezone = timezone

    def build(self, conversation: Conversation) -> list[Message]:
        """
        Build context from conversation history.

        Injects current time into system prompt and formats message timestamps.
        """
        messages = []
        tz = ZoneInfo(self.timezone)

        # System prompt with current time injected
        if self.system_prompt:
            current_time = datetime.now(tz)
            time_header = f"[Current Time: {current_time.strftime('%Y-%m-%d %H:%M')} ({self.timezone})]\n\n"
            messages.append(Message(role="system", content=time_header + self.system_prompt))

        # Process conversation messages with timestamp prefixes
        for msg in conversation.get_messages():
            content = msg.content
            if msg.timestamp and msg.role == "user" and content:
                local_time = msg.timestamp.astimezone(tz)
                content = f"[{local_time.strftime('%Y-%m-%d %H:%M')}] {content}"

            messages.append(
                Message(
                    role=msg.role,
                    content=content,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )
            )

        return messages

    def build_with_review(
        self,
        conversation: Conversation,
        prefetch_results: list[str],
        reminders: list[str],
    ) -> list[Message]:
        """Build context with pre-fetched data appended to system prompt."""
        messages = self.build(conversation)

        if not messages or messages[0].role != "system":
            return messages

        extra_sections: list[str] = []
        if prefetch_results:
            extra_sections.append(
                "## Pre-loaded Context\n\n" + "\n\n".join(prefetch_results)
            )
        if reminders:
            bullet_list = "\n".join(f"- {r}" for r in reminders)
            extra_sections.append(
                "## Reminders for This Response\n\n" + bullet_list
            )

        if extra_sections:
            base = messages[0].content or ""
            messages[0] = Message(
                role="system",
                content=base + "\n\n" + "\n\n".join(extra_sections),
            )

        return messages
