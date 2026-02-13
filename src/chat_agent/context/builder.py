import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from ..llm.base import Message
from .conversation import Conversation

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Assembles context to send to LLM."""

    def __init__(
        self,
        system_prompt: str | None = None,
        timezone: str = "Asia/Taipei",
        current_user: str | None = None,
        working_dir: Path | None = None,
        boot_files: list[str] | None = None,
        max_chars: int = 400_000,
        preserve_turns: int = 6,
    ):
        self.system_prompt = system_prompt
        self.timezone = timezone
        self.current_user = current_user
        self.working_dir = working_dir
        self.boot_files = boot_files
        self.max_chars = max_chars
        self.preserve_turns = preserve_turns
        self.last_total_chars: int = 0

    def _build_runtime_context(self) -> str:
        """Build runtime context string for session-specific values."""
        parts: list[str] = []
        if self.current_user:
            parts.append(f"current_user: {self.current_user}")
        return "\n".join(parts)

    def _read_boot_files(self) -> str | None:
        """Read boot files from disk, resolve placeholders, return combined content."""
        if not self.working_dir or not self.boot_files:
            return None

        sections: list[str] = []
        for rel_path in self.boot_files:
            # Resolve {current_user} placeholder
            if self.current_user:
                rel_path = rel_path.replace("{current_user}", self.current_user)

            full_path = self.working_dir / rel_path
            try:
                content = full_path.read_text(encoding="utf-8")
                sections.append(
                    f'<file path="{rel_path}">\n{content.rstrip()}\n</file>'
                )
            except FileNotFoundError:
                sections.append(
                    f'<file path="{rel_path}">\n[File not found]\n</file>'
                )

        if not sections:
            return None
        return "\n\n".join(sections)

    @staticmethod
    def _split_into_turns(conv_messages: list[Message]) -> list[list[Message]]:
        """Split conversation messages into turns (user msg + subsequent non-user msgs)."""
        turns: list[list[Message]] = []
        current_turn: list[Message] = []

        for msg in conv_messages:
            if msg.role == "user" and current_turn:
                turns.append(current_turn)
                current_turn = []
            current_turn.append(msg)

        if current_turn:
            turns.append(current_turn)

        return turns

    def _truncate_if_needed(
        self,
        prefix_messages: list[Message],
        conv_messages: list[Message],
    ) -> tuple[list[Message], bool]:
        """Truncate old turns if total chars exceed max_chars.

        Returns (kept_conv_messages, was_truncated).
        """
        prefix_chars = sum(len(m.content or "") for m in prefix_messages)
        conv_chars = sum(len(m.content or "") for m in conv_messages)
        total = prefix_chars + conv_chars

        if total <= self.max_chars:
            return conv_messages, False

        turns = self._split_into_turns(conv_messages)
        if len(turns) <= self.preserve_turns:
            return conv_messages, False

        # Keep the last preserve_turns turns
        kept_turns = turns[-self.preserve_turns:]
        kept_messages = [msg for turn in kept_turns for msg in turn]
        return kept_messages, True

    def build(self, conversation: Conversation) -> list[Message]:
        """Build context from conversation history."""
        prefix: list[Message] = []
        tz = ZoneInfo(self.timezone)

        if self.system_prompt:
            prefix.append(Message(role="system", content=self.system_prompt))

        # Inject runtime context as separate message (cache-friendly)
        runtime_ctx = self._build_runtime_context()
        if runtime_ctx:
            prefix.append(
                Message(role="system", content=f"[Runtime Context]\n{runtime_ctx}")
            )

        # Inject boot files content
        boot_content = self._read_boot_files()
        if boot_content:
            prefix.append(
                Message(role="system", content=f"[Boot Context]\n\n{boot_content}")
            )

        # Process conversation messages with timestamp prefixes
        conv_messages: list[Message] = []
        for msg in conversation.get_messages():
            content = msg.content
            if msg.timestamp and msg.role == "user" and content:
                local_time = msg.timestamp.astimezone(tz)
                content = f"[{local_time.strftime('%Y-%m-%d %H:%M')}] {content}"

            conv_messages.append(
                Message(
                    role=msg.role,
                    content=content,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )
            )

        # Truncate old turns if context too large
        kept_conv, truncated = self._truncate_if_needed(prefix, conv_messages)

        if truncated:
            dropped = len(conv_messages) - len(kept_conv)
            prefix.append(
                Message(
                    role="system",
                    content=(
                        f"[Context truncated: {dropped} older messages removed "
                        f"to fit context window. {len(kept_conv)} messages retained.]"
                    ),
                )
            )

        final = prefix + kept_conv
        self.last_total_chars = sum(len(m.content or "") for m in final)
        return final

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
