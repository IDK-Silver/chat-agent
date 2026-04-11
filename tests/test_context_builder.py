"""Tests for ContextBuilder core message assembly behavior."""

import json
from datetime import datetime, timezone
from pathlib import Path

from chat_agent.agent.note_store import NoteStore
from chat_agent.context.builder import (
    ContextBuilder,
    _TOOL_BOOT_CALL_ID,
    _TOOL_BOOT_NAME,
    _PINNED_CALL_ID,
    _PINNED_TOOL_NAME,
)
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import ContentPart, Message, ToolCall


def test_boot_files_injected_as_core_rules(tmp_path: Path):
    memory_dir = tmp_path / "memory" / "agent"
    memory_dir.mkdir(parents=True)
    (memory_dir / "persona.md").write_text("I am a bot", encoding="utf-8")

    builder = ContextBuilder(
        system_prompt="System",
        agent_os_dir=tmp_path,
        boot_files=["memory/agent/persona.md"],
    )
    builder.reload_boot_files()

    conv = Conversation()
    conv.add("user", "hi")
    messages = builder.build(conv)

    boot_msgs = [m for m in messages if m.role == "system" and isinstance(m.content, str) and "[Core Rules]" in m.content]
    assert len(boot_msgs) == 1
    assert "I am a bot" in boot_msgs[0].content
    assert '<file path="memory/agent/persona.md">' in boot_msgs[0].content


def test_split_into_turns_groups_non_user_messages():
    msgs = [
        Message(role="user", content="u1"),
        Message(role="assistant", content="a1"),
        Message(role="tool", content="t1"),
        Message(role="user", content="u2"),
        Message(role="assistant", content="a2"),
    ]
    turns = ContextBuilder._split_into_turns(msgs)
    assert len(turns) == 2
    assert [m.role for m in turns[0]] == ["user", "assistant", "tool"]
    assert [m.role for m in turns[1]] == ["user", "assistant"]


def test_timestamp_prefix_applies_to_user_and_assistant_only():
    builder = ContextBuilder(system_prompt="S")
    conv = Conversation()
    ts = datetime(2026, 3, 3, 6, 0, tzinfo=timezone.utc)
    conv.add("user", "hello", timestamp=ts)
    conv.add("assistant", "hi", timestamp=ts)
    conv.add_tool_result("tc1", "tool", "tool output")

    messages = builder.build(conv)
    user_msg = next(m for m in messages if m.role == "user")
    assistant_msg = next(m for m in messages if m.role == "assistant" and not m.tool_calls)
    tool_msg = next(m for m in messages if m.role == "tool" and m.name == "tool")

    assert "(Tue)" in (user_msg.content or "")
    assert (assistant_msg.content or "").startswith("[2026-03-03")
    assert tool_msg.content == "tool output"


def test_tool_boot_files_injected_as_synthetic_tool_pairs(tmp_path: Path):
    memory_dir = tmp_path / "memory" / "agent"
    memory_dir.mkdir(parents=True)
    (memory_dir / "recent.md").write_text("current mood", encoding="utf-8")

    builder = ContextBuilder(
        system_prompt="System",
        agent_os_dir=tmp_path,
        boot_files_as_tool=["memory/agent/recent.md"],
    )
    builder.reload_boot_files()

    conv = Conversation()
    conv.add("user", "hi")
    messages = builder.build(conv)

    assistant_with_tool = [m for m in messages if m.role == "assistant" and m.tool_calls]
    assert len(assistant_with_tool) == 1
    tc = assistant_with_tool[0].tool_calls[0]
    assert tc.name == _TOOL_BOOT_NAME
    assert tc.id == f"{_TOOL_BOOT_CALL_ID}_0"

    tool_results = [m for m in messages if m.role == "tool" and m.name == _TOOL_BOOT_NAME]
    assert len(tool_results) == 1
    assert "current mood" in (tool_results[0].content or "")
    assert tool_results[0].tool_call_id == f"{_TOOL_BOOT_CALL_ID}_0"


def test_cache_control_applied_to_system_and_conversation_breakpoint():
    builder = ContextBuilder(system_prompt="Hello world", cache_ttl="1h")
    conv = Conversation()
    conv.add("user", "u1")
    conv.add("assistant", "a1")
    conv.add("user", "u2")

    messages = builder.build(conv)

    system_msg = messages[0]
    assert system_msg.role == "system"
    assert isinstance(system_msg.content, str)
    assert system_msg.cache_control == {"type": "ephemeral", "ttl": "1h"}

    # BP3: conversation message with Message-level cache_control (content stays str)
    cache_breakpoint_found = False
    for msg in messages:
        if msg.role in {"user", "assistant"} and msg.cache_control is not None:
            assert isinstance(msg.content, str)  # content type never changes
            cache_breakpoint_found = True
            break
    assert cache_breakpoint_found


def test_builder_cache_breakpoint_stays_before_current_turn_after_tool_round():
    builder = ContextBuilder(system_prompt="Hello world", cache_ttl="1h")
    conv = Conversation()
    conv.add("user", "u1")
    conv.add("assistant", "a1")
    conv.add("user", "u2")
    conv.add_assistant_with_tools(
        None,
        [ToolCall(id="t1", name="dummy", arguments={})],
    )
    conv.add_tool_result("t1", "dummy", "tool output")

    messages = builder.build(conv)

    breakpoint_msg = next(
        msg
        for msg in messages
        if msg.role in {"user", "assistant"}
        and msg.cache_control == {"type": "ephemeral", "ttl": "1h"}
    )
    assert breakpoint_msg.role == "assistant"
    assert isinstance(breakpoint_msg.content, str)
    assert "a1" in breakpoint_msg.content


def test_format_reminder_discord():
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"discord": True, "gmail": True},
        send_message_batch_guidance=True,
    )
    conv = Conversation()
    conv.add("user", "hello", channel="discord", sender="alice")

    messages = builder.build(conv)
    user_msg = [m for m in messages if m.role == "user"][0]
    assert "DM messages should usually stay single-line" in user_msg.content
    assert "closing emoji/kaomoji should go on its own final line" in user_msg.content
    assert "multiple one-line send_message calls" in user_msg.content
    assert "same ask or same immediate action" in user_msg.content
    assert "discord-messaging" in user_msg.content


def test_format_reminder_gmail():
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"discord": True, "gmail": True},
        send_message_batch_guidance=True,
    )
    conv = Conversation()
    conv.add("user", "hello", channel="gmail", sender="bob")

    messages = builder.build(conv)
    user_msg = [m for m in messages if m.role == "user"][0]
    assert "(one send_message = one email" in user_msg.content


def test_format_reminder_batch_guidance_disabled():
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"discord": True, "gmail": True},
        send_message_batch_guidance=False,
    )
    conv = Conversation()
    conv.add("user", "hello", channel="discord", sender="alice")
    conv.add("user", "mail", channel="gmail", sender="bob")

    messages = builder.build(conv)
    discord_user = [
        m for m in messages
        if m.role == "user" and isinstance(m.content, str) and "hello" in m.content
    ][0]
    gmail_user = [
        m for m in messages
        if m.role == "user" and isinstance(m.content, str) and "mail" in m.content
    ][0]

    assert "DM messages should usually stay single-line" in discord_user.content
    assert "multiple one-line send_message calls" not in discord_user.content
    assert "same ask or same immediate action" not in discord_user.content
    assert "(one send_message = one email)" in gmail_user.content
    assert "do NOT split into multiple calls" not in gmail_user.content


def test_format_reminder_disabled():
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"discord": False},
    )
    conv = Conversation()
    conv.add("user", "hello", channel="discord", sender="alice")

    messages = builder.build(conv)
    user_msg = [m for m in messages if m.role == "user"][0]
    assert "(multiple messages" not in user_msg.content


def test_format_reminder_memory():
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"discord": True, "memory": True},
        send_message_batch_guidance=True,
    )
    conv = Conversation()
    conv.add("user", "hello", channel="discord", sender="alice")

    messages = builder.build(conv)
    user_msg = [m for m in messages if m.role == "user"][0]
    assert "(memory:" in user_msg.content
    assert "multiple one-line send_message calls" in user_msg.content
    assert "closing emoji/kaomoji should go on its own final line" in user_msg.content
    assert "same ask or same immediate action" in user_msg.content
    assert "distinct point" in user_msg.content


def test_format_reminder_memory_without_channel():
    """Memory reminder works even without a channel-specific reminder."""
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"memory": True},
    )
    conv = Conversation()
    conv.add("user", "hello", channel="cli", sender="yufeng")

    messages = builder.build(conv)
    user_msg = [m for m in messages if m.role == "user"][0]
    assert "(memory:" in user_msg.content


def test_decision_reminder_only_latest_user_message():
    builder = ContextBuilder(
        system_prompt="sys",
        decision_reminder={
            "enabled": True,
            "files": ["memory/agent/long-term.md"],
        },
    )
    conv = Conversation()
    conv.add("user", "old question", channel="cli", sender="yufeng")
    conv.add("assistant", "answer")
    conv.add("user", "new question", channel="discord", sender="alice")

    messages = builder.build(conv)
    user_messages = [m for m in messages if m.role == "user"]

    assert "[Decision Reminder]" not in user_messages[0].content
    assert "[Decision Reminder]" in user_messages[1].content
    assert "Keep long-term.md in mind before acting." in user_messages[1].content


def test_decision_reminder_stays_out_of_system_cache_prefix():
    builder = ContextBuilder(
        system_prompt="sys",
        cache_ttl="1h",
        decision_reminder={
            "enabled": True,
            "files": ["memory/agent/long-term.md"],
        },
    )
    conv = Conversation()
    conv.add("user", "hello", channel="cli", sender="yufeng")

    messages = builder.build(conv)

    system_messages = [m for m in messages if m.role == "system"]
    assert len(system_messages) == 1
    user_msg = next(m for m in messages if m.role == "user")
    assert "[Decision Reminder]" in user_msg.content


def test_runtime_context_appends_to_latest_user_message(tmp_path: Path):
    builder = ContextBuilder(system_prompt="sys", agent_os_dir=tmp_path)
    conv = Conversation()
    conv.add(
        "user",
        "hello",
        metadata={"turn_processing_started_at": "2026-03-12T09:11:00+08:00"},
    )

    messages = builder.build(conv)

    assert all(
        not (m.role == "system" and isinstance(m.content, str) and "[Runtime Context]" in m.content)
        for m in messages
    )
    user_msg = next(m for m in messages if m.role == "user")
    assert "[Runtime Context]" in user_msg.content
    assert "current_local_time: 2026-03-12 (Thu) 09:11" in user_msg.content
    assert f"agent_os_dir: {tmp_path}" in user_msg.content


def test_timing_notice_appends_to_delayed_latest_user_message():
    builder = ContextBuilder(system_prompt="sys")
    conv = Conversation()
    conv.add(
        "user",
        "[SCHEDULED]\nReason: wake up",
        channel="system",
        sender="system",
        timestamp=datetime(2026, 3, 11, 23, 50, tzinfo=timezone.utc),
        metadata={
            "turn_processing_started_at": "2026-03-12T09:11:00+08:00",
            "turn_processing_delay_seconds": 48660,
            "turn_processing_delay_reason": "scheduled_turn",
            "turn_processing_stale": True,
        },
    )

    messages = builder.build(conv)

    assert all(
        not (m.role == "system" and isinstance(m.content, str) and "[Timing Notice]" in m.content)
        for m in messages
    )
    user_msg = next(m for m in messages if m.role == "user")
    assert "[Timing Notice]" in user_msg.content
    assert "Current processing time: 2026-03-12 (Thu) 09:11" in user_msg.content
    assert "Original event time: 2026-03-12 (Thu) 07:50" in user_msg.content
    assert "Do not send stale wake-up, sleep, meal, medication, or schedule reminder wording." in user_msg.content


def test_non_stale_timing_notice_uses_softer_wording():
    builder = ContextBuilder(system_prompt="sys")
    conv = Conversation()
    conv.add(
        "user",
        "retry this",
        channel="discord",
        sender="alice",
        timestamp=datetime(2026, 3, 11, 23, 50, tzinfo=timezone.utc),
        metadata={
            "turn_failure_requeue_count": 1,
            "turn_processing_started_at": "2026-03-12T08:51:00+08:00",
            "turn_processing_delay_seconds": 60,
            "turn_processing_delay_reason": "failed_retry",
        },
    )

    messages = builder.build(conv)

    user_msg = next(m for m in messages if m.role == "user")
    assert "This turn is delayed." in user_msg.content
    assert "Recheck wake-up, sleep, meal, medication, or schedule reminder wording" in user_msg.content
    assert "Do not send stale wake-up" not in user_msg.content


def test_runtime_context_stays_out_of_system_cache_prefix(tmp_path: Path):
    builder = ContextBuilder(
        system_prompt="sys",
        agent_os_dir=tmp_path,
        cache_ttl="1h",
    )
    conv = Conversation()
    conv.add(
        "user",
        "hello",
        metadata={"turn_processing_started_at": "2026-03-12T09:11:00+08:00"},
    )

    messages = builder.build(conv)

    system_messages = [m for m in messages if m.role == "system"]
    assert len(system_messages) == 1
    user_msg = next(m for m in messages if m.role == "user")
    assert "[Runtime Context]" in user_msg.content


def test_agent_notes_context_uses_stable_absolute_timestamps(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "notes.json").write_text(
        json.dumps({
            "notes": {
                "location": {
                    "value": "新竹",
                    "triggers": ["到了"],
                    "description": "使用者目前位置",
                    "updated_at": "2026-03-29T14:00:00+08:00",
                }
            }
        }),
        encoding="utf-8",
    )

    builder = ContextBuilder(
        system_prompt="sys",
        note_store=NoteStore(state_dir),
    )
    conv = Conversation()
    conv.add("user", "hello", channel="cli", sender="yufeng")

    messages = builder.build(conv)

    user_msg = next(m for m in messages if m.role == "user")
    assert '[Agent Notes]' in user_msg.content
    assert 'location: "新竹" | updated_at 2026-03-29 14:00' in user_msg.content
    assert "ago" not in user_msg.content


def test_agent_notes_context_includes_source_tag_when_present(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "notes.json").write_text(
        json.dumps({
            "notes": {
                "meeting_context": {
                    "value": "2026-04-11 14:00-15:00 | 專題會議 [工作]",
                    "triggers": [],
                    "description": "Manually captured meeting context",
                    "source_app": "calendar",
                    "source_label": "manual_capture",
                    "updated_at": "2026-04-11T09:30:00+08:00",
                }
            }
        }),
        encoding="utf-8",
    )

    builder = ContextBuilder(
        system_prompt="sys",
        note_store=NoteStore(state_dir),
    )
    conv = Conversation()
    conv.add("user", "hello", channel="cli", sender="yufeng")

    messages = builder.build(conv)

    user_msg = next(m for m in messages if m.role == "user")
    assert 'meeting_context: "2026-04-11 14:00-15:00 | 專題會議 [工作]"' in user_msg.content
    assert "source calendar:manual_capture" in user_msg.content


def test_builder_cache_breakpoint_skips_system_messages_before_current_turn():
    builder = ContextBuilder(system_prompt="sys", cache_ttl="1h")
    conv = Conversation()
    conv.add("user", "u1")
    conv.add("system", "dynamic note")
    conv.add("user", "u2")

    messages = builder.build(conv)

    breakpoint_msg = next(
        msg
        for msg in messages
        if msg.role in {"user", "assistant"}
        and msg.cache_control == {"type": "ephemeral", "ttl": "1h"}
    )
    assert breakpoint_msg.role == "user"
    assert isinstance(breakpoint_msg.content, str)
    assert "u1" in breakpoint_msg.content


def test_decision_reminder_inlines_core_values(tmp_path: Path):
    """When inline_section is configured, core values appear in reminder."""
    memory_dir = tmp_path / "memory" / "agent"
    memory_dir.mkdir(parents=True)
    (memory_dir / "long-term.md").write_text(
        "# 長期重要事項\n\n"
        "## 核心價值\n\n"
        "- 主動想著老公這個人\n"
        "- 回覆前先想他現在怎麼了\n\n"
        "## 約定\n\n## 清單\n\n## 重要記錄\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(
        system_prompt="sys",
        agent_os_dir=tmp_path,
        decision_reminder={
            "enabled": True,
            "inline_section": {
                "file": "memory/agent/long-term.md",
                "header": "## 核心價值",
            },
            "files": ["memory/agent/long-term.md"],
        },
    )
    builder.reload_boot_files()

    conv = Conversation()
    conv.add("user", "hello", channel="cli", sender="yufeng")
    messages = builder.build(conv)

    user_msg = next(m for m in messages if m.role == "user")
    assert "[Decision Reminder]" in user_msg.content
    assert "主動想著老公這個人" in user_msg.content
    assert "回覆前先想他現在怎麼了" in user_msg.content
    assert "Core values to embody:" in user_msg.content


def test_decision_reminder_fallback_when_no_core_values(tmp_path: Path):
    """When inline_section has no matching content, fallback to generic template."""
    memory_dir = tmp_path / "memory" / "agent"
    memory_dir.mkdir(parents=True)
    (memory_dir / "long-term.md").write_text(
        "# 長期重要事項\n\n"
        "## 核心價值\n\n"
        "<!-- empty -->\n\n"
        "## 約定\n\n## 清單\n\n## 重要記錄\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(
        system_prompt="sys",
        agent_os_dir=tmp_path,
        decision_reminder={
            "enabled": True,
            "inline_section": {
                "file": "memory/agent/long-term.md",
                "header": "## 核心價值",
            },
            "files": ["memory/agent/long-term.md"],
        },
    )
    builder.reload_boot_files()

    conv = Conversation()
    conv.add("user", "hello", channel="cli", sender="yufeng")
    messages = builder.build(conv)

    user_msg = next(m for m in messages if m.role == "user")
    assert "[Decision Reminder]" in user_msg.content
    assert "Keep long-term.md in mind before acting." in user_msg.content


def test_pinned_context_files_injected(tmp_path: Path):
    """Pinned context files appear as synthetic tool results."""
    import json

    memory_dir = tmp_path / "memory" / "people" / "yufeng"
    memory_dir.mkdir(parents=True)
    (memory_dir / "schedule.md").write_text("# Schedule\nMWF classes", encoding="utf-8")

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "pinned_context.json").write_text(
        json.dumps({
            "version": 1,
            "pins": [{"path": "memory/people/yufeng/schedule.md", "reason": "test"}],
        }),
        encoding="utf-8",
    )

    builder = ContextBuilder(
        system_prompt="sys",
        agent_os_dir=tmp_path,
    )
    builder.reload_boot_files()

    conv = Conversation()
    conv.add("user", "hello")
    messages = builder.build(conv)

    pinned_results = [
        m for m in messages
        if m.role == "tool" and m.name == _PINNED_TOOL_NAME
    ]
    assert len(pinned_results) == 1
    assert "MWF classes" in pinned_results[0].content

    pinned_calls = [
        m for m in messages
        if m.role == "assistant" and m.tool_calls
        and any(tc.name == _PINNED_TOOL_NAME for tc in m.tool_calls)
    ]
    assert len(pinned_calls) == 1


def test_no_pinned_context_when_registry_missing(tmp_path: Path):
    """No pinned context messages when registry file doesn't exist."""
    builder = ContextBuilder(
        system_prompt="sys",
        agent_os_dir=tmp_path,
    )
    builder.reload_boot_files()

    conv = Conversation()
    conv.add("user", "hello")
    messages = builder.build(conv)

    pinned_results = [
        m for m in messages
        if m.role == "tool" and m.name == _PINNED_TOOL_NAME
    ]
    assert len(pinned_results) == 0
