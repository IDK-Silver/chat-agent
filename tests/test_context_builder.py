"""Tests for ContextBuilder core message assembly behavior."""

from datetime import datetime, timezone
from pathlib import Path

from chat_agent.context.builder import ContextBuilder, _TOOL_BOOT_CALL_ID, _TOOL_BOOT_NAME
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import ContentPart, Message


def test_build_with_reminders_appends_to_system_prompt():
    builder = ContextBuilder(system_prompt="Base prompt")
    conv = Conversation()
    conv.add("user", "hello")

    messages = builder.build_with_reminders(conv, ["Check time", "Be concise"])

    system_content = messages[0].content
    assert isinstance(system_content, str)
    assert "Base prompt" in system_content
    assert "## Reminders for This Response" in system_content
    assert "- Check time" in system_content
    assert "- Be concise" in system_content


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
    assert isinstance(system_msg.content, list)
    assert system_msg.content[0].cache_control == {"type": "ephemeral", "ttl": "1h"}

    cache_breakpoint_found = False
    for msg in messages:
        if msg.role in {"user", "assistant"} and isinstance(msg.content, list):
            part = msg.content[0]
            if isinstance(part, ContentPart) and part.cache_control == {"type": "ephemeral", "ttl": "1h"}:
                cache_breakpoint_found = True
                break
    assert cache_breakpoint_found


def test_format_reminder_discord():
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"discord": True, "gmail": True},
    )
    conv = Conversation()
    conv.add("user", "hello", channel="discord", sender="alice")

    messages = builder.build(conv)
    user_msg = [m for m in messages if m.role == "user"][0]
    assert "(multiple messages" in user_msg.content


def test_format_reminder_gmail():
    builder = ContextBuilder(
        system_prompt="sys",
        format_reminders={"discord": True, "gmail": True},
    )
    conv = Conversation()
    conv.add("user", "hello", channel="gmail", sender="bob")

    messages = builder.build(conv)
    user_msg = [m for m in messages if m.role == "user"][0]
    assert "(one send_message = one email" in user_msg.content


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
