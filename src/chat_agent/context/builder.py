import logging
from datetime import datetime
from pathlib import Path

from ..llm.base import Message
from ..llm.content import content_char_estimate, content_to_text
from ..llm.schema import ContentPart, ToolCall
from ..timezone_utils import parse_timezone_spec
from .conversation import Conversation

logger = logging.getLogger(__name__)

_TOOL_BOOT_CALL_ID = "boot_ctx_0"
_TOOL_BOOT_NAME = "read_startup_context"


class ContextBuilder:
    """Assembles context to send to LLM."""

    def __init__(
        self,
        system_prompt: str | None = None,
        timezone: str = "UTC+8",
        agent_os_dir: Path | None = None,
        boot_files: list[str] | None = None,
        boot_files_as_tool: list[str] | None = None,
        max_chars: int = 400_000,
        preserve_turns: int = 6,
        provider: str = "openai",
        cache_ttl: str | None = None,
    ):
        self.system_prompt = system_prompt
        self.timezone = timezone
        self.agent_os_dir = agent_os_dir
        self.boot_files = boot_files
        self.boot_files_as_tool = boot_files_as_tool
        self.max_chars = max_chars
        self.preserve_turns = preserve_turns
        self.provider = provider
        self.cache_ttl = cache_ttl
        self.last_total_chars: int = 0
        self.last_was_truncated: bool = False
        self._boot_content_cache: str | None = None
        self._tool_boot_content_cache: str | None = None

    def reload_boot_files(self) -> None:
        """Read boot files from disk and cache the result.

        Called on init, resume, and context_refresh.
        """
        self._boot_content_cache = self._read_file_sections(self.boot_files)
        self._tool_boot_content_cache = self._read_file_sections(
            self.boot_files_as_tool,
        )

    def estimate_chars(self, conversation: Conversation) -> int:
        """Recompute last_total_chars from current state (lightweight)."""
        sys_chars = content_char_estimate(self.system_prompt or "", self.provider)
        runtime_ctx = self._build_runtime_context()
        if runtime_ctx:
            sys_chars += content_char_estimate(
                f"[Runtime Context]\n{runtime_ctx}", self.provider,
            )
        boot = self._boot_content_cache
        if boot:
            sys_chars += content_char_estimate(
                f"[Core Rules]\n\n{boot}", self.provider,
            )
        tool_boot = self._tool_boot_content_cache
        if tool_boot:
            sys_chars += content_char_estimate(tool_boot, self.provider)
        conv_chars = sum(
            content_char_estimate(m.content, self.provider)
            for m in conversation.get_messages()
        )
        self.last_total_chars = sys_chars + conv_chars
        return self.last_total_chars

    def update_system_prompt(self, system_prompt: str) -> None:
        """Replace the resolved system prompt (e.g. after date change)."""
        self.system_prompt = system_prompt

    def _build_runtime_context(self) -> str:
        """Build runtime context string for session-specific values."""
        parts: list[str] = []
        if self.agent_os_dir:
            parts.append(f"agent_os_dir: {self.agent_os_dir}")
        return "\n".join(parts)

    def _read_file_sections(self, file_list: list[str] | None) -> str | None:
        """Read files from disk and return combined <file> content."""
        if not self.agent_os_dir or not file_list:
            return None

        sections: list[str] = []
        for rel_path in file_list:
            full_path = self.agent_os_dir / rel_path
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

    def _build_tool_boot_messages(self) -> list[Message]:
        """Build synthetic tool-call/result pair for tool-tier boot files."""
        content = self._tool_boot_content_cache
        if not content:
            return []

        call_msg = Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id=_TOOL_BOOT_CALL_ID,
                    name=_TOOL_BOOT_NAME,
                    arguments={},
                ),
            ],
        )
        result_msg = Message(
            role="tool",
            content=content,
            tool_call_id=_TOOL_BOOT_CALL_ID,
            name=_TOOL_BOOT_NAME,
        )
        return [call_msg, result_msg]

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
        prefix_chars = sum(
            content_char_estimate(m.content, self.provider) for m in prefix_messages
        )
        conv_chars = sum(
            content_char_estimate(m.content, self.provider) for m in conv_messages
        )
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

    @staticmethod
    def _inject_conversation_cache_breakpoint(
        kept_conv: list[Message],
        cache_ctrl: dict[str, str],
    ) -> list[Message]:
        """Inject BP3: mark last eligible message before current turn for caching.

        Eligible = non-tool, non-assistant-with-tool_calls, non-empty str content.
        This allows the entire conversation prefix to be cached by the provider.
        """
        # Find the last user message (current turn start)
        last_user_pos = None
        for i in range(len(kept_conv) - 1, -1, -1):
            if kept_conv[i].role == "user":
                last_user_pos = i
                break

        if last_user_pos is None or last_user_pos == 0:
            return kept_conv

        # Walk backwards to find an eligible message for cache breakpoint.
        # Skip tool messages (converter flattens ContentPart to plain text)
        # and assistant+tool_calls (converter forces content to str|None).
        for i in range(last_user_pos - 1, -1, -1):
            msg = kept_conv[i]
            if msg.role == "tool":
                continue
            if msg.role == "assistant" and msg.tool_calls:
                continue
            if not isinstance(msg.content, str) or not msg.content:
                continue
            # Replace content with ContentPart carrying cache_control
            kept_conv = list(kept_conv)
            kept_conv[i] = Message(
                role=msg.role,
                content=[ContentPart(
                    type="text",
                    text=msg.content,
                    cache_control=cache_ctrl,
                )],
                tool_calls=msg.tool_calls,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
                timestamp=msg.timestamp,
            )
            break

        return kept_conv

    def _cache_control_dict(self) -> dict[str, str] | None:
        """Build cache_control dict from cache_ttl setting."""
        if not self.cache_ttl:
            return None
        ctrl: dict[str, str] = {"type": "ephemeral"}
        if self.cache_ttl != "ephemeral":
            ctrl["ttl"] = self.cache_ttl
        return ctrl

    def build(self, conversation: Conversation) -> list[Message]:
        """Build context from conversation history."""
        prefix: list[Message] = []
        tz = parse_timezone_spec(self.timezone)
        cache_ctrl = self._cache_control_dict()

        # BP1: system prompt (most stable, largest block)
        if self.system_prompt:
            if cache_ctrl:
                prefix.append(Message(role="system", content=[
                    ContentPart(
                        type="text",
                        text=self.system_prompt,
                        cache_control=cache_ctrl,
                    ),
                ]))
            else:
                prefix.append(Message(role="system", content=self.system_prompt))

        # Inject runtime context as separate message (no cache: changes per session)
        runtime_ctx = self._build_runtime_context()
        if runtime_ctx:
            prefix.append(
                Message(role="system", content=f"[Runtime Context]\n{runtime_ctx}")
            )

        # BP2: system-tier boot files (snapshot-based: cached by reload_boot_files)
        boot_content = self._boot_content_cache
        if boot_content:
            text = f"[Core Rules]\n\n{boot_content}"
            if cache_ctrl:
                prefix.append(Message(role="system", content=[
                    ContentPart(
                        type="text",
                        text=text,
                        cache_control=cache_ctrl,
                    ),
                ]))
            else:
                prefix.append(Message(role="system", content=text))

        # Inject tool-tier boot files as synthetic tool-call/result pair
        prefix.extend(self._build_tool_boot_messages())

        # Process conversation messages with timestamp prefixes
        all_msgs = conversation.get_messages()
        now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

        # Find last user message index for "now" marker
        last_user_idx = None
        for i in range(len(all_msgs) - 1, -1, -1):
            if all_msgs[i].role == "user":
                last_user_idx = i
                break

        conv_messages: list[Message] = []
        for i, msg in enumerate(all_msgs):
            content = msg.content

            # Inject [channel, from sender] tag for user messages
            if msg.role == "user" and isinstance(content, str) and content:
                channel = getattr(msg, "channel", None)
                sender = getattr(msg, "sender", None)
                if channel and sender:
                    content = f"[{channel}, from {sender}] {content}"
                elif channel:
                    content = f"[{channel}] {content}"

            if msg.timestamp and msg.role in ("user", "assistant") and isinstance(content, str) and content:
                local_time = msg.timestamp.astimezone(tz)
                ts = local_time.strftime("%Y-%m-%d %H:%M")
                if msg.role == "user" and i == last_user_idx:
                    content = f"[{ts}, now {now_str}] {content}"
                else:
                    content = f"[{ts}] {content}"

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

        # BP3: cache conversation prefix before current turn
        if cache_ctrl and kept_conv:
            kept_conv = self._inject_conversation_cache_breakpoint(
                kept_conv, cache_ctrl,
            )

        if truncated:
            # Fixed text to avoid invalidating BP3 cache when counts change.
            prefix.append(
                Message(
                    role="system",
                    content="[Context truncated: older messages removed to fit context window.]",
                )
            )

        final = prefix + kept_conv
        self.last_total_chars = sum(
            content_char_estimate(m.content, self.provider) for m in final
        )
        self.last_was_truncated = truncated
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
        reminder_text = "## Reminders for This Response\n\n" + bullet_list
        # When caching is active, insert reminders as separate message
        # to preserve BP1 cache_control on the system prompt.
        if self.cache_ttl and isinstance(messages[0].content, list):
            messages.insert(1, Message(role="system", content=reminder_text))
        else:
            base = content_to_text(messages[0].content)
            messages[0] = Message(
                role="system",
                content=base + "\n\n" + reminder_text,
            )
        return messages
