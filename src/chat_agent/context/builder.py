from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..llm.base import Message
from ..llm.schema import ContentPart, ToolCall, make_tool_result_message
from ..send_message_batch_guidance import (
    all_channel_reminder_variants,
    build_channel_reminders,
)
from ..turn_timing import build_turn_timing_notice, parse_turn_timing_info
from ..timezone_utils import localise as tz_localise
from .conversation import Conversation

if TYPE_CHECKING:
    from ..agent.note_store import NoteStore

_TOOL_BOOT_CALL_ID = "boot_ctx_0"
_TOOL_BOOT_NAME = "read_startup_context"

_PINNED_CALL_ID = "pinned_ctx_0"
_PINNED_TOOL_NAME = "read_pinned_context"

_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class ContextBuilder:
    """Assembles context to send to LLM."""

    # Channel-agnostic reminders keyed by feature name.
    _GENERAL_REMINDERS: dict[str, str] = {
        "memory": "(memory: search before answering from memory; edit to save new information)",
    }
    _DECISION_REMINDER_LABEL = "[Decision Reminder]"
    _DECISION_REMINDER_TEMPLATE = (
        "Keep {anchors} in mind before acting. Verify constraints, commitments, "
        "blocked state, cooldown, and current risk. Then decide send_message, "
        "schedule_action, or silent wait."
    )
    _DECISION_REMINDER_WITH_VALUES_TEMPLATE = (
        "Core values to embody:\n{values}\n"
        "Verify constraints from {anchors}, then decide."
    )

    def __init__(
        self,
        system_prompt: str | None = None,
        timezone: str | None = None,
        agent_os_dir: Path | None = None,
        boot_files: list[str] | None = None,
        boot_files_as_tool: list[str] | None = None,
        preserve_turns: int = 6,
        provider: str = "openai",
        cache_ttl: str | None = None,
        format_reminders: dict[str, bool] | None = None,
        decision_reminder: dict[str, object] | None = None,
        send_message_batch_guidance: bool = False,
        note_store: NoteStore | None = None,
    ):
        self.system_prompt = system_prompt
        self.timezone = timezone
        self.agent_os_dir = agent_os_dir
        self.boot_files = boot_files
        self.boot_files_as_tool = boot_files_as_tool
        self.preserve_turns = preserve_turns
        self.provider = provider
        self.cache_ttl = cache_ttl
        self._format_reminders = format_reminders or {}
        self._channel_reminders = build_channel_reminders(
            enabled=send_message_batch_guidance,
        )
        cfg = decision_reminder or {}
        self._decision_reminder_enabled = bool(cfg.get("enabled"))
        self._decision_reminder_files = [
            str(path)
            for path in (cfg.get("files") or [])
            if isinstance(path, str) and path
        ]
        inline_raw = cfg.get("inline_section")
        inline = inline_raw if isinstance(inline_raw, dict) else {}
        self._inline_section_file: str = str(inline.get("file", ""))
        self._inline_section_header: str = str(inline.get("header", ""))
        self._boot_content_cache: str | None = None
        self._tool_boot_segments: list[tuple[str, str]] = []
        self._pinned_segments: list[tuple[str, str]] = []
        self._core_values_cache: str | None = None
        self._note_store = note_store

    @classmethod
    def channel_reminder_variants(cls) -> tuple[str, ...]:
        """Return all channel reminder variants used by runtime prompting."""
        return all_channel_reminder_variants()

    def reload_boot_files(self) -> None:
        """Read boot files from disk and cache the result.

        Called on init, resume, context_refresh, and overflow recovery.
        """
        self._boot_content_cache = self._read_file_sections(self.boot_files)
        self._tool_boot_segments = self._read_file_segments(
            self.boot_files_as_tool,
        )
        self._pinned_segments = self._read_file_segments(
            self._load_pinned_paths(),
        )
        self._core_values_cache = self._extract_section_from_disk(
            self._inline_section_file,
            self._inline_section_header,
        )

    def update_system_prompt(self, system_prompt: str) -> None:
        """Replace the resolved system prompt (e.g. after date change)."""
        self.system_prompt = system_prompt

    @staticmethod
    def _append_text_block(content: str, block: str | None) -> str:
        """Append one note block while keeping the original user text intact."""
        if not block:
            return content
        if not content:
            return block
        return f"{content}\n\n{block}"

    def _build_latest_turn_runtime_context(self, entry) -> str | None:
        """Build per-turn runtime context using the frozen turn metadata snapshot."""
        parts: list[str] = []
        timing = parse_turn_timing_info(entry)
        if timing is not None and (self.agent_os_dir is not None or self.timezone is not None):
            now_local = tz_localise(timing.processing_started_at)
            day = _DAY_NAMES[now_local.weekday()]
            parts.append(
                now_local.strftime(f"current_local_time: %Y-%m-%d ({day}) %H:%M")
            )
        if self.agent_os_dir:
            parts.append(f"agent_os_dir: {self.agent_os_dir}")
        if not parts:
            return None
        return f"[Runtime Context]\n{'\n'.join(parts)}"

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

    def _read_file_segments(
        self, file_list: list[str] | None,
    ) -> list[tuple[str, str]]:
        """Read files from disk and return per-file (path, content) tuples.

        Each file becomes a separate cache block so unchanged files keep
        their cache hit when other files change (e.g. after archive).
        """
        if not self.agent_os_dir or not file_list:
            return []
        segments: list[tuple[str, str]] = []
        for rel_path in file_list:
            full_path = self.agent_os_dir / rel_path
            try:
                content = full_path.read_text(encoding="utf-8").rstrip()
            except FileNotFoundError:
                content = "[File not found]"
            segments.append((rel_path, content))
        return segments

    def _extract_section_from_disk(
        self, rel_path: str, header: str,
    ) -> str | None:
        """Extract a markdown section from a boot file and cache it.

        Called at reload time so the result is stable across turns.
        Returns the bullet-list body of the section (without the header),
        or None if the file/section is not found.
        """
        if not self.agent_os_dir or not rel_path or not header:
            return None
        full_path = self.agent_os_dir / rel_path
        try:
            content = full_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

        lines = content.splitlines()
        collecting = False
        section_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped == header:
                collecting = True
                continue
            if collecting and stripped.startswith("## "):
                break
            if collecting and stripped and not stripped.startswith("<!--"):
                section_lines.append(stripped)

        return "\n".join(section_lines) if section_lines else None

    def _load_pinned_paths(self) -> list[str] | None:
        """Load pinned context paths from the registry file."""
        if not self.agent_os_dir:
            return None
        registry = self.agent_os_dir / "state" / "pinned_context.json"
        if not registry.exists():
            return None
        try:
            import json
            data = json.loads(registry.read_text(encoding="utf-8"))
            pins = data.get("pins", [])
            return [p["path"] for p in pins if isinstance(p, dict) and p.get("path")]
        except Exception:
            return None

    def _build_tool_boot_messages(self) -> list[Message]:
        """Build synthetic tool-call/result messages for tool-tier boot files.

        Each file gets its own tool result so Anthropic's backward prefix
        checking can cache unchanged files independently.
        """
        segments = self._tool_boot_segments
        if not segments:
            return []

        # One assistant message with parallel tool calls
        tool_calls = [
            ToolCall(
                id=f"{_TOOL_BOOT_CALL_ID}_{i}",
                name=_TOOL_BOOT_NAME,
                arguments={"file": rel_path},
            )
            for i, (rel_path, _content) in enumerate(segments)
        ]
        call_msg = Message(
            role="assistant",
            content=None,
            tool_calls=tool_calls,
        )

        # One tool result per file (separate cache blocks)
        result_msgs = [
            make_tool_result_message(
                tool_call_id=f"{_TOOL_BOOT_CALL_ID}_{i}",
                name=_TOOL_BOOT_NAME,
                content=f'<file path="{rel_path}">\n{content}\n</file>',
            )
            for i, (rel_path, content) in enumerate(segments)
        ]
        return [call_msg] + result_msgs

    def _build_pinned_context_messages(self) -> list[Message]:
        """Build synthetic tool-call/result messages for pinned context files."""
        segments = self._pinned_segments
        if not segments:
            return []
        tool_calls = [
            ToolCall(
                id=f"{_PINNED_CALL_ID}_{i}",
                name=_PINNED_TOOL_NAME,
                arguments={"file": rel_path},
            )
            for i, (rel_path, _content) in enumerate(segments)
        ]
        call_msg = Message(
            role="assistant",
            content=None,
            tool_calls=tool_calls,
        )
        result_msgs = [
            make_tool_result_message(
                tool_call_id=f"{_PINNED_CALL_ID}_{i}",
                name=_PINNED_TOOL_NAME,
                content=f'<file path="{rel_path}">\n{content}\n</file>',
            )
            for i, (rel_path, content) in enumerate(segments)
        ]
        return [call_msg] + result_msgs

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

    @staticmethod
    def _inject_conversation_cache_breakpoint(
        kept_conv: list[Message],
        cache_ctrl: dict[str, str],
    ) -> list[Message]:
        """Inject BP3: mark last eligible message before current turn for caching.

        Eligible = non-system, non-tool, non-assistant-with-tool_calls,
        non-empty str content.
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
        # Skip system/tool messages and assistant+tool_calls. Per-turn dynamic
        # notes must stay on the latest user turn rather than create fresh
        # system messages that compete for the breakpoint.
        for i in range(last_user_pos - 1, -1, -1):
            msg = kept_conv[i]
            if msg.role == "system":
                continue
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
                reasoning_content=msg.reasoning_content,
                reasoning_details=msg.reasoning_details,
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

    @staticmethod
    def _find_last_user_message_index(messages: list[Message]) -> int | None:
        """Return the index of the latest user message in conversation order."""
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                return i
        return None

    @staticmethod
    def _format_decision_anchor_list(files: list[str]) -> str:
        """Render short file anchors, keeping basenames unless ambiguous."""
        if not files:
            return "key rules"

        counts: dict[str, int] = {}
        basenames = [Path(path).name or path for path in files]
        for name in basenames:
            counts[name] = counts.get(name, 0) + 1

        rendered = [
            name if counts[name] == 1 else path
            for path, name in zip(files, basenames, strict=False)
        ]
        if len(rendered) == 1:
            return rendered[0]
        if len(rendered) == 2:
            return f"{rendered[0]} and {rendered[1]}"
        return ", ".join(rendered[:-1]) + f", and {rendered[-1]}"

    def _build_decision_reminder(self) -> str | None:
        """Build the latest-turn decision reminder text.

        When core values are cached (via inline_section config), inline them
        directly instead of just referencing a file name.
        """
        if not self._decision_reminder_enabled:
            return None
        anchors = self._format_decision_anchor_list(
            self._decision_reminder_files,
        )
        if self._core_values_cache:
            body = self._DECISION_REMINDER_WITH_VALUES_TEMPLATE.format(
                values=self._core_values_cache,
                anchors=anchors,
            )
        else:
            body = self._DECISION_REMINDER_TEMPLATE.format(anchors=anchors)
        return f"{self._DECISION_REMINDER_LABEL}\n{body}"

    def _build_notes_block(self) -> str | None:
        """Build the agent notes block for context injection."""
        if self._note_store is None:
            return None
        return self._note_store.format_context_block()

    def build(self, conversation: Conversation) -> list[Message]:
        """Build context from conversation history."""
        prefix: list[Message] = []
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

        # Inject pinned context files (agent-registered, loaded at reload time)
        prefix.extend(self._build_pinned_context_messages())

        # Process conversation messages with timestamp prefixes
        all_msgs = conversation.get_messages()
        last_user_idx = self._find_last_user_message_index(all_msgs)

        conv_messages: list[Message] = []
        for idx, msg in enumerate(all_msgs):
            content = msg.content

            # Inject [channel, from sender] tag for user messages
            if msg.role == "user" and isinstance(content, str) and content:
                channel = getattr(msg, "channel", None)
                sender = getattr(msg, "sender", None)
                if channel and sender:
                    content = f"[{channel}, from {sender}] {content}"
                elif channel:
                    content = f"[{channel}] {content}"
                # Append per-channel format reminder
                if channel and self._format_reminders.get(channel):
                    reminder = self._channel_reminders.get(channel)
                    if reminder:
                        content = f"{content}\n{reminder}"
                # Append general reminders
                for key, text in self._GENERAL_REMINDERS.items():
                    if self._format_reminders.get(key):
                        content = f"{content}\n{text}"
                # Per-turn decision reminders must stay on the latest user
                # message. Do not inject them into BP1/BP2 system-tier prefix,
                # which would both weaken recency and break prompt-cache
                # invariants across turns.
                if idx == last_user_idx:
                    runtime_ctx = self._build_latest_turn_runtime_context(msg)
                    content = self._append_text_block(content, runtime_ctx)
                    timing_notice = build_turn_timing_notice(msg)
                    content = self._append_text_block(content, timing_notice)
                    reminder = self._build_decision_reminder()
                    content = self._append_text_block(content, reminder)
                    notes_block = self._build_notes_block()
                    content = self._append_text_block(content, notes_block)

            if msg.timestamp and msg.role in ("user", "assistant") and isinstance(content, str) and content:
                local_time = tz_localise(msg.timestamp)
                day = _DAY_NAMES[local_time.weekday()]
                ts = local_time.strftime(f"%Y-%m-%d ({day}) %H:%M")
                content = f"[{ts}] {content}"

            conv_messages.append(
                Message(
                    role=msg.role,
                    content=content,
                    reasoning_content=msg.reasoning_content,
                    reasoning_details=msg.reasoning_details,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )
            )

        # BP3: cache conversation prefix before current turn
        if cache_ctrl and conv_messages:
            conv_messages = self._inject_conversation_cache_breakpoint(
                conv_messages, cache_ctrl,
            )

        return prefix + conv_messages
