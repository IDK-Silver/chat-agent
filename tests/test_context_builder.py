"""Tests for ContextBuilder: build_with_reminders, boot injection, truncation, timestamps."""

from datetime import datetime, timezone
from pathlib import Path

from chat_agent.context.builder import (
    ContextBuilder,
    _TOOL_BOOT_CALL_ID,
    _TOOL_BOOT_NAME,
)
from chat_agent.context.conversation import Conversation
from chat_agent.llm.schema import ContentPart, Message


class TestBuildWithReminders:
    def test_appends_reminders_to_system_prompt(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_reminders(conv, ["Check time", "Be concise"])

        system_content = messages[0].content
        assert "Base prompt" in system_content
        assert "## Reminders for This Response" in system_content
        assert "- Check time" in system_content
        assert "- Be concise" in system_content

    def test_empty_reminders_no_modification(self):
        builder = ContextBuilder(system_prompt="Base prompt")
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_reminders(conv, [])
        system_content = messages[0].content

        assert "Reminders" not in system_content

    def test_no_system_prompt_unchanged(self):
        builder = ContextBuilder(system_prompt=None)
        conv = Conversation()
        conv.add("user", "hello")

        messages = builder.build_with_reminders(conv, ["note"])

        # No system message, so nothing to append to
        assert messages[0].role == "user"

    def test_preserves_conversation_messages(self):
        builder = ContextBuilder(system_prompt="Base")
        conv = Conversation()
        conv.add("user", "msg1")
        conv.add("assistant", "resp1")
        conv.add("user", "msg2")

        messages = builder.build_with_reminders(conv, ["reminder"])

        # system + 3 conversation messages
        assert len(messages) == 4
        assert messages[0].role == "system"


class TestBootFileInjection:
    def test_boot_files_injected_as_system_message(self, tmp_path: Path):
        """Boot files should appear as a [Core Rules] system message."""
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

        # system + runtime(none) + boot + user
        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 1
        assert "I am a bot" in boot_msgs[0].content
        assert '<file path="memory/agent/persona.md">' in boot_msgs[0].content
        assert "</file>" in boot_msgs[0].content

    def test_missing_file_shows_not_found(self, tmp_path: Path):
        """Missing boot files should show [File not found]."""
        builder = ContextBuilder(
            system_prompt="System",
            agent_os_dir=tmp_path,
            boot_files=["memory/agent/nonexistent.md"],
        )
        builder.reload_boot_files()
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 1
        assert "[File not found]" in boot_msgs[0].content

    def test_no_boot_files_no_injection(self):
        """No boot_files => no [Core Rules] message."""
        builder = ContextBuilder(system_prompt="System")
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 0

    def test_no_agent_os_dir_no_injection(self):
        """No agent_os_dir => no [Core Rules] message even with boot_files."""
        builder = ContextBuilder(
            system_prompt="System",
            boot_files=["memory/agent/persona.md"],
        )
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 0

    def test_multiple_boot_files(self, tmp_path: Path):
        """Multiple boot files are combined into a single message."""
        agent_dir = tmp_path / "memory" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "persona.md").write_text("persona content", encoding="utf-8")
        (agent_dir / "recent.md").write_text("short term content", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            agent_os_dir=tmp_path,
            boot_files=[
                "memory/agent/persona.md",
                "memory/agent/recent.md",
            ],
        )
        builder.reload_boot_files()
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 1
        content = boot_msgs[0].content
        assert "persona content" in content
        assert "short term content" in content


class TestContextTruncation:
    def test_no_truncation_under_limit(self):
        """No truncation when total chars < max_chars."""
        builder = ContextBuilder(
            system_prompt="Sys",
            max_chars=100_000,
            preserve_turns=2,
        )
        conv = Conversation()
        conv.add("user", "msg1")
        conv.add("assistant", "resp1")
        conv.add("user", "msg2")
        conv.add("assistant", "resp2")

        messages = builder.build(conv)
        # No truncation notice
        truncation = [m for m in messages if "Context truncated" in (m.content or "")]
        assert len(truncation) == 0
        # All messages present: system + 4 conv
        assert len(messages) == 5

    def test_truncation_drops_old_turns(self):
        """When exceeding max_chars, old turns are dropped, recent ones kept."""
        builder = ContextBuilder(
            system_prompt="S",
            max_chars=100,  # Very small limit to force truncation
            preserve_turns=1,
        )
        conv = Conversation()
        conv.add("user", "A" * 50)
        conv.add("assistant", "B" * 50)
        conv.add("user", "C" * 10)
        conv.add("assistant", "D" * 10)

        messages = builder.build(conv)

        # Should have truncation notice
        truncation = [m for m in messages if "Context truncated" in (m.content or "")]
        assert len(truncation) == 1

        # Only the last turn should remain (user C + assistant D)
        conv_msgs = [m for m in messages if m.role != "system"]
        assert len(conv_msgs) == 2
        assert "C" * 10 in conv_msgs[0].content
        assert "D" * 10 in conv_msgs[1].content

    def test_system_and_boot_never_truncated(self, tmp_path: Path):
        """System prompt and boot context are never truncated."""
        agent_dir = tmp_path / "memory" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "persona.md").write_text("persona data", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System prompt content",
            agent_os_dir=tmp_path,
            boot_files=["memory/agent/persona.md"],
            max_chars=200,  # Small limit
            preserve_turns=1,
        )
        builder.reload_boot_files()
        conv = Conversation()
        conv.add("user", "X" * 100)
        conv.add("assistant", "Y" * 100)
        conv.add("user", "Z" * 10)

        messages = builder.build(conv)

        # System prompt preserved
        assert messages[0].content == "System prompt content"
        # Boot context preserved
        boot_msgs = [m for m in messages if "[Core Rules]" in (m.content or "")]
        assert len(boot_msgs) == 1
        assert "persona data" in boot_msgs[0].content

    def test_turn_includes_tool_messages(self):
        """A turn is user + all subsequent non-user messages (assistant, tool)."""
        builder = ContextBuilder(
            system_prompt="S",
            max_chars=50,
            preserve_turns=1,
        )
        conv = Conversation()
        # Turn 1: user + assistant + tool result
        conv.add("user", "A" * 30)
        conv.add("assistant", "B" * 30)
        conv.add("tool", "tool_result")
        # Turn 2: user + assistant
        conv.add("user", "C")
        conv.add("assistant", "D")

        messages = builder.build(conv)
        conv_msgs = [m for m in messages if m.role != "system"]

        # Only turn 2 remains (user C + assistant D)
        assert len(conv_msgs) == 2
        assert conv_msgs[0].content.endswith("C")  # may have timestamp prefix
        assert conv_msgs[1].content.endswith("D")  # may have timestamp prefix

    def test_preserve_turns_respected(self):
        """Cannot drop below preserve_turns even if still over limit."""
        builder = ContextBuilder(
            system_prompt="S",
            max_chars=10,  # Very small, but preserve_turns=3
            preserve_turns=3,
        )
        conv = Conversation()
        for i in range(3):
            conv.add("user", f"msg{i}")
            conv.add("assistant", f"resp{i}")

        messages = builder.build(conv)
        # 3 turns = preserve_turns, so no truncation occurs
        conv_msgs = [m for m in messages if m.role != "system"]
        assert len(conv_msgs) == 6

    def test_build_with_reminders_also_truncates(self):
        """build_with_reminders should also apply truncation."""
        builder = ContextBuilder(
            system_prompt="S",
            max_chars=100,
            preserve_turns=1,
        )
        conv = Conversation()
        conv.add("user", "A" * 50)
        conv.add("assistant", "B" * 50)
        conv.add("user", "C" * 10)

        messages = builder.build_with_reminders(conv, ["check this"])

        # Should have truncation notice
        truncation = [m for m in messages if "Context truncated" in (m.content or "")]
        assert len(truncation) == 1
        # Reminders still applied to system prompt
        assert "Reminders" in messages[0].content


class TestBuildStats:
    def test_initial_value_is_zero(self):
        builder = ContextBuilder(system_prompt="Sys")
        assert builder.last_total_chars == 0

    def test_last_total_chars_after_build(self):
        builder = ContextBuilder(system_prompt="Hello")
        conv = Conversation()
        conv.add("user", "world")

        messages = builder.build(conv)
        expected = sum(len(m.content or "") for m in messages)
        assert builder.last_total_chars == expected
        assert builder.last_total_chars > 0

    def test_last_total_chars_reflects_truncation(self):
        builder = ContextBuilder(
            system_prompt="S",
            max_chars=100,
            preserve_turns=1,
        )
        conv = Conversation()
        conv.add("user", "A" * 50)
        conv.add("assistant", "B" * 50)
        conv.add("user", "C" * 10)
        conv.add("assistant", "D" * 10)

        messages = builder.build(conv)
        expected = sum(len(m.content or "") for m in messages)
        assert builder.last_total_chars == expected
        # Truncation drops old turns; fewer conversation messages remain
        conv_msgs = [m for m in messages if m.role != "system"]
        assert len(conv_msgs) == 2  # only last turn kept

    def test_build_with_reminders_updates_stats(self):
        builder = ContextBuilder(system_prompt="Base")
        conv = Conversation()
        conv.add("user", "hi")

        builder.build_with_reminders(conv, ["reminder1"])
        # build_with_reminders calls build() internally, which updates stats
        assert builder.last_total_chars > 0


class TestSplitIntoTurns:
    def test_basic_split(self):
        from chat_agent.llm.base import Message

        msgs = [
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="u2"),
            Message(role="assistant", content="a2"),
        ]
        turns = ContextBuilder._split_into_turns(msgs)
        assert len(turns) == 2
        assert turns[0][0].content == "u1"
        assert turns[0][1].content == "a1"
        assert turns[1][0].content == "u2"
        assert turns[1][1].content == "a2"

    def test_tool_messages_grouped_with_turn(self):
        from chat_agent.llm.base import Message

        msgs = [
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
            Message(role="tool", content="t1"),
            Message(role="assistant", content="a1b"),
            Message(role="user", content="u2"),
        ]
        turns = ContextBuilder._split_into_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0]) == 4  # user + assistant + tool + assistant
        assert len(turns[1]) == 1  # user only

    def test_single_message(self):
        from chat_agent.llm.base import Message

        msgs = [Message(role="user", content="only")]
        turns = ContextBuilder._split_into_turns(msgs)
        assert len(turns) == 1
        assert turns[0][0].content == "only"

    def test_empty_list(self):
        turns = ContextBuilder._split_into_turns([])
        assert turns == []


class TestMultimodalCharEstimate:
    def test_multimodal_message_char_estimate(self):
        """Multimodal messages should estimate image token cost."""
        builder = ContextBuilder(system_prompt="S", provider="openai")
        conv = Conversation()
        conv.add("user", "look at this")
        # Inject a multimodal tool result directly
        conv.add_tool_result(
            "tc1", "read_image",
            [
                ContentPart(type="text", text="[Image: test.png (512x512)]"),
                ContentPart(type="image", media_type="image/png", data="x", width=512, height=512),
            ],
        )

        messages = builder.build(conv)
        # Estimate should include image cost, not just text length
        # Image at 512x512 for openai: (170*1*1 + 85)*4 = 1020
        assert builder.last_total_chars > 1000

    def test_truncated_flag_set(self):
        """last_was_truncated should be set when context is truncated."""
        builder = ContextBuilder(
            system_prompt="S",
            max_chars=50,
            preserve_turns=1,
        )
        conv = Conversation()
        conv.add("user", "A" * 30)
        conv.add("assistant", "B" * 30)
        conv.add("user", "C")
        conv.add("assistant", "D")

        builder.build(conv)
        assert builder.last_was_truncated is True

    def test_no_truncation_flag_false(self):
        """last_was_truncated should be False when no truncation."""
        builder = ContextBuilder(system_prompt="S", max_chars=100_000)
        conv = Conversation()
        conv.add("user", "hello")
        builder.build(conv)
        assert builder.last_was_truncated is False


class TestTimestampPrefixes:
    """Tests for timestamp prefix injection on user/assistant messages."""

    def test_assistant_message_gets_timestamp_prefix(self):
        """Assistant messages with timestamps should get [YYYY-MM-DD HH:MM] prefix."""
        builder = ContextBuilder(system_prompt="S")
        conv = Conversation()
        ts = datetime(2026, 2, 19, 14, 30, tzinfo=timezone.utc)
        conv.add("user", "hello", timestamp=ts)
        conv.add("assistant", "hi there", timestamp=ts)
        conv.add("user", "bye", timestamp=ts)

        messages = builder.build(conv)
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0].content.startswith("[")
        assert "] hi there" in assistant_msgs[0].content

    def test_last_user_message_gets_now_marker(self):
        """The last user message should have 'now' marker with current time."""
        builder = ContextBuilder(system_prompt="S")
        conv = Conversation()
        ts = datetime(2026, 2, 19, 14, 30, tzinfo=timezone.utc)
        conv.add("user", "hello", timestamp=ts)
        conv.add("assistant", "hi", timestamp=ts)
        conv.add("user", "what time", timestamp=ts)

        messages = builder.build(conv)
        user_msgs = [m for m in messages if m.role == "user"]
        last_user = user_msgs[-1]
        assert ", now " in last_user.content
        assert "what time" in last_user.content

    def test_tool_message_no_timestamp_prefix(self):
        """Tool messages should not get timestamp prefixes."""
        builder = ContextBuilder(system_prompt="S")
        conv = Conversation()
        ts = datetime(2026, 2, 19, 14, 30, tzinfo=timezone.utc)
        conv.add("user", "hello", timestamp=ts)
        conv.add_tool_result("tc1", "some_tool", "tool output")
        conv.add("user", "next", timestamp=ts)

        messages = builder.build(conv)
        tool_msgs = [m for m in messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "tool output"

    def test_non_last_user_message_no_now_marker(self):
        """Non-last user messages should have plain timestamp, no 'now' marker."""
        builder = ContextBuilder(system_prompt="S")
        conv = Conversation()
        ts = datetime(2026, 2, 19, 14, 30, tzinfo=timezone.utc)
        conv.add("user", "first msg", timestamp=ts)
        conv.add("assistant", "reply", timestamp=ts)
        conv.add("user", "second msg", timestamp=ts)

        messages = builder.build(conv)
        user_msgs = [m for m in messages if m.role == "user"]
        first_user = user_msgs[0]
        # Should have timestamp but NOT "now"
        assert first_user.content.startswith("[")
        assert "first msg" in first_user.content
        assert ", now " not in first_user.content


class TestToolBootInjection:
    """Tests for tool-tier boot file injection as synthetic tool-call/result pairs."""

    def test_tool_boot_files_injected_as_tool_pair(self, tmp_path: Path):
        """Tool boot files should appear as assistant+tool message pair."""
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

        assistant_with_tool = [
            m for m in messages if m.role == "assistant" and m.tool_calls
        ]
        assert len(assistant_with_tool) == 1
        tc = assistant_with_tool[0].tool_calls[0]
        assert tc.name == _TOOL_BOOT_NAME
        assert tc.id == _TOOL_BOOT_CALL_ID

        tool_results = [
            m for m in messages if m.role == "tool" and m.name == _TOOL_BOOT_NAME
        ]
        assert len(tool_results) == 1
        assert "current mood" in tool_results[0].content
        assert tool_results[0].tool_call_id == _TOOL_BOOT_CALL_ID

    def test_no_tool_boot_files_no_injection(self):
        """No boot_files_as_tool => no tool pair."""
        builder = ContextBuilder(system_prompt="System")
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        tool_msgs = [m for m in messages if m.role == "tool"]
        assert len(tool_msgs) == 0

    def test_both_boot_tiers_present(self, tmp_path: Path):
        """Both system and tool tier boot files should be present."""
        memory_dir = tmp_path / "memory" / "agent"
        memory_dir.mkdir(parents=True)
        (memory_dir / "persona.md").write_text("I am a bot", encoding="utf-8")
        (memory_dir / "recent.md").write_text("feeling ok", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            agent_os_dir=tmp_path,
            boot_files=["memory/agent/persona.md"],
            boot_files_as_tool=["memory/agent/recent.md"],
        )
        builder.reload_boot_files()
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)

        core_msgs = [
            m for m in messages
            if m.role == "system" and "[Core Rules]" in (m.content or "")
        ]
        assert len(core_msgs) == 1
        assert "I am a bot" in core_msgs[0].content

        tool_msgs = [
            m for m in messages
            if m.role == "tool" and m.name == _TOOL_BOOT_NAME
        ]
        assert len(tool_msgs) == 1
        assert "feeling ok" in tool_msgs[0].content

    def test_tool_boot_not_subject_to_truncation(self, tmp_path: Path):
        """Tool boot messages are in prefix, not subject to truncation."""
        memory_dir = tmp_path / "memory" / "agent"
        memory_dir.mkdir(parents=True)
        (memory_dir / "state.md").write_text("important state", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="S",
            agent_os_dir=tmp_path,
            boot_files_as_tool=["memory/agent/state.md"],
            max_chars=200,
            preserve_turns=1,
        )
        builder.reload_boot_files()
        conv = Conversation()
        conv.add("user", "X" * 100)
        conv.add("assistant", "Y" * 100)
        conv.add("user", "Z" * 10)

        messages = builder.build(conv)

        tool_msgs = [
            m for m in messages
            if m.role == "tool" and m.name == _TOOL_BOOT_NAME
        ]
        assert len(tool_msgs) == 1
        assert "important state" in tool_msgs[0].content

    def test_tool_boot_reload_picks_up_changes(self, tmp_path: Path):
        """Reload should pick up tool boot file changes."""
        memory_dir = tmp_path / "memory" / "agent"
        memory_dir.mkdir(parents=True)
        (memory_dir / "state.md").write_text("v1", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="S",
            agent_os_dir=tmp_path,
            boot_files_as_tool=["memory/agent/state.md"],
        )
        builder.reload_boot_files()

        (memory_dir / "state.md").write_text("v2", encoding="utf-8")
        builder.reload_boot_files()

        conv = Conversation()
        conv.add("user", "hi")
        messages = builder.build(conv)

        tool_msgs = [m for m in messages if m.role == "tool"]
        assert "v2" in tool_msgs[0].content
