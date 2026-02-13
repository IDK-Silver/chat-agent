"""Tests for ContextBuilder: build_with_reminders, boot injection, truncation."""

from pathlib import Path

from chat_agent.context.builder import ContextBuilder
from chat_agent.context.conversation import Conversation


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
        """Boot files should appear as a [Boot Context] system message."""
        memory_dir = tmp_path / "memory" / "agent"
        memory_dir.mkdir(parents=True)
        (memory_dir / "persona.md").write_text("I am a bot", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            working_dir=tmp_path,
            boot_files=["memory/agent/persona.md"],
        )
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)

        # system + runtime(none) + boot + user
        boot_msgs = [m for m in messages if m.role == "system" and "[Boot Context]" in (m.content or "")]
        assert len(boot_msgs) == 1
        assert "I am a bot" in boot_msgs[0].content
        assert '<file path="memory/agent/persona.md">' in boot_msgs[0].content
        assert "</file>" in boot_msgs[0].content

    def test_missing_file_shows_not_found(self, tmp_path: Path):
        """Missing boot files should show [File not found]."""
        builder = ContextBuilder(
            system_prompt="System",
            working_dir=tmp_path,
            boot_files=["memory/agent/nonexistent.md"],
        )
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Boot Context]" in (m.content or "")]
        assert len(boot_msgs) == 1
        assert "[File not found]" in boot_msgs[0].content

    def test_current_user_placeholder_resolved(self, tmp_path: Path):
        """The {current_user} placeholder should be resolved in boot file paths."""
        people_dir = tmp_path / "memory" / "people"
        people_dir.mkdir(parents=True)
        (people_dir / "user-alice.md").write_text("Alice info", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            current_user="alice",
            working_dir=tmp_path,
            boot_files=["memory/people/user-{current_user}.md"],
        )
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Boot Context]" in (m.content or "")]
        assert len(boot_msgs) == 1
        assert "Alice info" in boot_msgs[0].content
        # Path in tag should be resolved
        assert '<file path="memory/people/user-alice.md">' in boot_msgs[0].content

    def test_no_boot_files_no_injection(self):
        """No boot_files => no [Boot Context] message."""
        builder = ContextBuilder(system_prompt="System")
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Boot Context]" in (m.content or "")]
        assert len(boot_msgs) == 0

    def test_no_working_dir_no_injection(self):
        """No working_dir => no [Boot Context] message even with boot_files."""
        builder = ContextBuilder(
            system_prompt="System",
            boot_files=["memory/agent/persona.md"],
        )
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Boot Context]" in (m.content or "")]
        assert len(boot_msgs) == 0

    def test_multiple_boot_files(self, tmp_path: Path):
        """Multiple boot files are combined into a single message."""
        agent_dir = tmp_path / "memory" / "agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "persona.md").write_text("persona content", encoding="utf-8")
        (agent_dir / "short-term.md").write_text("short term content", encoding="utf-8")

        builder = ContextBuilder(
            system_prompt="System",
            working_dir=tmp_path,
            boot_files=[
                "memory/agent/persona.md",
                "memory/agent/short-term.md",
            ],
        )
        conv = Conversation()
        conv.add("user", "hi")

        messages = builder.build(conv)
        boot_msgs = [m for m in messages if m.role == "system" and "[Boot Context]" in (m.content or "")]
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
            working_dir=tmp_path,
            boot_files=["memory/agent/persona.md"],
            max_chars=200,  # Small limit
            preserve_turns=1,
        )
        conv = Conversation()
        conv.add("user", "X" * 100)
        conv.add("assistant", "Y" * 100)
        conv.add("user", "Z" * 10)

        messages = builder.build(conv)

        # System prompt preserved
        assert messages[0].content == "System prompt content"
        # Boot context preserved
        boot_msgs = [m for m in messages if "[Boot Context]" in (m.content or "")]
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
        assert conv_msgs[1].content == "D"

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
