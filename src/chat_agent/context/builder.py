from zoneinfo import ZoneInfo

from ..llm.base import Message
from .conversation import Conversation


class ContextBuilder:
    """Assembles context to send to LLM."""

    def __init__(
        self,
        system_prompt: str | None = None,
        timezone: str = "Asia/Taipei",
        current_user: str | None = None,
    ):
        self.system_prompt = system_prompt
        self.timezone = timezone
        self.current_user = current_user

    def _build_runtime_context(self) -> str:
        """Build runtime context string for session-specific values."""
        parts: list[str] = []
        if self.current_user:
            parts.append(f"current_user: {self.current_user}")
        return "\n".join(parts)

    def build(self, conversation: Conversation) -> list[Message]:
        """Build context from conversation history."""
        messages = []
        tz = ZoneInfo(self.timezone)

        if self.system_prompt:
            messages.append(Message(role="system", content=self.system_prompt))

        # Inject runtime context as separate message (cache-friendly)
        runtime_ctx = self._build_runtime_context()
        if runtime_ctx:
            messages.append(
                Message(role="system", content=f"[Runtime Context]\n{runtime_ctx}")
            )

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

    def build_with_reminders(
        self,
        conversation: Conversation,
        reminders: list[str],
    ) -> list[Message]:
        """Build context with reminders appended to system prompt."""
        messages = self.build(conversation)
        if not reminders or not messages or messages[0].role != "system":
            return messages
        bullet_list = "\n".join(f"- {r}" for r in reminders)
        base = messages[0].content or ""
        messages[0] = Message(
            role="system",
            content=base + "\n\n## Reminders for This Response\n\n" + bullet_list,
        )
        return messages
